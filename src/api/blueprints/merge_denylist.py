# SPDX-License-Identifier: AGPL-3.0-or-later
"""Admin CRUD for the identifier denylist (#973)."""

from flask import Blueprint, current_app

from src.api.middleware.error_handler import (
    handle_errors,
    ValidationError,
    NotFoundError,
)
from src.api.utils.responses import response_wrapper
from src.api.utils.validation import ensure_uuid, validate_request
from src.auth.middleware.rbac import require_admin
from flask_jwt_extended import jwt_required, get_jwt_identity

from src.modules.merge_denylist.models import DenylistEntryCreate

merge_denylist_bp = Blueprint(
    "merge_denylist", __name__, url_prefix="/api/merge-denylist"
)


@merge_denylist_bp.route("", methods=["GET"])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def list_denylist():
    return {"entries": current_app.merge_denylist_service.list_entries()}


@merge_denylist_bp.route("", methods=["POST"])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(DenylistEntryCreate)
@response_wrapper
def add_denylist(validated_data: DenylistEntryCreate):
    try:
        return current_app.merge_denylist_service.add_entry(
            identifier_type=validated_data.identifier_type,
            match_type=validated_data.match_type,
            raw_value=validated_data.raw_value,
            range_start=validated_data.range_start,
            range_end=validated_data.range_end,
            reason=validated_data.reason,
            created_by=get_jwt_identity(),
        )
    except ValueError as exc:
        raise ValidationError(str(exc))


@merge_denylist_bp.route("/<entry_id>", methods=["DELETE"])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def delete_denylist(entry_id: str):
    if not current_app.merge_denylist_service.remove_entry(
        ensure_uuid(entry_id, "denylist entry")
    ):
        raise NotFoundError("Denylist entry not found")
    return {"ok": True}
