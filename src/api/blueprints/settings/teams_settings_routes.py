# SPDX-License-Identifier: AGPL-3.0-or-later
"""Custom Teams settings endpoints layered on top of the generic settings API.

Reasons for a dedicated blueprint:
  - Read-time redaction of bot_app_password (mirrors /api/settings/email).
  - Computed messaging endpoint URL (later task).
  - Forwarded "test connection" probe (later task).
  - Manifest zip download (later task).
"""
from __future__ import annotations

import logging
import os

import requests as _requests
from flask import Blueprint, Response as _FlaskResponse, current_app, request
from flask_jwt_extended import jwt_required

from src.api.middleware.error_handler import handle_errors, ValidationError
from src.api.utils.public_base_url import resolve_public_base_url
from src.api.utils.responses import response_wrapper
from src.api.utils.teams_manifest import (
    ManifestBuildError,
    build_teams_manifest_zip,
)
from src.auth.middleware.rbac import require_admin
from src.modules.integrations.shared.encryption import decrypt, is_encrypted

logger = logging.getLogger(__name__)

teams_settings_bp = Blueprint(
    'teams_settings', __name__, url_prefix='/api/settings/teams'
)

_PASSWORD_SENTINEL = '__SET__'


@teams_settings_bp.route('', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_teams_settings():
    """Return Teams settings with the bot_app_password redacted.

    - Stored ENC:... ciphertext  -> '__SET__'
    - Empty string               -> '' (untouched)
    """
    settings_manager = current_app.settings_manager
    cat = settings_manager.get_all(category='teams').get('teams', {})

    out = {}
    for key, entry in cat.items():
        value = entry.get('value') if isinstance(entry, dict) else entry
        if key == 'bot_app_password':
            value = _PASSWORD_SENTINEL if (
                isinstance(value, str) and is_encrypted(value)
            ) else ''
        out[key] = value
    return out


@teams_settings_bp.route('/messaging-endpoint', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_messaging_endpoint():
    """Return the URL the operator pastes into Azure Bot's Configuration.

    Resolution order:
      1. The operator-supplied teams.messaging_endpoint override, if non-empty.
      2. ${security.public_base_url}/teams/api/messages computed default
         (falling back to the ASSETATLAS_PUBLIC_BASE_URL env var when the
         setting is empty).

    Returns: {url, https_ok, is_override}.
    """
    sm = current_app.settings_manager
    override = (sm.get('teams', 'messaging_endpoint') or '').strip()
    if override:
        return {
            'url': override,
            'https_ok': override.lower().startswith('https://'),
            'is_override': True,
        }
    base = resolve_public_base_url(sm)
    if not base:
        raise ValidationError(
            'security.public_base_url is not configured; the messaging '
            'endpoint cannot be derived. Set it in System → Security or '
            'set the ASSETATLAS_PUBLIC_BASE_URL env var.'
        )
    return {
        'url': f"{base}/teams/api/messages",
        'https_ok': base.lower().startswith('https://'),
        'is_override': False,
    }


def _post_to_bridge(payload: dict) -> dict:
    """POST to the bridge's /test-credentials endpoint and return its JSON.

    Default URL assumes Docker-Compose service-name resolution
    (http://teams-bridge:3978). Override with TEAMS_BRIDGE_URL for non-compose
    setups.
    """
    bridge_url = os.environ.get(
        'TEAMS_BRIDGE_URL', 'http://teams-bridge:3978',
    )
    resp = _requests.post(
        f"{bridge_url.rstrip('/')}/test-credentials",
        json=payload, timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


@teams_settings_bp.route('/test-connection', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def test_connection():
    """Probe Bot Framework auth via the bridge.

    Body shape (all optional; missing fields fall back to the saved values):
      {bot_app_id, bot_app_password, bot_app_tenant_id}

    Returns the bridge's verdict: {'ok': true} or {'ok': false, 'error': ...}.
    Always HTTP 200 — the UI distinguishes outcomes by body['ok'].
    """
    sm = current_app.settings_manager
    body = request.get_json(silent=True) or {}
    saved_password_raw = sm.get('teams', 'bot_app_password') or ''
    if isinstance(saved_password_raw, str) and is_encrypted(saved_password_raw):
        saved_password = decrypt(saved_password_raw)
    else:
        saved_password = saved_password_raw

    payload = {
        'bot_app_id': body.get('bot_app_id') or sm.get('teams', 'bot_app_id'),
        'bot_app_password': body.get('bot_app_password') or saved_password,
        'bot_app_tenant_id': (
            body.get('bot_app_tenant_id')
            or sm.get('teams', 'bot_app_tenant_id')
        ),
    }
    try:
        return _post_to_bridge(payload)
    except _requests.ConnectionError as exc:
        logger.warning("test-connection: bridge unreachable: %s", exc)
        return {'ok': False, 'error': 'bridge unreachable'}, 200
    except _requests.RequestException as exc:
        logger.warning("test-connection: bridge error: %s", exc)
        return {'ok': False, 'error': 'bridge error'}, 200


@teams_settings_bp.route('/manifest.zip', methods=['GET'])
@jwt_required()
@require_admin()
def manifest_zip():
    """Return the sideload-ready Teams app zip.

    Bypasses @response_wrapper because the body is binary, not JSON.
    Errors come back as plain-text 400s rather than the JSON error
    envelope so the UI's <a href download> can surface the message
    directly via window.fetch + response.text().
    """
    sm = current_app.settings_manager
    bot_app_id = (sm.get('teams', 'bot_app_id') or '').strip()
    public_url = resolve_public_base_url(sm)

    try:
        data = build_teams_manifest_zip(
            bot_app_id=bot_app_id, public_base_url=public_url,
        )
    except ManifestBuildError as exc:
        return _FlaskResponse(
            response=str(exc), status=400, content_type='text/plain',
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("manifest.zip build failed unexpectedly")
        return _FlaskResponse(
            response=f"Internal error building manifest zip: {exc.__class__.__name__}",
            status=500, content_type='text/plain',
        )

    return _FlaskResponse(
        response=data,
        status=200,
        content_type='application/zip',
        headers={
            'Content-Disposition':
                'attachment; filename="assetatlas-teams.zip"',
        },
    )
