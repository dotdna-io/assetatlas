# SPDX-License-Identifier: AGPL-3.0-or-later
"""API key management endpoints (admin-only, JWT-authenticated)."""

import logging
from flask import Blueprint, request, current_app
from flask_jwt_extended import jwt_required, get_jwt

from src.api.middleware.error_handler import handle_errors, ValidationError, NotFoundError
from src.api.utils.responses import response_wrapper
from src.auth.middleware.rbac import require_admin
from src.modules.api_keys.models import ApiKeyCreate, ApiKeyUpdate

logger = logging.getLogger(__name__)

api_keys_bp = Blueprint('api_keys', __name__, url_prefix='/api/api-keys')


@api_keys_bp.route('', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def create_api_key():
    """Create a new API key. Returns the full key once."""
    data = request.get_json()
    if not data:
        raise ValidationError('Request body is required')

    try:
        params = ApiKeyCreate(**data)
    except Exception as e:
        raise ValidationError(str(e))

    claims = get_jwt()
    user_id = claims.get('sub', '')

    service = current_app.api_key_service
    result = service.create_key(
        user_id=user_id,
        name=params.name,
        expires_at=params.expires_at,
    )
    return result, 201


@api_keys_bp.route('', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def list_api_keys():
    """List all API keys (never exposes the key itself)."""
    service = current_app.api_key_service
    keys = service.list_keys()
    return {'api_keys': keys, 'total': len(keys)}


@api_keys_bp.route('/<key_id>', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_api_key(key_id):
    """Get a single API key's details."""
    service = current_app.api_key_service
    key = service.get_key(key_id)
    if key is None:
        raise NotFoundError(f'API key {key_id} not found')
    return key


@api_keys_bp.route('/<key_id>', methods=['PATCH'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def update_api_key(key_id):
    """Update an API key's name or active status."""
    data = request.get_json()
    if not data:
        raise ValidationError('Request body is required')

    try:
        params = ApiKeyUpdate(**data)
    except Exception as e:
        raise ValidationError(str(e))

    service = current_app.api_key_service
    result = service.update_key(
        key_id=key_id,
        name=params.name,
        is_active=params.is_active,
    )
    if result is None:
        raise NotFoundError(f'API key {key_id} not found')
    return result


@api_keys_bp.route('/<key_id>', methods=['DELETE'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def delete_api_key(key_id):
    """Permanently delete an API key."""
    service = current_app.api_key_service
    deleted = service.delete_key(key_id)
    if not deleted:
        raise NotFoundError(f'API key {key_id} not found')
    return {'message': f'API key {key_id} deleted'}
