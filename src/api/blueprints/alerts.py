# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Alerts Blueprint

Endpoints:
- GET   /api/alerts           - List alerts with filters, summary, and timeline
- PATCH /api/alerts/<id>      - Update alert status (acknowledge/resolve)
"""

import hmac
import logging
from flask import Blueprint, request, current_app
from flask_jwt_extended import jwt_required

from src.api.middleware.error_handler import handle_errors, ValidationError, AuthError
from src.api.utils.responses import response_wrapper
from src.api.utils.validation import ensure_uuid
from src.auth.middleware.rbac import require_admin

logger = logging.getLogger(__name__)

alerts_bp = Blueprint('alerts', __name__, url_prefix='/api/alerts')

VALID_STATUSES = {'acknowledged', 'resolved'}


@alerts_bp.route('', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def get_alerts():
    """Get alerts with filters, summary, timeline, and pagination."""
    service = current_app.alerts_service

    time_range = request.args.get('time_range', '7d')
    severity = request.args.get('severity') or None
    alert_type = request.args.get('type') or None
    status = request.args.get('status') or None
    search = request.args.get('search') or None
    # Optional exact asset filter (#903/#908). ensure_uuid canonicalises the id
    # and turns a malformed value into a 400 before the repo casts it to ::uuid
    # (an unvalidated cast would surface as a 500, per #973).
    asset_id = request.args.get('asset_id') or None
    if asset_id:
        asset_id = ensure_uuid(asset_id, 'alert asset')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    return service.get_alerts_response(
        time_range=time_range, severity=severity,
        alert_type=alert_type, status=status,
        search=search, asset_id=asset_id,
        page=page, per_page=per_page
    )


@alerts_bp.route('/<alert_id>', methods=['PATCH'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def patch_alert(alert_id):
    """Update alert status (acknowledge or resolve)."""
    service = current_app.alerts_service
    data = request.get_json()
    if not data:
        raise ValidationError('Request body is required')

    new_status = data.get('status')
    if new_status not in VALID_STATUSES:
        raise ValidationError(f"Invalid status '{new_status}'. Must be one of: {', '.join(VALID_STATUSES)}")

    result = service.update_alert_status(alert_id, new_status)
    if result is None:
        raise ValidationError(f"Alert {alert_id} not found")

    return result


@alerts_bp.route('/<alert_id>/history', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def get_alert_history(alert_id):
    """Per-run firing history for one alert (#905 timechart)."""
    service = current_app.alerts_service
    days = request.args.get('days', 90, type=int)
    return service.get_alert_history(alert_id, days=days)


@alerts_bp.route('/webhook/alertmanager', methods=['POST'])
@handle_errors
@response_wrapper
def alertmanager_webhook():
    """Alertmanager-facing webhook.

    Auth: shared-secret via Authorization: Bearer <secret>. No JWT —
    Alertmanager is a system peer, not a logged-in user.
    """
    expected = current_app.config.get('ALERTMANAGER_WEBHOOK_SECRET')
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        raise AuthError('Missing or malformed Authorization header')
    presented = auth_header[len('Bearer '):]
    if not expected or not hmac.compare_digest(presented, expected):
        raise AuthError('Invalid webhook credentials')

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValidationError('Invalid JSON body')

    return current_app.prometheus_webhook_service.handle_alertmanager_payload(payload)
