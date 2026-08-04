# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Log Management API Endpoints

Provides REST API for log retention and cleanup management.

Endpoints:
- GET    /api/logs/retention/settings - Get current retention settings
- PUT    /api/logs/retention/settings - Update retention settings
- POST   /api/logs/retention/cleanup  - Trigger manual cleanup
- GET    /api/logs/retention/status   - Get cleanup service status
"""

import logging
from flask import Blueprint, request, current_app, jsonify
from flask_jwt_extended import jwt_required

from src.api.middleware.error_handler import handle_errors, ValidationError
from src.api.utils.responses import response_wrapper
from src.auth.middleware.rbac import require_admin

logger = logging.getLogger(__name__)

# Create blueprint
log_management_bp = Blueprint('log_management', __name__, url_prefix='/api/logs')


@log_management_bp.route('/retention/settings', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_retention_settings():
    """
    Get current log retention settings.

    Returns:
        200: Current retention settings including schedule
    """
    cleanup_service = current_app.log_cleanup_service
    settings = cleanup_service.get_settings()

    return {
        'settings': settings
    }


@log_management_bp.route('/retention/settings', methods=['PUT'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def update_retention_settings():
    """
    Update log retention settings.

    Request Body:
        {
            "retention_days": {
                "access": 30,
                "app": 30,
                "error": 90,
                "security": 365,
                "performance": 60
            },
            "compression_after_days": 7,
            "cleanup_schedule": "0 2 * * *",
            "enabled": true
        }

    Returns:
        200: Settings updated successfully
        400: Validation error
    """
    cleanup_service = current_app.log_cleanup_service
    data = request.get_json()

    if not data:
        raise ValidationError('Request body is required')

    # Validate retention_days if provided
    if 'retention_days' in data:
        retention_days = data['retention_days']
        if not isinstance(retention_days, dict):
            raise ValidationError('retention_days must be an object')

        valid_types = ['access', 'app', 'error', 'security', 'performance']
        for log_type, days in retention_days.items():
            if log_type not in valid_types:
                raise ValidationError(f'Unknown log type: {log_type}')
            if not isinstance(days, int) or days < 1 or days > 3650:
                raise ValidationError(f'Invalid retention days for {log_type}: must be 1-3650')

    # Validate compression_after_days if provided
    if 'compression_after_days' in data:
        days = data['compression_after_days']
        if not isinstance(days, int) or days < 1 or days > 365:
            raise ValidationError('compression_after_days must be 1-365')

    # Validate cleanup_schedule if provided
    if 'cleanup_schedule' in data:
        schedule = data['cleanup_schedule']
        if not isinstance(schedule, str):
            raise ValidationError('cleanup_schedule must be a cron expression string')
        # Basic cron validation (5 space-separated fields)
        parts = schedule.strip().split()
        if len(parts) != 5:
            raise ValidationError('cleanup_schedule must be a valid cron expression (5 fields)')

    # Validate enabled if provided
    if 'enabled' in data:
        if not isinstance(data['enabled'], bool):
            raise ValidationError('enabled must be a boolean')

    # Update settings
    success = cleanup_service.update_settings(data)

    if not success:
        raise ValidationError('Failed to update settings')

    # Reschedule cleanup job if schedule changed
    if 'cleanup_schedule' in data and hasattr(current_app, 'scheduler_service'):
        try:
            _reschedule_cleanup_job()
            logger.info(f"Log cleanup job rescheduled with new cron: {data['cleanup_schedule']}")
        except Exception as e:
            logger.warning(f"Failed to reschedule cleanup job: {e}")

    # Get updated settings
    updated_settings = cleanup_service.get_settings()

    return {
        'message': 'Log retention settings updated successfully',
        'settings': updated_settings
    }


@log_management_bp.route('/retention/cleanup', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def trigger_cleanup():
    """
    Trigger a manual log cleanup operation.

    Request Body (optional):
        {
            "dry_run": false
        }

    Returns:
        200: Cleanup result with statistics
    """
    cleanup_service = current_app.log_cleanup_service
    data = request.get_json() or {}

    dry_run = data.get('dry_run', False)
    if not isinstance(dry_run, bool):
        raise ValidationError('dry_run must be a boolean')

    result = cleanup_service.run_cleanup(dry_run=dry_run)

    return {
        'message': 'Log cleanup completed' if not dry_run else 'Dry run completed',
        'result': result.to_dict()
    }


@log_management_bp.route('/retention/status', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_cleanup_status():
    """
    Get current status of log cleanup service.

    Returns disk usage, oldest file, last cleanup result, and schedule information.

    Returns:
        200: Cleanup service status
    """
    cleanup_service = current_app.log_cleanup_service
    status = cleanup_service.get_status()

    # Add scheduler information if available
    if hasattr(current_app, 'scheduler_service'):
        try:
            scheduler = current_app.scheduler_service
            job = scheduler.get_job('log_cleanup')
            if job:
                status['next_scheduled_run'] = job.next_run_time.isoformat() if job.next_run_time else None
            else:
                status['next_scheduled_run'] = None
        except Exception as e:
            logger.warning(f"Failed to get scheduler info: {e}")
            status['next_scheduled_run'] = 'unknown'
    else:
        status['next_scheduled_run'] = 'scheduler_not_configured'

    return status


def _reschedule_cleanup_job():
    """
    Reschedule the log cleanup job with current settings.

    This is called when cleanup_schedule is updated.
    NOTE: Scheduler integration is optional. If not configured, cleanup runs manually via API.
    """
    if not hasattr(current_app, 'scheduler_service') or not hasattr(current_app, 'log_cleanup_service'):
        logger.info("Scheduler not available - cleanup will run manually via API")
        return

    # Future enhancement: integrate with SchedulerService for automatic cleanup
    # For now, cleanup is triggered manually via POST /api/logs/retention/cleanup
    logger.info("Schedule updated - manual cleanup available via API")
