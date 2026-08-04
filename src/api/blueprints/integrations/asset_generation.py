# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Asset Generation Blueprint

Handles asset generation triggers, status monitoring, and health diagnostics.

Endpoints:
    POST /api/integrations/asset-generation/trigger              - Trigger generation for all pending batches
    POST /api/integrations/asset-generation/trigger/<batch_id>  - Trigger generation for specific batch
    GET  /api/integrations/asset-generation/status              - Get generation status overview
    GET  /api/integrations/<id>/asset-generation/history        - Get integration generation history
    GET  /api/integrations/<id>/health                          - Get integration health status
    GET  /api/integrations/<id>/generation-stats                - Get generation statistics
    GET  /api/integrations/<id>/schedule/next-runs              - Calculate next scheduled runs
    GET  /api/integrations/<id>/recent-errors                   - Get recent error logs
"""

import logging
from flask_jwt_extended import jwt_required
from src.auth.middleware.rbac import require_admin
from flask import Blueprint, request, jsonify, current_app
from uuid import UUID

from src.api.middleware.error_handler import handle_errors, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

# Create blueprint
asset_generation_bp = Blueprint('asset_generation', __name__)



# =============================================================================
# Asset Generation Triggers
# =============================================================================

@asset_generation_bp.route('/asset-generation/trigger', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
def trigger_asset_generation():
    """
    Trigger asset generation for all pending batches.

    Processes latest batch from each integration that is:
    - Completed mapping (status = 'completed')
    - Not yet generated (asset_generation_status IS NULL)
    - Has mapped records (mapped_records_count > 0)

    Returns:
        200: Generation results with counts
        500: Service not initialized or server error

    Note: This endpoint does not use @response_wrapper because it returns
    a complex result structure with multiple count fields.
    """
    asset_generation_service = current_app.asset_generation_trigger_service
    if not asset_generation_service:
        return jsonify({
            'success': False,
            'error': 'Asset generation service not initialized'
        }), 500

    logger.info("Triggering asset generation for all pending batches")

    result = asset_generation_service.generate_assets_for_latest_batches()

    if result['success']:
        return jsonify({
            'success': True,
            'message': f"Generated assets from {result['batches_processed']} batches",
            'batches_processed': result['batches_processed'],
            'batches_failed': result.get('batches_failed', 0),
            'total_assets_created': result['total_assets_created'],
            'total_assets_updated': result['total_assets_updated'],
            'duration_seconds': result['duration_seconds'],
            'batch_results': result['batch_results']
        }), 200
    else:
        return jsonify({
            'success': False,
            'error': result.get('error', 'Unknown error'),
            'duration_seconds': result.get('duration_seconds', 0)
        }), 500


@asset_generation_bp.route('/asset-generation/trigger/<uuid:batch_id>', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
def trigger_batch_generation(batch_id: UUID):
    """
    Trigger asset generation for a specific batch.

    Args:
        batch_id: Batch UUID

    Returns:
        200: Generation results
        404: Batch not found
        409: Batch already generated or not ready
        500: Server error

    Note: This endpoint does not use @response_wrapper because it returns
    a complex result structure and needs custom status code handling.
    """
    asset_generation_service = current_app.asset_generation_trigger_service
    if not asset_generation_service:
        return jsonify({
            'success': False,
            'error': 'Asset generation service not initialized'
        }), 500

    logger.info(f"Triggering asset generation for batch {batch_id}")

    result = asset_generation_service.generate_assets_for_batch(batch_id)

    if result['success']:
        return jsonify({
            'success': True,
            'message': f"Successfully generated assets from batch {batch_id}",
            'batch_id': result['batch_id'],
            'assets_created': result['assets_created'],
            'assets_updated': result['assets_updated'],
            'duration_seconds': result['duration_seconds'],
            'input_records': result.get('input_records', 0),
            'stats': result.get('stats', {})
        }), 200
    else:
        error = result.get('error', 'Unknown error')

        # Determine appropriate status code
        if 'not found' in error.lower():
            status_code = 404
        elif 'already generated' in error.lower() or 'not ready' in error.lower():
            status_code = 409
        else:
            status_code = 500

        return jsonify({
            'success': False,
            'error': error,
            'batch_id': result['batch_id'],
            'duration_seconds': result.get('duration_seconds', 0)
        }), status_code


@asset_generation_bp.route('/asset-generation/status', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_generation_status():
    """
    Get asset generation status overview.

    Returns comprehensive status across all batches including:
    - Pending batches (ready for generation)
    - Processing batches (currently generating)
    - Completed batches
    - Failed batches
    - Last generation details
    - Total assets generated

    Returns:
        200: Status overview
        500: Service not initialized or server error

    Note: This endpoint does not use @response_wrapper because it returns
    a complex multi-field structure.
    """
    asset_generation_service = current_app.asset_generation_trigger_service
    if not asset_generation_service:
        return jsonify({
            'success': False,
            'error': 'Asset generation service not initialized'
        }), 500

    status = asset_generation_service.get_generation_status_overview()

    if 'error' in status:
        return jsonify({
            'success': False,
            'error': status['error']
        }), 500

    return jsonify({
        'success': True,
        'pending_batches': status['pending_batches'],
        'processing_batches': status['processing_batches'],
        'completed_batches': status['completed_batches'],
        'failed_batches': status['failed_batches'],
        'last_generation': status.get('last_generation'),
        'total_assets_generated': status['total_assets_generated']
    }), 200


@asset_generation_bp.route('/<uuid:integration_id>/asset-generation/history', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_integration_generation_history(integration_id: UUID):
    """
    Get asset generation history for an integration.

    Shows all batches with asset generation status, including:
    - Generation timestamps
    - Assets created/updated counts
    - Generation status
    - Error messages (if failed)

    Query params:
        limit: Maximum number of records (default: 50, max: 200)
        offset: Pagination offset (default: 0)

    Returns:
        200: List of batches with generation statistics
        404: Integration not found
        500: Service not initialized or server error
    """
    integration_service = current_app.integration_service
    asset_generation_service = current_app.asset_generation_trigger_service
    if not asset_generation_service:
        raise ValidationError('Asset generation service not initialized')

    # Verify integration exists
    integration = integration_service.get_integration(integration_id)
    if not integration:
        raise NotFoundError('Integration not found')

    # Get query params
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = int(request.args.get('offset', 0))

    history = asset_generation_service.get_integration_generation_history(
        integration_id,
        limit=limit,
        offset=offset
    )

    return jsonify({
        'success': True,
        'integration_id': str(integration_id),
        'integration_name': integration.name,
        'limit': limit,
        'offset': offset,
        'history': history
    }), 200


# =============================================================================
# Health & Diagnostics
# =============================================================================

@asset_generation_bp.route('/<uuid:integration_id>/health', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_integration_health(integration_id: UUID):
    """
    Get health status for an integration.

    Returns:
        200: Health status with last run info and issues
        404: Integration not found
    """
    integration_service = current_app.integration_service
    integration = integration_service.get_integration(integration_id)
    if not integration:
        raise NotFoundError('Integration not found')

    # Determine health status based on integration state
    status = 'healthy'
    if integration.status.value == 'error' or integration.last_error:
        status = 'error'
    # Note: Don't mark as warning if never synced - that's normal for new integrations

    return jsonify({
        'success': True,
        'status': status,
        'last_successful_run': integration.last_sync_at.isoformat() if integration.last_sync_at else None,
        'last_error': integration.last_error,
        'is_running': integration.is_running
    }), 200


@asset_generation_bp.route('/<uuid:integration_id>/generation-stats', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_integration_generation_stats(integration_id: UUID):
    """
    Get asset generation statistics for an integration.

    Returns:
        200: Statistics including batch counts and asset counts
        404: Integration not found
    """
    integration_service = current_app.integration_service
    integration = integration_service.get_integration(integration_id)
    if not integration:
        raise NotFoundError('Integration not found')

    # Get aggregated statistics from all batches
    stats = integration_service.batch_repository.get_batch_stats(integration_id)

    return jsonify({
        'success': True,
        'total_batches': stats['total_batches'],
        'total_records': stats['total_records'],
        'total_mapped': stats['total_mapped'],
        'total_assets_created': stats['total_assets_created'],
        'total_assets_updated': stats['total_assets_updated'],
        'total_assets': stats['total_assets_created'] + stats['total_assets_updated']
    }), 200


@asset_generation_bp.route('/<uuid:integration_id>/schedule/next-runs', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_schedule_next_runs(integration_id: UUID):
    """
    Calculate next scheduled run times for an integration.

    Query params:
        count: Number of runs to calculate (default: 5)

    Returns:
        200: List of next run times
        404: Integration not found or not scheduled
    """
    integration_service = current_app.integration_service
    integration = integration_service.get_integration(integration_id)
    if not integration:
        raise NotFoundError('Integration not found')

    if not integration.schedule_enabled or not integration.schedule_cron:
        raise NotFoundError('Integration does not have an active schedule')

    count = int(request.args.get('count', 5))

    # Calculate next runs using cron validator
    from src.modules.integrations.shared.cron_validator import calculate_next_runs

    next_runs = calculate_next_runs(
        integration.schedule_cron,
        integration.schedule_timezone,
        count
    )

    return jsonify({
        'success': True,
        'next_runs': [run.isoformat() for run in next_runs]
    }), 200


@asset_generation_bp.route('/<uuid:integration_id>/recent-errors', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_integration_recent_errors(integration_id: UUID):
    """
    Get recent error logs for an integration.

    Query params:
        limit: Number of errors to retrieve (default: 10)

    Returns:
        200: List of recent errors
        404: Integration not found
    """
    integration_service = current_app.integration_service
    integration = integration_service.get_integration(integration_id)
    if not integration:
        raise NotFoundError('Integration not found')

    errors = []

    # If there's a recent error, include it
    if integration.last_error:
        errors.append({
            'timestamp': integration.updated_at.isoformat() if integration.updated_at else None,
            'message': integration.last_error,
            'batch_id': None
        })

    return jsonify({
        'success': True,
        'errors': errors
    }), 200


