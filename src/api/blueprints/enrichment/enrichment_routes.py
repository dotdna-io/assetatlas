# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Enrichment CSV Upload API Endpoints

Provides endpoints for managing enrichment CSV datasets: upload, list,
get, delete, status update, preview, and apply.

Endpoints:
- GET    /api/enrichment/datasets                      — List all datasets
- GET    /api/enrichment/datasets/<dataset_id>         — Get dataset details
- DELETE /api/enrichment/datasets/<dataset_id>         — Delete a dataset (admin)
- PUT    /api/enrichment/datasets/<dataset_id>/status  — Update dataset status (admin)
- POST   /api/enrichment/upload                        — Upload a CSV dataset (admin)
- POST   /api/enrichment/datasets/<dataset_id>/preview — Preview dataset matches (admin)
- POST   /api/enrichment/datasets/<dataset_id>/apply   — Apply dataset to assets (admin)
"""

import logging
from flask import Blueprint, request, current_app
from flask_jwt_extended import jwt_required

from src.api.middleware.error_handler import handle_errors, NotFoundError, ValidationError
from src.api.utils.responses import response_wrapper
from src.api.utils.validation import validate_request
from src.auth.middleware.rbac import require_admin
from src.api.utils.audit_logger import AuditLogger, get_current_user_context
from src.api.utils.audit_vocab import AuditAction, AuditResource
from src.modules.enrichment.domain.enrichment_models import (
    EnrichmentDatasetCreate,
    EnrichmentDatasetStatusUpdate,
)

logger = logging.getLogger(__name__)

enrichment_bp = Blueprint("enrichment", __name__, url_prefix="/api/enrichment")


@enrichment_bp.route("/ext-fields", methods=["GET"])
@jwt_required()
@handle_errors
@response_wrapper
def list_ext_fields():
    """List available extended_attributes source/key paths for matching."""
    fields = current_app.enrichment_service.list_ext_fields()
    return {"fields": fields}


@enrichment_bp.route("/datasets", methods=["GET"])
@jwt_required()
@handle_errors
@response_wrapper
def list_datasets():
    """List all enrichment datasets."""
    datasets = current_app.enrichment_service.list_datasets()
    return {"datasets": datasets}


@enrichment_bp.route("/datasets/<dataset_id>", methods=["GET"])
@jwt_required()
@handle_errors
@response_wrapper
def get_dataset(dataset_id):
    """Get a single enrichment dataset with sample rows."""
    dataset = current_app.enrichment_service.get_dataset(dataset_id)
    if dataset is None:
        raise NotFoundError(f"Dataset {dataset_id} not found")
    return {"dataset": dataset}


@enrichment_bp.route("/datasets/<dataset_id>", methods=["DELETE"])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def delete_dataset(dataset_id):
    """Delete an enrichment dataset (admin only)."""
    result = current_app.enrichment_service.delete_dataset(dataset_id)
    if not result:
        raise NotFoundError(f"Dataset {dataset_id} not found")

    user_id, username = get_current_user_context()
    AuditLogger.log_data_operation(
        resource=AuditResource.ENRICHMENT_DATASET,
        resource_id=dataset_id,
        action=AuditAction.DELETE,
        user_id=user_id,
        username=username,
    )

    return {"message": "Dataset deleted"}


@enrichment_bp.route("/datasets/<dataset_id>/status", methods=["PUT"])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(EnrichmentDatasetStatusUpdate)
@response_wrapper
def update_dataset_status(dataset_id, validated_data=None):
    """Update enrichment dataset status (admin only)."""
    result = current_app.enrichment_service.update_status(
        dataset_id, validated_data.status
    )
    if not result:
        raise NotFoundError(f"Dataset {dataset_id} not found")

    user_id, username = get_current_user_context()
    AuditLogger.log_data_operation(
        resource=AuditResource.ENRICHMENT_DATASET,
        resource_id=dataset_id,
        action=AuditAction.UPDATE,
        user_id=user_id,
        username=username,
        details={"status": validated_data.status},
    )

    return {"message": f"Dataset status updated to {validated_data.status}"}


@enrichment_bp.route("/upload", methods=["POST"])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def upload_dataset():
    """Upload a CSV enrichment dataset (admin only)."""
    file = request.files.get("file")
    if not file:
        raise ValidationError("File is required")

    name = request.form.get("name")
    match_field = request.form.get("match_field")
    match_type = request.form.get("match_type", "exact")
    target_field = request.form.get("target_field")
    match_col = request.form.get("match_col")
    enrich_col = request.form.get("enrich_col")

    # Validate required form fields
    missing = []
    if not name:
        missing.append("name")
    if not match_field:
        missing.append("match_field")
    if not target_field:
        missing.append("target_field")
    if not match_col:
        missing.append("match_col")
    if not enrich_col:
        missing.append("enrich_col")
    if missing:
        raise ValidationError(
            f"Missing required fields: {', '.join(missing)}"
        )

    # Validate via Pydantic model
    EnrichmentDatasetCreate(
        name=name,
        match_field=match_field,
        match_type=match_type,
        target_field=target_field,
    )

    user_id, username = get_current_user_context()

    result = current_app.enrichment_service.upload_csv(
        file=file,
        name=name,
        match_field=match_field,
        match_type=match_type,
        target_field=target_field,
        match_col=match_col,
        enrich_col=enrich_col,
        uploaded_by=username,
    )

    if "error" in result:
        raise ValidationError(result["error"])

    AuditLogger.log_data_operation(
        resource=AuditResource.ENRICHMENT_DATASET,
        resource_id=result.get("dataset_id", "unknown"),
        action=AuditAction.CREATE,
        user_id=user_id,
        username=username,
        details={"name": name, "row_count": result.get("row_count")},
    )

    return result, 201


@enrichment_bp.route("/datasets/<dataset_id>/preview", methods=["POST"])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def preview_dataset(dataset_id):
    """Preview how a dataset would match against current assets (admin only)."""
    result = current_app.enrichment_service.preview_dataset(dataset_id)
    if result is None:
        raise NotFoundError(f"Dataset {dataset_id} not found")
    return result


@enrichment_bp.route("/datasets/<dataset_id>/apply", methods=["POST"])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def apply_dataset(dataset_id):
    """Apply an enrichment dataset to matching assets (admin only)."""
    result = current_app.enrichment_service.apply_dataset(dataset_id)
    if result is None:
        raise NotFoundError(f"Dataset {dataset_id} not found")
    if "error" in result:
        # Dataset exists but its match_field no longer resolves to a live
        # catalog field — surface the real reason, not a misleading 404.
        raise ValidationError(result["error"])

    user_id, username = get_current_user_context()
    AuditLogger.log_data_operation(
        resource=AuditResource.ENRICHMENT_DATASET,
        resource_id=dataset_id,
        action=AuditAction.APPLY,
        user_id=user_id,
        username=username,
        details={"matched_count": result.get("matched_count")},
    )

    return result
