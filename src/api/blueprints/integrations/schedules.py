# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Schedules Blueprint

Handles schedule configuration and management for integrations.

Endpoints:
    GET  /api/integrations/<id>/schedule           - Get schedule configuration
    PUT  /api/integrations/<id>/schedule           - Update schedule
    POST /api/integrations/<id>/schedule/enable    - Enable schedule
    POST /api/integrations/<id>/schedule/disable   - Disable schedule
    POST /api/integrations/<id>/schedule/trigger   - Trigger immediate run
    GET  /api/integrations/<id>/schedule/next-runs - Get next scheduled run times
    GET  /api/integrations/scheduler/status        - Get scheduler status
"""

import logging
from flask_jwt_extended import jwt_required
from src.auth.middleware.rbac import require_admin
from flask import Blueprint, request, jsonify, current_app
from uuid import UUID

from src.api.middleware.error_handler import handle_errors, NotFoundError, ValidationError
from src.modules.integrations.shared.cron_validator import (
    calculate_next_runs,
    get_cron_description,
    validate_cron_with_details
)

logger = logging.getLogger(__name__)

# Create blueprint
schedules_bp = Blueprint('schedules', __name__)


@schedules_bp.route('/<uuid:integration_id>/schedule', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_schedule(integration_id: UUID):
    """
    Get schedule configuration for an integration.

    Returns:
        200: Schedule details
        404: Integration not found
    """
    integration_service = current_app.integration_service
    scheduler_service = current_app.scheduler_service
    integration = integration_service.get_integration(integration_id)

    if not integration:
        raise NotFoundError(f'No integration found with ID: {integration_id}')

    # Get schedule details
    schedule_info = {
        'enabled': integration.schedule_enabled,
        'cron': integration.schedule_cron,
        'timezone': integration.schedule_timezone,
        'next_run_at': integration.next_run_at.isoformat() if integration.next_run_at else None,
        'is_running': integration.is_running
    }

    # Add description if cron is set
    if integration.schedule_cron:
        schedule_info['description'] = get_cron_description(integration.schedule_cron)

        # Add detailed validation info
        validation = validate_cron_with_details(
            integration.schedule_cron,
            integration.schedule_timezone
        )
        if validation['valid']:
            schedule_info['next_5_runs'] = validation.get('next_5_runs', [])

    return jsonify({
        'success': True,
        'schedule': schedule_info
    }), 200


@schedules_bp.route('/<uuid:integration_id>/schedule', methods=['PUT'])
@jwt_required()
@require_admin()
@handle_errors
def update_schedule(integration_id: UUID):
    """
    Update schedule configuration for an integration.

    Request body:
        {
            "cron": "0 * * * *",
            "timezone": "America/New_York",
            "enabled": true
        }

    Returns:
        200: Schedule updated
        400: Invalid cron or timezone
        404: Integration not found
    """
    integration_service = current_app.integration_service
    scheduler_service = current_app.scheduler_service
    data = request.get_json()

    # Validate required fields
    if 'cron' not in data:
        raise ValidationError('cron expression is required')

    cron = data['cron']
    timezone = data.get('timezone', 'UTC')
    enabled = data.get('enabled', True)

    # Validate cron expression
    validation = validate_cron_with_details(cron, timezone)
    if not validation['valid']:
        raise ValidationError(
            validation.get('message', 'Please provide a valid cron expression')
        )

    if enabled:
        # Schedule the integration
        result = scheduler_service.schedule_integration(integration_id, cron, timezone)
    else:
        # Unschedule the integration
        result = scheduler_service.unschedule_integration(integration_id)

    if not result['success']:
        raise ValidationError(result.get('message', 'Failed to update schedule'))

    # Get updated integration
    integration = integration_service.get_integration(integration_id)

    return jsonify({
        'success': True,
        'message': 'Schedule updated successfully',
        'schedule': {
            'enabled': integration.schedule_enabled,
            'cron': integration.schedule_cron,
            'timezone': integration.schedule_timezone,
            'next_run_at': integration.next_run_at.isoformat() if integration.next_run_at else None,
            'description': validation.get('description')
        }
    }), 200


@schedules_bp.route('/<uuid:integration_id>/schedule/enable', methods=['PATCH'])
@jwt_required()
@require_admin()
@handle_errors
def enable_schedule(integration_id: UUID):
    """
    Enable scheduling for an integration.

    Returns:
        200: Schedule enabled
        400: No schedule configured or error
        404: Integration not found
    """
    integration_service = current_app.integration_service
    scheduler_service = current_app.scheduler_service
    integration = integration_service.get_integration(integration_id)

    if not integration:
        raise NotFoundError(f'No integration found with ID: {integration_id}')

    if not integration.schedule_cron:
        raise ValidationError('Please configure a schedule first using PUT /schedule')

    # Schedule the integration
    result = scheduler_service.schedule_integration(
        integration_id,
        integration.schedule_cron,
        integration.schedule_timezone
    )

    if not result['success']:
        raise ValidationError(result.get('message', 'Failed to enable schedule'))

    return jsonify({
        'success': True,
        'message': 'Schedule enabled successfully',
        'next_run_at': result.get('next_run_at')
    }), 200


@schedules_bp.route('/<uuid:integration_id>/schedule/disable', methods=['PATCH'])
@jwt_required()
@require_admin()
@handle_errors
def disable_schedule(integration_id: UUID):
    """
    Disable scheduling for an integration.

    Returns:
        200: Schedule disabled
        404: Integration not found
    """
    integration_service = current_app.integration_service
    scheduler_service = current_app.scheduler_service
    integration = integration_service.get_integration(integration_id)

    if not integration:
        raise NotFoundError(f'No integration found with ID: {integration_id}')

    # Unschedule the integration
    result = scheduler_service.unschedule_integration(integration_id)

    if not result['success']:
        raise ValidationError(result.get('message', 'Failed to disable schedule'))

    return jsonify({
        'success': True,
        'message': 'Schedule disabled successfully'
    }), 200


@schedules_bp.route('/<uuid:integration_id>/schedule/trigger', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
def trigger_now(integration_id: UUID):
    """
    Trigger immediate execution (doesn't affect schedule).

    Returns:
        200: Integration triggered
        400: Integration already running or error
        404: Integration not found

    Note: This endpoint does not use @response_wrapper because it returns
    the service's result directly (which already has success/error structure).
    """
    integration_service = current_app.integration_service
    scheduler_service = current_app.scheduler_service
    # Trigger execution
    result = scheduler_service.trigger_integration(integration_id)

    status_code = 200 if result['success'] else 400

    return jsonify(result), status_code


@schedules_bp.route('/<uuid:integration_id>/schedule/next-runs', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_next_runs(integration_id: UUID):
    """
    Get the next scheduled run times for an integration.

    Query parameters:
        count: Number of runs to return (default: 5, max: 20)

    Returns:
        200: List of next run times in ISO format
        400: No schedule configured
        404: Integration not found
    """
    integration_service = current_app.integration_service
    integration = integration_service.get_integration(integration_id)

    if not integration:
        raise NotFoundError('Integration not found')

    if not integration.schedule_cron:
        raise ValidationError('Integration has no schedule configured')

    count = min(int(request.args.get('count', 5)), 20)
    timezone = integration.schedule_timezone or 'UTC'

    next_runs = calculate_next_runs(integration.schedule_cron, timezone, count=count)

    return jsonify({
        'success': True,
        'integration_id': str(integration_id),
        'count': len(next_runs),
        'next_runs': [dt.isoformat() for dt in next_runs]
    }), 200


@schedules_bp.route('/scheduler/status', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_scheduler_status():
    """
    Get scheduler status and job information.
    Falls back to Redis for cross-process visibility when scheduler runs in worker mode.

    Returns:
        200: Scheduler status
    """
    scheduler_service = current_app.scheduler_service
    status = scheduler_service.get_scheduler_status()

    # If local scheduler isn't running, check Redis for worker status
    if not status.get('running'):
        try:
            import json
            from src.api.utils.caching import cache as flask_cache
            if hasattr(flask_cache, 'cache') and hasattr(flask_cache.cache, '_read_client'):
                redis_client = flask_cache.cache._read_client
                cached = redis_client.get('scheduler:status')
                if cached:
                    status = json.loads(cached)
                    status['source'] = 'worker'
        except Exception:
            pass

    return jsonify({
        'success': True,
        'scheduler': status
    }), 200
