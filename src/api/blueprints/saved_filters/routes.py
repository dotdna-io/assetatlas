# SPDX-License-Identifier: AGPL-3.0-or-later
"""Saved Explorer filters — REST routes (per-user CRUD)."""
from __future__ import annotations

from flask import Blueprint, current_app
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from src.api.middleware.error_handler import NotFoundError, handle_errors
from src.api.utils.responses import response_wrapper
from src.api.utils.validation import validate_request

from .models import SavedFilterCreate, SavedFilterUpdate

saved_filters_bp = Blueprint("saved_filters", __name__, url_prefix="/api/saved-filters")


def _is_admin() -> bool:
    role = (get_jwt() or {}).get("role", "user")
    return role in {"admin", "super_admin"}


@saved_filters_bp.route("", methods=["POST"])
@jwt_required()
@handle_errors
@validate_request(SavedFilterCreate)
@response_wrapper
def create_saved_filter(validated_data: SavedFilterCreate):
    """Create a saved filter owned by the authenticated user."""
    user_id = get_jwt_identity()
    svc = current_app.saved_filters_service
    row = svc.create(owner_id=user_id, is_admin=_is_admin(), data=validated_data)
    return svc.to_dict(row, viewer_id=user_id)


@saved_filters_bp.route("", methods=["GET"])
@jwt_required()
@handle_errors
@response_wrapper
def list_saved_filters():
    """List saved filters visible to the user, grouped as mine/shared_with_me/library."""
    user_id = get_jwt_identity()
    svc = current_app.saved_filters_service
    grouped = svc.list_for_user(user_id=user_id)
    return {k: [svc.to_dict(r, viewer_id=user_id) for r in v] for k, v in grouped.items()}


@saved_filters_bp.route("/<filter_id>", methods=["GET"])
@jwt_required()
@handle_errors
@response_wrapper
def get_saved_filter(filter_id: str):
    """Return one saved filter if the user may access it."""
    user_id = get_jwt_identity()
    svc = current_app.saved_filters_service
    row = svc.get(filter_id, user_id=user_id)
    if row is None:
        raise NotFoundError("Saved filter not found")
    return svc.to_dict(row, viewer_id=user_id)


@saved_filters_bp.route("/<filter_id>", methods=["PUT"])
@jwt_required()
@handle_errors
@validate_request(SavedFilterUpdate)
@response_wrapper
def update_saved_filter(filter_id: str, validated_data: SavedFilterUpdate):
    """Update a saved filter owned by the user."""
    user_id = get_jwt_identity()
    svc = current_app.saved_filters_service
    row = svc.update(filter_id, owner_id=user_id, is_admin=_is_admin(),
                     data=validated_data)
    if row is None:
        raise NotFoundError("Saved filter not found")
    return svc.to_dict(row, viewer_id=user_id)


@saved_filters_bp.route("/<filter_id>", methods=["DELETE"])
@jwt_required()
@handle_errors
@response_wrapper
def delete_saved_filter(filter_id: str):
    """Soft-delete a saved filter owned by the user."""
    user_id = get_jwt_identity()
    ok = current_app.saved_filters_service.delete(filter_id, owner_id=user_id)
    if not ok:
        raise NotFoundError("Saved filter not found")
    return {"ok": True}
