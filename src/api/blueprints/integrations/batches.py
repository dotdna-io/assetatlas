# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Batches Blueprint

Handles batch execution history, raw records, and data mapping operations.

Endpoints:
    GET    /api/integrations/stats                                  - Get global batch statistics
    GET    /api/integrations/<id>/batches                           - List batches
    GET    /api/integrations/<id>/batches/<batch_id>                - Get batch details
    GET    /api/integrations/<id>/batches/<batch_id>/records        - Get raw records
    DELETE /api/integrations/<id>/batches/<batch_id>                - Delete batch
    GET    /api/integrations/<id>/stats/batches                     - Get batch statistics
    POST   /api/integrations/<id>/batches/<batch_id>/map            - Trigger field mapping
    POST   /api/integrations/<id>/batches/<batch_id>/remap          - Remap batch
    GET    /api/integrations/<id>/mapping/stats                     - Get mapping statistics
    GET    /api/integrations/<id>/batches/<batch_id>/mapped-records - Get mapped records
    GET    /api/integrations/<id>/mapping-preview                    - Preview field mapping
"""

import logging
from flask_jwt_extended import jwt_required
from src.auth.middleware.rbac import require_admin
from flask import Blueprint, request, jsonify, current_app
from uuid import UUID

from src.api.middleware.error_handler import handle_errors, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

# Create blueprint
batches_bp = Blueprint('batches', __name__)



@batches_bp.route('/<uuid:integration_id>/batches', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def list_batches(integration_id: UUID):
    """
    List execution batches for an integration.

    Query params:
        limit: Maximum number of batches (default: 50, max: 200)
        offset: Pagination offset (default: 0)
        status: Filter by status (optional)

    Returns:
        200: Paginated list of batches
    """
    integration_service = current_app.integration_service
    # Verify integration exists
    integration = integration_service.get_integration(integration_id)
    if not integration:
        raise NotFoundError('Integration not found')

    # Get query params
    limit = min(int(request.args.get('limit', 50)), 200)  # Max 200
    offset = int(request.args.get('offset', 0))
    status = request.args.get('status')

    # Get batches
    batches = integration_service.batch_repository.list_batches(
        integration_id, limit, offset, status
    )

    # Get total count
    total = integration_service.batch_repository.count_batches(
        integration_id, status
    )

    # Calculate duration and transform field names for each batch
    for batch in batches:
        if batch.get('started_at') and batch.get('completed_at'):
            delta = batch['completed_at'] - batch['started_at']
            batch['duration_seconds'] = int(delta.total_seconds())
        else:
            batch['duration_seconds'] = None

        # Transform field names to match UI expectations
        batch['total_records'] = batch.get('raw_records_count', 0)
        batch['records_mapped'] = batch.get('mapped_records_count', 0)
        batch['assets_created'] = batch.get('assets_created_count', 0)

    return jsonify({
        'success': True,
        'total': total,
        'limit': limit,
        'offset': offset,
        'batches': batches
    }), 200


@batches_bp.route('/<uuid:integration_id>/batches/<uuid:batch_id>', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_batch(integration_id: UUID, batch_id: UUID):
    """
    Get detailed information about a specific batch.

    Returns:
        200: Batch details
        404: Batch not found
        403: Batch does not belong to this integration
    """
    integration_service = current_app.integration_service
    # Verify integration exists
    integration = integration_service.get_integration(integration_id)
    if not integration:
        raise NotFoundError('Integration not found')

    # Get batch
    batch = integration_service.batch_repository.get_batch(batch_id)
    if not batch:
        raise NotFoundError('Batch not found')

    # Verify batch belongs to integration
    if str(batch['integration_id']) != str(integration_id):
        from src.api.middleware.error_handler import AuthError
        raise AuthError('Batch does not belong to this integration')

    # Calculate duration
    if batch.get('started_at') and batch.get('completed_at'):
        delta = batch['completed_at'] - batch['started_at']
        batch['duration_seconds'] = int(delta.total_seconds())
    else:
        batch['duration_seconds'] = None

    # Add integration name
    batch['integration_name'] = integration.name

    return jsonify({
        'success': True,
        'batch': batch
    }), 200


@batches_bp.route('/<uuid:integration_id>/batches/<uuid:batch_id>/records', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_batch_records(integration_id: UUID, batch_id: UUID):
    """
    Get raw records from a batch.

    Query params:
        limit: Maximum number of records (default: 100, max: 500)
        offset: Pagination offset (default: 0)

    Returns:
        200: Paginated list of raw records
    """
    integration_service = current_app.integration_service
    # Verify integration exists
    integration = integration_service.get_integration(integration_id)
    if not integration:
        raise NotFoundError('Integration not found')

    # Verify batch exists and belongs to integration
    batch = integration_service.batch_repository.get_batch(batch_id)
    if not batch:
        raise NotFoundError('Batch not found')

    if str(batch['integration_id']) != str(integration_id):
        from src.api.middleware.error_handler import AuthError
        raise AuthError('Batch does not belong to this integration')

    # Get query params
    limit = min(int(request.args.get('limit', 100)), 500)  # Max 500
    offset = int(request.args.get('offset', 0))

    # Get records
    records = integration_service.raw_data_repository.get_records(
        batch_id, limit, offset
    )

    # Get total count
    total = integration_service.raw_data_repository.count_records(batch_id)

    return jsonify({
        'success': True,
        'total': total,
        'limit': limit,
        'offset': offset,
        'records': records
    }), 200


@batches_bp.route('/<uuid:integration_id>/batches/<uuid:batch_id>', methods=['DELETE'])
@jwt_required()
@require_admin()
@handle_errors
def delete_batch(integration_id: UUID, batch_id: UUID):
    """
    Delete a batch and all associated raw records.

    Returns:
        200: Batch deleted successfully
        404: Batch not found
        403: Batch does not belong to this integration
    """
    integration_service = current_app.integration_service
    # Verify integration exists
    integration = integration_service.get_integration(integration_id)
    if not integration:
        raise NotFoundError('Integration not found')

    # Verify batch exists and belongs to integration
    batch = integration_service.batch_repository.get_batch(batch_id)
    if not batch:
        raise NotFoundError('Batch not found')

    if str(batch['integration_id']) != str(integration_id):
        from src.api.middleware.error_handler import AuthError
        raise AuthError('Batch does not belong to this integration')

    # Delete batch (CASCADE will delete associated records)
    integration_service.batch_repository.delete_batch(batch_id)

    return jsonify({
        'success': True,
        'message': 'Batch deleted successfully'
    }), 200


@batches_bp.route('/batches/stats', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_global_batch_stats():
    """
    Get global batch execution statistics across all integrations.

    Returns:
        200: Global batch statistics
    """
    integration_service = current_app.integration_service

    # Get global batch stats (aggregate across all integrations)
    stats = integration_service.batch_repository.get_global_batch_stats()

    return jsonify({
        'success': True,
        **stats
    }), 200


@batches_bp.route('/<uuid:integration_id>/stats/batches', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_batch_stats(integration_id: UUID):
    """
    Get batch execution statistics for an integration.

    Returns:
        200: Batch statistics
        404: Integration not found
    """
    integration_service = current_app.integration_service
    # Verify integration exists
    integration = integration_service.get_integration(integration_id)
    if not integration:
        raise NotFoundError('Integration not found')

    # Get batch stats
    stats = integration_service.batch_repository.get_batch_stats(integration_id)

    # Get raw data stats
    raw_stats = integration_service.raw_data_repository.get_integration_stats(integration_id)

    # Combine stats
    combined_stats = {
        **stats,
        'raw_data': raw_stats
    }

    return jsonify({
        'success': True,
        'stats': combined_stats
    }), 200


# =============================================================================
# Data Mapping / Transformation
# =============================================================================

@batches_bp.route('/<uuid:integration_id>/batches/<uuid:batch_id>/map', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
def trigger_batch_mapping(integration_id: UUID, batch_id: UUID):
    """
    Trigger field mapping for a specific batch.

    Transforms raw_source_data → raw_asset_data using field_mapping configuration.

    Returns:
        200: Mapping result with counts
        404: Batch or integration not found
        409: Batch already mapped

    Note: This endpoint does not use @response_wrapper because it returns
    the service's result directly (which already has success/error structure).
    """
    integration_service = current_app.integration_service
    # Verify integration exists
    integration = integration_service.get_integration(integration_id)
    if not integration:
        return jsonify({
            'success': False,
            'error': 'Integration not found'
        }), 404

    # Verify batch exists and belongs to integration
    batch = integration_service.batch_repository.get_batch(batch_id)
    if not batch:
        return jsonify({
            'success': False,
            'error': 'Batch not found'
        }), 404

    if str(batch['integration_id']) != str(integration_id):
        return jsonify({
            'success': False,
            'error': 'Batch does not belong to this integration'
        }), 403

    # Trigger mapping
    result = integration_service.mapping_service.map_batch(batch_id)

    status_code = 200 if result['success'] else 400

    return jsonify(result), status_code


@batches_bp.route('/<uuid:integration_id>/batches/<uuid:batch_id>/remap', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
def remap_batch(integration_id: UUID, batch_id: UUID):
    """
    Delete existing mapped records and remap batch.

    Useful after updating field_mapping configuration.

    Returns:
        200: Remapping result
        404: Batch or integration not found

    Note: This endpoint does not use @response_wrapper because it returns
    the service's result directly (which already has success/error structure).
    """
    integration_service = current_app.integration_service
    # Verify integration exists
    integration = integration_service.get_integration(integration_id)
    if not integration:
        return jsonify({
            'success': False,
            'error': 'Integration not found'
        }), 404

    # Verify batch exists and belongs to integration
    batch = integration_service.batch_repository.get_batch(batch_id)
    if not batch:
        return jsonify({
            'success': False,
            'error': 'Batch not found'
        }), 404

    if str(batch['integration_id']) != str(integration_id):
        return jsonify({
            'success': False,
            'error': 'Batch does not belong to this integration'
        }), 403

    # Remap batch
    result = integration_service.mapping_service.remap_batch(batch_id)

    status_code = 200 if result['success'] else 400

    return jsonify(result), status_code


@batches_bp.route('/<uuid:integration_id>/mapping/stats', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_mapping_stats(integration_id: UUID):
    """
    Get mapping statistics for an integration.

    Returns:
        200: Mapping statistics including field coverage
        404: Integration not found
    """
    integration_service = current_app.integration_service
    # Verify integration exists
    integration = integration_service.get_integration(integration_id)
    if not integration:
        raise NotFoundError('Integration not found')

    # Get mapping stats
    stats = integration_service.mapping_service.get_mapping_stats(integration_id)

    return jsonify({
        'success': True,
        'stats': stats
    }), 200


@batches_bp.route('/<uuid:integration_id>/batches/<uuid:batch_id>/mapped-records', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_mapped_records(integration_id: UUID, batch_id: UUID):
    """
    Get mapped records (raw_asset_data) for a batch.

    Query params:
        limit: Maximum number of records (default: 100, max: 500)
        offset: Pagination offset (default: 0)

    Returns:
        200: Paginated list of mapped records
        404: Batch or integration not found
    """
    integration_service = current_app.integration_service
    # Verify integration exists
    integration = integration_service.get_integration(integration_id)
    if not integration:
        raise NotFoundError('Integration not found')

    # Verify batch exists and belongs to integration
    batch = integration_service.batch_repository.get_batch(batch_id)
    if not batch:
        raise NotFoundError('Batch not found')

    if str(batch['integration_id']) != str(integration_id):
        from src.api.middleware.error_handler import AuthError
        raise AuthError('Batch does not belong to this integration')

    # Get query params
    limit = min(int(request.args.get('limit', 100)), 500)  # Max 500
    offset = int(request.args.get('offset', 0))

    # Get mapped records from raw_asset_repository
    from src.modules.integrations.repositories.raw_asset_repository import RawAssetRepository
    raw_asset_repo = RawAssetRepository(current_app.connection_manager)

    records = raw_asset_repo.get_by_batch(batch_id, limit, offset)
    total = raw_asset_repo.count_by_batch(batch_id)

    return jsonify({
        'success': True,
        'total': total,
        'limit': limit,
        'offset': offset,
        'records': records
    }), 200


@batches_bp.route('/<uuid:integration_id>/raw-data', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_raw_data(integration_id: UUID):
    """
    Get raw records from the latest completed batch with field schema.

    Query params:
        limit: Maximum number of records (default: 50, max: 500)
        offset: Pagination offset (default: 0)
        include_schema: Whether to compute field schema (default: true, skip on pagination)

    Returns:
        200: Records with field schema, or empty result if no batches exist
    """
    integration_service = current_app.integration_service
    integration = integration_service.get_integration(integration_id)
    if not integration:
        raise NotFoundError('Integration not found')

    limit = min(int(request.args.get('limit', 50)), 500)
    offset = int(request.args.get('offset', 0))
    include_schema = request.args.get('include_schema', 'true').lower() != 'false'

    batch = integration_service.batch_repository.get_latest_completed_batch(
        integration_id
    )

    if not batch:
        return jsonify({
            'success': True,
            'integration_id': str(integration_id),
            'batch_id': None,
            'batch_completed_at': None,
            'total_records': 0,
            'records': [],
            'field_schema': [],
            'limit': limit,
            'offset': offset,
        }), 200

    batch_id = batch['id']
    records = integration_service.raw_data_repository.get_records(
        batch_id, limit, offset
    )
    total = integration_service.raw_data_repository.count_records(batch_id)

    # Compute field schema from a sample of records (skip on pagination)
    field_schema = []
    if include_schema and total > 0:
        from src.modules.integrations.services.field_schema import (
            compute_field_schema,
        )
        schema_records = integration_service.raw_data_repository.get_records(
            batch_id, 200, 0
        )
        field_schema = compute_field_schema(schema_records)

    return jsonify({
        'success': True,
        'integration_id': str(integration_id),
        'batch_id': str(batch_id),
        'batch_completed_at': batch.get('completed_at'),
        'total_records': total,
        'records': records,
        'field_schema': field_schema,
        'limit': limit,
        'offset': offset,
    }), 200


@batches_bp.route('/<uuid:integration_id>/mapping-preview', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_mapping_preview(integration_id: UUID):
    """
    Preview field mapping results for the latest batch.

    Shows how raw events map to AssetAtlas fields and which records
    would be dropped by the asset generation pipeline.

    Query params:
        limit: Maximum records per page (default: 50, max: 500)
        offset: Pagination offset (default: 0)
        status: Mapping-status filter — 'all' (default), 'ok', or 'dropped'.
            Filtering happens server-side across the whole batch so the
            requested page reflects the selected status, not just the
            records that happen to be on the current raw page.

    Returns:
        200: Mapping preview with mapped records and drop status
    """
    limit = min(request.args.get('limit', 50, type=int), 500)
    offset = max(request.args.get('offset', 0, type=int), 0)
    status = request.args.get('status', 'all')
    if status not in ('all', 'ok', 'dropped'):
        status = 'all'

    result = current_app.mapping_preview_service.get_mapping_preview(
        str(integration_id), limit=limit, offset=offset, status=status
    )
    return jsonify(result)
