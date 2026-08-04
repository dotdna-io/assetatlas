# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Pipeline Blueprint

Provides REST API for pipeline execution, status monitoring, and run history.

Endpoints:
    POST /api/pipeline/trigger                  - Trigger a pipeline run
    GET  /api/pipeline/status                   - Get current pipeline status
    GET  /api/pipeline/runs                     - Get paginated run history
    GET  /api/pipeline/runs/<run_id>            - Get single run details
    GET  /api/pipeline/runs/<run_id>/phases     - Get 4-phase view for a run
    GET  /api/pipeline/data-version             - Last finished run id (any role)

Every route here is admin-only EXCEPT /data-version, which any authenticated
role may read — see its handler for why.
"""

import logging
from flask import Blueprint, request, current_app, jsonify
from flask_jwt_extended import jwt_required
from uuid import UUID

from src.api.middleware.error_handler import handle_errors, NotFoundError
from src.api.utils.responses import response_wrapper
from src.auth.middleware.rbac import require_admin

logger = logging.getLogger(__name__)

pipeline_bp = Blueprint('pipeline', __name__)


@pipeline_bp.route('/trigger', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
def trigger_pipeline():
    """Trigger a pipeline run. Returns run_id immediately."""
    pipeline_service = current_app.pipeline_service
    result = pipeline_service.trigger_run(triggered_by='manual')

    if result.get('success'):
        return jsonify({
            'success': True,
            'data': {'run_id': result['run_id']}
        }), 202
    else:
        return jsonify({
            'success': False,
            'error': result.get('error', 'Failed to trigger pipeline'),
            'data': {'run_id': result.get('run_id')}
        }), 409


@pipeline_bp.route('/status', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_pipeline_status():
    """Get current pipeline status (running or idle), including next scheduled run."""
    pipeline_service = current_app.pipeline_service
    status = pipeline_service.get_current_status()

    # Include next scheduled run time if available
    next_run = _get_pipeline_next_run(pipeline_service)
    if next_run:
        status['next_run_at'] = next_run

    return status


@pipeline_bp.route('/data-version', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def get_data_version():
    """Identify the data currently in `assets`, so clients can drop stale caches.

    A run rebuilds the asset table from scratch, which invalidates every cached
    query in the UI at once. The app polls this and clears its client-side
    caches whenever `run_id` changes.

    NOT @require_admin(), unlike every other route in this blueprint, and that
    is the point: the operators who live in Explorer hold the plain `user`
    role, so an admin gate would 403 every poll for exactly the people who need
    to see the new data. It exposes only a run id, its outcome and a timestamp
    — no asset data, no run statistics — which is why widening the audience is
    safe. /status stays admin-only; it carries stage names and run stats.
    """
    return current_app.pipeline_service.get_data_version()


def _get_pipeline_next_run(pipeline_service):
    """
    Get the next scheduled pipeline run time.

    For cron mode: always compute from cron expression (authoritative, no stale data).
    For interval mode: try APScheduler/Redis first, fall back to last_run + interval.
    """
    from datetime import datetime, timezone, timedelta

    try:
        enabled = pipeline_service._get_setting(
            'asset_generation', 'pipeline_schedule_enabled'
        )
        if not enabled:
            return None

        mode = pipeline_service._get_setting(
            'asset_generation', 'pipeline_schedule_mode'
        )

        # Cron mode: compute directly from expression (always fresh)
        if mode == 'cron':
            from src.modules.integrations.shared.cron_validator import calculate_next_run as cron_next_run

            cron_expr = pipeline_service._get_setting(
                'asset_generation', 'pipeline_schedule_cron'
            )
            tz_name = pipeline_service._get_setting(
                'asset_generation', 'pipeline_schedule_timezone'
            )
            next_run = cron_next_run(cron_expr, tz_name)
            return next_run.isoformat() if next_run else None

        # Interval mode: try APScheduler / Redis first, fall back to computation
        # 1. APScheduler directly (embedded scheduler in same process)
        scheduler_service = getattr(current_app, 'scheduler_service', None)
        if scheduler_service and getattr(scheduler_service, 'scheduler', None):
            try:
                job = scheduler_service.scheduler.get_job(
                    pipeline_service.PIPELINE_SCHEDULE_JOB_ID
                )
                if job and getattr(job, 'next_run_time', None):
                    return job.next_run_time.isoformat()
            except Exception:
                pass

        # 2. Redis scheduler status (separate scheduler worker)
        try:
            from src.api.utils.caching import cache as flask_cache
            if hasattr(flask_cache, 'cache') and hasattr(flask_cache.cache, '_read_clients'):
                import json
                redis_client = flask_cache.cache._read_clients[0]
                raw = redis_client.get('scheduler:status')
                if raw:
                    sched_status = json.loads(raw)
                    for job in sched_status.get('jobs', []):
                        if job.get('id') == pipeline_service.PIPELINE_SCHEDULE_JOB_ID:
                            if job.get('next_run_time'):
                                return job['next_run_time']
        except Exception:
            pass

        # 3. Compute from last run + interval (always works)
        interval_hours = pipeline_service._get_setting(
            'asset_generation', 'pipeline_schedule_interval_hours'
        )
        interval = timedelta(hours=interval_hours)
        now = datetime.now(timezone.utc)

        last_run = pipeline_service._db.execute_query(
            """SELECT completed_at FROM pipeline_runs
               WHERE status = 'completed'
               ORDER BY completed_at DESC LIMIT 1""",
            (), fetch=True
        )

        if last_run and last_run[0].get('completed_at'):
            next_run = last_run[0]['completed_at'] + interval
            if next_run <= now:
                elapsed = (now - last_run[0]['completed_at']).total_seconds()
                intervals_passed = int(elapsed / interval.total_seconds())
                next_run = last_run[0]['completed_at'] + interval * (intervals_passed + 1)
        else:
            next_run = now + interval

        return next_run.isoformat()
    except Exception:
        return None


@pipeline_bp.route('/runs', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_pipeline_runs():
    """Get paginated pipeline run history."""
    pipeline_service = current_app.pipeline_service
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    per_page = min(per_page, 100)  # Cap at 100
    return pipeline_service.get_runs(page=page, per_page=per_page)


@pipeline_bp.route('/runs/<uuid:run_id>', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_pipeline_run(run_id: UUID):
    """Get a single pipeline run's details."""
    pipeline_service = current_app.pipeline_service
    run = pipeline_service.get_run(run_id)
    if not run:
        raise NotFoundError(f'Pipeline run {run_id} not found')
    return run


@pipeline_bp.route('/runs/<uuid:run_id>/phases', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_pipeline_run_phases(run_id: UUID):
    """4-phase operator view of a run with rolling-baseline outliers."""
    phase_service = current_app.pipeline_phase_service
    result = phase_service.get_phases_for_run(run_id)
    if result is None:
        raise NotFoundError(f'Pipeline run {run_id} not found')
    return result
