# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unified Fields API (Settings -> Data). Admin for writes; readable by any
authenticated user. Routes live at /api/fields/unified to match the frontend
contract (separate from the settings field-registry blueprint)."""

from flask import Blueprint, current_app
from flask_jwt_extended import jwt_required

from src.api.middleware.error_handler import handle_errors
from src.api.utils.responses import response_wrapper
from src.api.utils.validation import validate_request
from src.auth.middleware.rbac import require_admin
from src.modules.fields.unified.models import UnifiedFieldWrite, UnifiedFieldPreviewRequest

unified_fields_bp = Blueprint("unified_fields", __name__, url_prefix="/api/fields")


@unified_fields_bp.route("/unified", methods=["GET"])
@jwt_required()
@handle_errors
@response_wrapper
def list_unified_fields():
    return {"success": True, "fields": current_app.unified_fields_service.list()}


@unified_fields_bp.route("/unified", methods=["POST"])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(UnifiedFieldWrite)
@response_wrapper
def create_unified_field(validated_data: UnifiedFieldWrite):
    field = current_app.unified_fields_service.create(validated_data)
    return {"success": True, "field": field}, 201


@unified_fields_bp.route("/unified/<field_id>", methods=["GET"])
@jwt_required()
@handle_errors
@response_wrapper
def get_unified_field(field_id: str):
    return {"success": True, "field": current_app.unified_fields_service.get(field_id)}


@unified_fields_bp.route("/unified/<field_id>", methods=["PUT"])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(UnifiedFieldWrite)
@response_wrapper
def update_unified_field(field_id: str, validated_data: UnifiedFieldWrite):
    field = current_app.unified_fields_service.update(field_id, validated_data)
    return {"success": True, "field": field}


@unified_fields_bp.route("/unified/<field_id>", methods=["DELETE"])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def delete_unified_field(field_id: str):
    current_app.unified_fields_service.delete(field_id)
    return {"success": True, "message": "Unified field deleted"}


@unified_fields_bp.route("/unified/preview", methods=["POST"])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(UnifiedFieldPreviewRequest)
@response_wrapper
def preview_unified_field(validated_data: UnifiedFieldPreviewRequest):
    result = current_app.unified_fields_service.preview(validated_data)
    return {"success": True, "data_type": result["data_type"], "rows": result["rows"]}
