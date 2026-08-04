# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Preview Blueprint — Server-side caching for wizard preview data.

Endpoints:
- POST /preview/fetch: Fetch all source records, cache in Redis, return sample + total
- POST /preview/filter-count: Evaluate filters against cached records, return match count
- DELETE /preview/<session_id>: Delete cached preview data
"""

import json
import logging
import uuid

import requests as _requests
from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required

from src.api.middleware.error_handler import handle_errors, ValidationError
from src.auth.middleware.rbac import require_admin
from src.modules.integrations.connectors.custom_http_connector import (
    CustomHttpConnector,
)
from src.modules.integrations.services.integration_service import (
    IntegrationService,
)
from src.modules.integrations.shared.json_path import (
    extract_many,
    validate as validate_jsonpath,
)
from src.modules.integrations.shared import data_filters

logger = logging.getLogger(__name__)

preview_bp = Blueprint('preview', __name__)

CACHE_KEY_PREFIX = 'wizard_preview:'
CACHE_TTL = 86400  # 24 hours
PREVIEW_SAMPLE_LIMIT = 1000


def _get_redis_client():
    """Get the raw Redis client from Flask-Caching."""
    from src.api.utils.caching import cache
    cache_type = current_app.config.get('CACHE_TYPE', 'simple')
    if cache_type == 'redis':
        return cache.cache._write_client
    return None


def _cache_key(session_id: str) -> str:
    return f"{CACHE_KEY_PREFIX}{session_id}"


def _load_from_db(integration) -> list:
    """Load raw records from the latest batch in DB."""
    try:
        db_adapter = current_app.db_adapter
        # Only load from the most recent batch to avoid pulling all historical data
        rows = db_adapter.execute_query(
            """
            SELECT r.raw_payload AS data
            FROM raw_source_data r
            INNER JOIN (
                SELECT id FROM ingestion_batches
                WHERE integration_id = %(integration_id)s
                ORDER BY started_at DESC
                LIMIT 1
            ) b ON r.batch_id = b.id
            ORDER BY r.id
            LIMIT 50000
            """,
            {'integration_id': str(integration.id)}
        )
        if rows:
            logger.info(f"Loaded {len(rows)} preview records from latest batch for {integration.name}")
            return [row['data'] for row in rows]
    except Exception as e:
        logger.warning(f"Could not load preview from DB: {e}")
    return []


@preview_bp.route('/preview/fetch', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
def fetch_preview_data():
    """
    Fetch all records from a source integration, cache them in Redis,
    and return a sample + total count for the wizard UI.

    Request body:
        {
            "integration_id": "uuid",     // existing integration ID
            "session_id": "uuid",         // wizard session ID (client-generated)
            "config": {...}               // optional candidate config from
                                          // the wizard. Same merge semantics
                                          // as /test and /sample: non-sensitive
                                          // keys override the saved row,
                                          // sensitive keys fall back to the
                                          // saved decrypted value. When
                                          // present, the DB-cache shortcut
                                          // is skipped (records there were
                                          // collected with the SAVED query).
        }

    Returns:
        {
            "success": true,
            "total_count": 5000,
            "sample_count": 100,
            "samples": [...],
            "session_id": "uuid"
        }
    """
    data = request.get_json()
    if not data:
        raise ValidationError("Request body is required")

    integration_id = data.get('integration_id')
    session_id = data.get('session_id')
    candidate_config = data.get('config')

    if not integration_id:
        raise ValidationError("integration_id is required")
    if not session_id:
        raise ValidationError("session_id is required")

    # Validate session_id is a valid UUID
    try:
        uuid.UUID(session_id)
    except ValueError:
        raise ValidationError("session_id must be a valid UUID")

    integration_service = current_app.integration_service

    # Get integration with decrypted credentials
    integration = integration_service.repository.get_by_id(
        uuid.UUID(integration_id), decrypt=True
    )
    if not integration:
        return jsonify({'success': False, 'message': 'Integration not found'}), 404

    try:
        # When the wizard is iterating on the config (candidate_config
        # provided), skip the DB-cache shortcut — the cached rows were
        # collected with the SAVED query and would mislead the preview.
        all_records = (
            [] if candidate_config is not None else _load_from_db(integration)
        )

        if not all_records:
            # Fall back to live API fetch for new integrations with no data yet
            connector = integration_service.build_connector(
                integration, candidate_config
            )

            logger.info(f"Fetching all preview records from API for {integration.name} (session: {session_id})")
            all_records = connector.get_sample_data(limit=100000)

        # Flatten ServiceNow-style wrapper objects ({display_value, value} → string)
        # so the wizard sees clean field names instead of nested .display_value/.value
        if integration.integration_type == 'servicenow_cmdb':
            from src.modules.integrations.connectors.servicenow_connector import ServiceNowConnector
            all_records = [ServiceNowConnector._flatten_reference_fields(r) for r in all_records]

        total_count = len(all_records)
        logger.info(f"Preview data ready: {total_count} records for {integration.name} (session: {session_id})")

        # Cache in Redis
        redis_client = _get_redis_client()
        if redis_client:
            cache_data = json.dumps(all_records, default=str)
            redis_client.setex(_cache_key(session_id), CACHE_TTL, cache_data)
            logger.info(f"Cached {total_count} records in Redis ({len(cache_data)} bytes, TTL: {CACHE_TTL}s)")
        else:
            # Fallback: store in app-level dict (for dev/test without Redis)
            if not hasattr(current_app, '_preview_cache'):
                current_app._preview_cache = {}
            current_app._preview_cache[session_id] = all_records
            logger.info(f"Cached {total_count} records in memory (no Redis available)")

        sample_limit = PREVIEW_SAMPLE_LIMIT
        samples = all_records[:sample_limit]

        return jsonify({
            'success': True,
            'total_count': total_count,
            'sample_count': len(samples),
            'samples': samples,
            'session_id': session_id
        })

    except Exception as e:
        logger.error(f"Error fetching preview data: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'Failed to fetch preview data: {str(e)}'
        }), 500


@preview_bp.route('/preview/filter-count', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
def preview_filter_count():
    """
    Evaluate filters against cached preview records and return match count.

    Request body:
        {
            "session_id": "uuid",
            "filters": [
                {"field": "accountEnabled", "operator": "is_true"}
            ]
        }

    Returns:
        {
            "success": true,
            "match_count": 4450,
            "total_count": 5000
        }
    """
    data = request.get_json()
    if not data:
        raise ValidationError("Request body is required")

    session_id = data.get('session_id')
    filters = data.get('filters', [])

    if not session_id:
        raise ValidationError("session_id is required")

    # Load cached records
    records = None
    redis_client = _get_redis_client()
    if redis_client:
        cached = redis_client.get(_cache_key(session_id))
        if cached:
            records = json.loads(cached)
    else:
        # Fallback: in-memory cache
        cache_dict = getattr(current_app, '_preview_cache', {})
        records = cache_dict.get(session_id)

    if records is None:
        return jsonify({
            'success': False,
            'message': 'Preview cache expired or not found. Re-fetch preview data.'
        }), 404

    total_count = len(records)

    if not filters:
        return jsonify({
            'success': True,
            'match_count': total_count,
            'total_count': total_count
        })

    # Evaluate filters using the same shared logic as BaseConnector
    match_count = 0
    for record in records:
        if data_filters.matches_filters(record, filters):
            match_count += 1

    return jsonify({
        'success': True,
        'match_count': match_count,
        'total_count': total_count
    })


@preview_bp.route('/preview/<session_id>', methods=['DELETE'])
@jwt_required()
@require_admin()
@handle_errors
def delete_preview_cache(session_id: str):
    """Delete cached preview data for a session."""
    redis_client = _get_redis_client()
    if redis_client:
        redis_client.delete(_cache_key(session_id))
    else:
        cache_dict = getattr(current_app, '_preview_cache', {})
        cache_dict.pop(session_id, None)

    logger.info(f"Deleted preview cache for session {session_id}")
    return jsonify({'success': True})


_BODY_PREVIEW_BYTES = 50 * 1024  # 50 KB
_SAMPLE_RECORDS = 3


def _scrub_url_in_message(message: str) -> str:
    """Strip query strings from URLs that requests' HTTPError embeds in its
    message, so an api_key carried as a query param doesn't leak back to the
    wizard. The message shape is ``"<status> Client Error: <reason> for url: <URL>"``;
    we replace any '?...' on the URL with '?[redacted]'."""
    if not message:
        return message
    # Find "for url: <URL>" and scrub the query portion of <URL> only.
    marker = "for url: "
    idx = message.rfind(marker)
    if idx < 0:
        return message
    head = message[: idx + len(marker)]
    tail = message[idx + len(marker):]
    qpos = tail.find("?")
    if qpos < 0:
        return message
    return f"{head}{tail[:qpos]}?[redacted]"


@preview_bp.route('/connectors/custom_http/test', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
def test_custom_http():
    """Single HTTP call against an in-progress custom_http config blob.

    Always returns HTTP 200 with success/error inside the body so the wizard
    can render inline. Auth/permission failures are handled by decorators.
    """
    body = request.get_json() or {}
    integration_id = body.pop("integration_id", None)
    config = body

    # Editing an existing integration: the wizard loads credentials encrypted
    # (edit-mode load doesn't decrypt them), so the candidate config carries
    # ``ENC:...`` secrets. Merge against the saved decrypted config — sensitive
    # keys fall back to the stored secret when the candidate is
    # empty/unchanged/still-encrypted. Mirrors the /test, /sample and
    # /preview/fetch endpoints; without it the stateless connector test sent
    # the ciphertext as the password and the upstream API rejected it as 401.
    if integration_id:
        saved = None
        try:
            saved = current_app.integration_service.repository.get_by_id(
                uuid.UUID(str(integration_id)), decrypt=True
            )
        except Exception:
            # A bad id or a transient DB/decrypt error must not break this
            # endpoint's always-200 inline-error contract. Fall back to the
            # candidate config; the connector call below surfaces any auth
            # failure inline.
            logger.warning(
                "custom_http test: could not load integration %s for "
                "credential merge", integration_id, exc_info=True
            )
        if saved is not None and saved.config:
            config = IntegrationService.merge_candidate_config(
                config, saved.config
            )

    connector = CustomHttpConnector(config)
    v = connector.validate_config()
    if not v["valid"]:
        return jsonify({
            "success": False,
            "error_type": "config",
            "message": "; ".join(v["errors"]),
            "errors": v["errors"],
        })

    try:
        result = connector.run_single_call()
    except _requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        error_type = "auth" if status in (401, 403) else "http"
        return jsonify({
            "success": False,
            "error_type": error_type,
            "message": _scrub_url_in_message(str(e)),
            "status_code": status,
        })
    except _requests.ConnectionError as e:
        return jsonify({
            "success": False,
            "error_type": "network",
            "message": _scrub_url_in_message(str(e)),
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error_type": "http",
            "message": _scrub_url_in_message(str(e)),
        })

    body = result["body"]
    serialized = json.dumps(body)
    truncated = False
    if len(serialized) > _BODY_PREVIEW_BYTES:
        truncated = True
        body = {"_truncated_preview": serialized[:_BODY_PREVIEW_BYTES]}

    out = {
        "success": True,
        "status_code": result["status_code"],
        "elapsed_ms": result["elapsed_ms"],
        "response_headers": result["response_headers"],
        "body_truncated": truncated,
        "body_preview": body,
    }

    rr = (config.get("records_root") or "").strip()
    if rr and rr != "$":
        err = validate_jsonpath(rr)
        if err:
            out["records_root_match"] = {"valid_path": False, "error": err}
        else:
            try:
                root_val = extract_many(result["body"], rr)
                if len(root_val) == 1 and isinstance(root_val[0], list):
                    matched_records = root_val[0]
                else:
                    matched_records = root_val
                out["records_root_match"] = {
                    "valid_path": True,
                    "matched_count": len(matched_records),
                    "sample_records": matched_records[:_SAMPLE_RECORDS],
                }
            except Exception as e:
                out["records_root_match"] = {"valid_path": False, "error": str(e)}
    elif not rr:
        # Auto-detect mode — show the user what we picked.
        detected_path, records = CustomHttpConnector.auto_detect_records_root(
            result["body"]
        )
        out["records_root_match"] = {
            "valid_path": detected_path is not None,
            "auto_detected": True,
            "detected_path": detected_path,
            "matched_count": len(records),
            "sample_records": records[:_SAMPLE_RECORDS],
            "error": (
                None if detected_path is not None
                else "Could not auto-detect a records list. "
                     "Set Records root explicitly."
            ),
        }

    return jsonify(out)
