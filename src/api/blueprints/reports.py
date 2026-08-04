# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Reports Blueprint

Handles all reporting dashboard endpoints for system-wide report management.

Endpoints (7 total):
- GET    /api/reports              - List all system-wide reports
- GET    /api/reports/default      - Get default report
- GET    /api/reports/<id>          - Get specific report
- POST   /api/reports              - Create new report
- PUT    /api/reports/<id>          - Update existing report
- DELETE /api/reports/<id>          - Delete report (except default)
- POST   /api/reports/<id>/clone   - Clone an existing report
"""

import logging
from flask import Blueprint, request, current_app, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from src.api.middleware.error_handler import handle_errors, NotFoundError, ValidationError
from src.api.utils.responses import response_wrapper
from src.auth.middleware.rbac import require_admin
from src.api.utils.validation import validate_query, validate_request
from src.api.models.requests import (
    ReportCreateRequest,
    ReportUpdateRequest,
    ReportListParams
)

logger = logging.getLogger(__name__)

# Create blueprint
reports_bp = Blueprint('reports', __name__, url_prefix='/api')


# =============================================================================
# Report Endpoints (System-Wide)
# =============================================================================

@reports_bp.route('/reports', methods=['GET'])
@jwt_required()
@handle_errors
@validate_query(ReportListParams)
@response_wrapper
def list_reports(query_params: ReportListParams):
    """
    List all system-wide reports.
    ---
    tags:
      - Reports
    summary: List reports
    description: |
      Retrieves a list of all system-wide reports.

      All reports are accessible to all authenticated users.
      Supports pagination and search.
    parameters:
      - name: page
        in: query
        type: integer
        required: false
        default: 1
        description: Page number (1-indexed)
      - name: limit
        in: query
        type: integer
        required: false
        default: 50
        description: Results per page (max 200)
      - name: search
        in: query
        type: string
        required: false
        description: Search in title and description
    responses:
      200:
        description: List of reports
        schema:
          type: object
          properties:
            reports:
              type: array
              items:
                type: object
            total:
              type: integer
            page:
              type: integer
            limit:
              type: integer
            has_more:
              type: boolean
      500:
        description: Internal server error
    """
    reports_service = current_app.reports_service

    result = reports_service.list_reports(query_params)

    return result.model_dump(mode='json')


@reports_bp.route('/reports/default', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def get_default_report():
    """
    Get the default report.
    ---
    tags:
      - Reports
    summary: Get default report
    description: |
      Retrieves the default report that is shown when users first open the reporting dashboard.

      The default report is a special report that:
      - Cannot be deleted
      - Is shown on initial page load
      - Is accessible to all authenticated users
    responses:
      200:
        description: Default report details
        schema:
          type: object
          properties:
            id:
              type: string
            title:
              type: string
            description:
              type: string
            charts:
              type: array
            is_default:
              type: boolean
              example: true
            created_at:
              type: string
              format: date-time
            updated_at:
              type: string
              format: date-time
      404:
        description: Default report not found (should never happen)
      500:
        description: Internal server error
    """
    reports_service = current_app.reports_service

    report = reports_service.get_default_report()

    if not report:
        raise NotFoundError('Default report not found', details={'message': 'Please contact system administrator'})

    return report.model_dump(mode='json')


@reports_bp.route('/reports/<report_id>', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def get_report(report_id: str):
    """
    Get specific report details.
    ---
    tags:
      - Reports
    summary: Get report details
    description: |
      Retrieves detailed information for a specific system-wide report including:
      - Report metadata (title, description, timestamps, audit info)
      - Full chart configurations array

      All reports are accessible to all authenticated users.
    parameters:
      - name: report_id
        in: path
        type: string
        required: true
        description: Report UUID
    responses:
      200:
        description: Report details
        schema:
          type: object
          properties:
            id:
              type: string
            title:
              type: string
            description:
              type: string
            charts:
              type: array
            created_at:
              type: string
              format: date-time
            updated_at:
              type: string
              format: date-time
            created_by:
              type: string
              description: User UUID who created the report
            updated_by:
              type: string
              description: User UUID who last updated the report
      404:
        description: Report not found
      500:
        description: Internal server error
    """
    reports_service = current_app.reports_service

    report = reports_service.get_report(report_id)

    if not report:
        raise NotFoundError(f'Report {report_id} not found', details={'report_id': report_id})

    return report.model_dump(mode='json')


@reports_bp.route('/reports', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(ReportCreateRequest, param_name='validated_data')
@response_wrapper
def create_report(validated_data: ReportCreateRequest):
    """
    Create a new system-wide report.
    ---
    tags:
      - Reports
    summary: Create report
    description: |
      Creates a new system-wide report from the provided chart configurations.

      The report will be accessible to all users.
      Charts array can be empty (report will start with no charts).
      When charts are added, each chart must contain:
      - chartId (chart type UUID)
      - name (chart name)
      - Additional configuration (size, position, queryConfig, etc.)
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - title
            - charts
          properties:
            title:
              type: string
              minLength: 1
              maxLength: 255
              description: Report title
            description:
              type: string
              description: Report description (optional)
            charts:
              type: array
              description: Array of chart configurations
              items:
                type: object
    responses:
      200:
        description: Report created successfully
        schema:
          type: object
          properties:
            id:
              type: string
            title:
              type: string
            charts:
              type: array
      400:
        description: Invalid request body
      500:
        description: Internal server error
    """
    user_id = get_jwt_identity()  # Used for audit trail only
    reports_service = current_app.reports_service

    report = reports_service.create_report(validated_data, created_by=user_id)

    return report.model_dump(mode='json')


@reports_bp.route('/reports/<report_id>', methods=['PUT'])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(ReportUpdateRequest, param_name='validated_data')
@response_wrapper
def update_report(report_id: str, validated_data: ReportUpdateRequest):
    """
    Update an existing system-wide report.
    ---
    tags:
      - Reports
    summary: Update report
    description: |
      Updates an existing system-wide report.

      All fields are optional - only provided fields will be updated.
      Any authenticated user can update any report.
    parameters:
      - name: report_id
        in: path
        type: string
        required: true
        description: Report UUID
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            title:
              type: string
              minLength: 1
              maxLength: 255
            description:
              type: string
            charts:
              type: array
    responses:
      200:
        description: Report updated successfully
      404:
        description: Report not found
      400:
        description: Invalid request body
      500:
        description: Internal server error
    """
    user_id = get_jwt_identity()  # Used for audit trail only
    reports_service = current_app.reports_service

    report = reports_service.update_report(report_id, validated_data, updated_by=user_id)

    if not report:
        raise NotFoundError(
            f'Report {report_id} not found',
            details={'report_id': report_id}
        )

    return report.model_dump(mode='json')


@reports_bp.route('/reports/<report_id>', methods=['DELETE'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def delete_report(report_id: str):
    """
    Delete a system-wide report.
    ---
    tags:
      - Reports
    summary: Delete report
    description: |
      Deletes a system-wide report.
      Any authenticated user can delete any report except the default report.

      The default report cannot be deleted to ensure there's always a starting point.
    parameters:
      - name: report_id
        in: path
        type: string
        required: true
        description: Report UUID
    responses:
      200:
        description: Report deleted successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            message:
              type: string
      400:
        description: Cannot delete default report
      404:
        description: Report not found
      500:
        description: Internal server error
    """
    reports_service = current_app.reports_service

    try:
        success = reports_service.delete_report(report_id)

        if not success:
            raise NotFoundError(
                f'Report {report_id} not found',
                details={'report_id': report_id}
            )

        return {
            'success': True,
            'message': f'Report {report_id} deleted successfully'
        }
    except ValueError as e:
        # Raised when trying to delete default report
        raise ValidationError(str(e), details={'report_id': report_id})


@reports_bp.route('/reports/<report_id>/clone', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def clone_report(report_id: str):
    """Clone an existing report."""
    user_id = get_jwt_identity()
    result = current_app.reports_service.clone_report(report_id, created_by=user_id)
    if result is None:
        raise NotFoundError("Report not found")
    return result.model_dump(mode='json'), 200
