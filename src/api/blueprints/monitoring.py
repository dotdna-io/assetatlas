# SPDX-License-Identifier: AGPL-3.0-or-later
"""Monitoring blueprint — admin-only Prometheus proxy.

Endpoints:
- GET /api/monitoring/query        — instant query
- GET /api/monitoring/query_range  — range query (with start/end/step)
- GET /api/monitoring/status       — dashboards-enabled + Prometheus reachability
- GET /api/monitoring/storage      — TSDB usage + applied/desired retention

The proxy routes are JWT + admin-gated and additionally gated on the runtime
`monitoring.dashboards` setting: the blueprint is always registered (so the
Settings toggle needs no restart), but when the dashboards are disabled the
proxy behaves as if the feature isn't there (404), matching the pre-toggle
contract. `/status` is intentionally NOT gated so the Settings page can probe
reachability before enabling.
"""

import logging

from flask import Blueprint, request, current_app
from flask_jwt_extended import jwt_required

from src.api.middleware.error_handler import handle_errors, NotFoundError, ValidationError
from src.api.modules.settings.settings_schema import monitoring_dashboards_enabled
from src.modules.monitoring.durations import parse_prometheus_duration
from src.api.utils.responses import response_wrapper
from src.auth.middleware.rbac import require_admin

logger = logging.getLogger(__name__)

monitoring_bp = Blueprint('monitoring', __name__, url_prefix='/api/monitoring')


def _dashboards_enabled() -> bool:
    """Resolve the effective on/off state of the monitoring dashboards."""
    return monitoring_dashboards_enabled(
        getattr(current_app, 'settings_manager', None),
        current_app.config.get('MONITORING_ENABLED', False),
    )


def _require_dashboards_enabled():
    """404 when the dashboards are disabled — the proxy's routes then read as
    'feature not enabled', which is how callers already treat a missing proxy."""
    if not _dashboards_enabled():
        raise NotFoundError('Monitoring dashboards are not enabled')


@monitoring_bp.route('/query', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def proxy_query():
    """Proxy to Prometheus /api/v1/query (instant)."""
    _require_dashboards_enabled()
    query = request.args.get('query', type=str)
    time_param = request.args.get('time', type=str)
    if not query:
        raise ValidationError('query parameter is required')
    return current_app.monitoring_service.query(query, time=time_param)


@monitoring_bp.route('/query_range', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def proxy_query_range():
    """Proxy to Prometheus /api/v1/query_range."""
    _require_dashboards_enabled()
    query = request.args.get('query', type=str)
    start = request.args.get('start', type=str)
    end = request.args.get('end', type=str)
    step = request.args.get('step', type=str)
    missing = [name for name, v in (
        ('query', query), ('start', start), ('end', end), ('step', step),
    ) if not v]
    if missing:
        raise ValidationError(f"Missing required parameter(s): {', '.join(missing)}")
    return current_app.monitoring_service.query_range(query, start=start, end=end, step=step)


@monitoring_bp.route('/status', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def monitoring_status():
    """Report whether dashboards are enabled and whether Prometheus is reachable.

    Not gated on the toggle: the Settings page probes this before enabling to
    tell the admin if the monitoring stack (Prometheus) is actually deployed.
    """
    reachable, detail = current_app.monitoring_service.ping()
    return {
        'enabled': _dashboards_enabled(),
        'prometheus_reachable': reachable,
        'detail': detail,
    }


@monitoring_bp.route('/storage', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def monitoring_storage():
    """TSDB storage usage + applied/desired retention for the Settings panel.

    Not gated on the dashboards toggle — retention/storage is a stack-level
    concern (like /status), useful whether or not the dashboards are shown.
    """
    stats = current_app.monitoring_service.storage_stats()

    settings_manager = getattr(current_app, 'settings_manager', None)
    desired = ''
    if settings_manager is not None:
        try:
            desired = settings_manager.get('monitoring', 'retention') or ''
        except Exception:  # noqa: BLE001 — a settings read must not 500 the panel
            desired = ''

    desired_seconds = parse_prometheus_duration(desired) if desired else None
    applied_seconds = stats.get('applied_retention_seconds')
    # applied_seconds is None when Prometheus is unreachable or its flags read
    # failed: we can't confirm drift then, so pending stays False (fail-safe — we
    # never surface an apply command we can't justify).
    pending = (
        bool(desired)
        and desired_seconds is not None
        and applied_seconds is not None
        and desired_seconds != applied_seconds
    )
    return {**stats, 'desired_retention': desired, 'pending': pending}
