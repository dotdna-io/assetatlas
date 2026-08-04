# SPDX-License-Identifier: AGPL-3.0-or-later
"""Alert delivery channel + per-category routing admin API (#917, #918).

Channel config secrets are encrypted at rest and masked in responses; this
blueprint never handles ciphertext directly — the service returns what's
already safe to expose.

Endpoints (all admin-only):
- GET/POST   /api/alerts/channels               - list / create channels
- GET/PUT/DELETE /api/alerts/channels/<id>       - get / update / delete a channel
- POST       /api/alerts/channels/<id>/test      - synchronous send-test
- GET        /api/alerts/channels/types          - list available channel types
- GET        /api/alerts/routing                 - read routing for all categories
- PUT        /api/alerts/routing/<category>      - set a category's routing
"""
import logging
from typing import Any, Dict, List, Literal

from flask import Blueprint, current_app
from flask_jwt_extended import jwt_required, current_user
from psycopg2 import DataError, IntegrityError
from pydantic import BaseModel, Field

from src.api.middleware.error_handler import handle_errors, NotFoundError, ValidationError
from src.api.utils.responses import response_wrapper, success_response
from src.api.utils.validation import validate_request
from src.auth.middleware.rbac import require_admin
from src.modules.alert_delivery.channels import registry
from src.modules.alert_delivery.service import ROUTING_CATEGORIES

logger = logging.getLogger(__name__)

alert_channels_bp = Blueprint('alert_channels', __name__, url_prefix='/api/alerts')


class ChannelCreate(BaseModel):
    type: str = Field(min_length=1)
    name: str = Field(min_length=1)
    config: Dict[str, Any] = Field(default_factory=dict)


class ChannelUpdate(BaseModel):
    name: str = Field(min_length=1)
    enabled: bool = True
    config: Dict[str, Any] = Field(default_factory=dict)


class RouteItem(BaseModel):
    channel_id: str
    # Constrained so a typo ("crticial") is rejected at validation time rather than
    # silently falling through to SEVERITY_ORDER.get(..., 0) = lowest threshold, which
    # would route every severity to a channel the admin meant to gate on critical-only.
    min_severity: Literal['low', 'medium', 'high', 'critical'] = 'low'


class CategoryRoutes(BaseModel):
    channels: List[RouteItem] = Field(default_factory=list)


@alert_channels_bp.route('/channels', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def list_channels():
    # response_wrapper only auto-wraps dict/tuple returns; a bare list would pass
    # through unwrapped (no {success, data} envelope), so wrap explicitly here.
    return success_response(current_app.alert_delivery_service.list_channels())


@alert_channels_bp.route('/channels', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(ChannelCreate)
@response_wrapper
def create_channel(validated_data: ChannelCreate):
    try:
        result = current_app.alert_delivery_service.create_channel(
            validated_data.type, validated_data.name, validated_data.config,
            created_by=current_user.get('id'))
    except registry.UnknownChannelType:
        raise ValidationError(f"Unknown channel type '{validated_data.type}'")
    except ValueError as e:
        raise ValidationError(str(e))
    except IntegrityError:
        # alert_channels.name is UNIQUE — surface a clean 400 instead of a 500.
        raise ValidationError(f"A channel named '{validated_data.name}' already exists")
    return result, 201


@alert_channels_bp.route('/channels/types', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def list_channel_types():
    return success_response([
        {'type': t, 'label': t.replace('_', ' ').title()}
        for t in registry.available_types() if registry.get(t).user_creatable])


@alert_channels_bp.route('/channels/<channel_id>', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_channel(channel_id):
    channel = current_app.alert_delivery_service.get_channel(channel_id)
    if channel is None:
        raise NotFoundError(f"Channel {channel_id} not found")
    return channel


@alert_channels_bp.route('/channels/<channel_id>', methods=['PUT'])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(ChannelUpdate)
@response_wrapper
def update_channel(channel_id, validated_data: ChannelUpdate):
    try:
        result = current_app.alert_delivery_service.update_channel(
            channel_id, validated_data.name, validated_data.enabled, validated_data.config)
    except ValueError as e:
        raise ValidationError(str(e))
    except IntegrityError:
        # alert_channels.name is UNIQUE — surface a clean 400 instead of a 500.
        raise ValidationError(f"A channel named '{validated_data.name}' already exists")
    if result is None:
        raise NotFoundError(f"Channel {channel_id} not found")
    return result


@alert_channels_bp.route('/channels/<channel_id>', methods=['DELETE'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def delete_channel(channel_id):
    if not current_app.alert_delivery_service.delete_channel(channel_id):
        raise NotFoundError(f"Channel {channel_id} not found")
    return {'deleted': True}


@alert_channels_bp.route('/channels/<channel_id>/test', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def test_channel(channel_id):
    result = current_app.alert_delivery_service.send_test(channel_id)
    return {'ok': result.ok, 'response_code': result.response_code, 'error': result.error}


@alert_channels_bp.route('/routing', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_routing():
    return success_response(current_app.alert_delivery_service.get_all_routes())


@alert_channels_bp.route('/routing/<category>', methods=['PUT'])
@jwt_required()
@require_admin()
@handle_errors
@validate_request(CategoryRoutes)
@response_wrapper
def set_routing(category, validated_data: CategoryRoutes):
    if category not in ROUTING_CATEGORIES:
        raise ValidationError(f"Unknown category '{category}'")
    try:
        current_app.alert_delivery_service.set_routes_for_category(
            category, [r.model_dump() for r in validated_data.channels])
    except (IntegrityError, DataError):
        # A routed channel_id no longer exists (FK violation) or isn't a valid UUID
        # (the ::uuid cast raises DataError, not IntegrityError). Clean 400 instead
        # of a 500; the atomic replace has rolled back so the category's previous
        # routing is preserved.
        raise ValidationError("One or more channels are invalid or no longer exist")
    return {'updated': True}
