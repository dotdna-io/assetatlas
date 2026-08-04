# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Connectors Blueprint

Handles connector discovery and schema retrieval for available integration types.

Endpoints:
    GET /api/integrations/available                   - List available connector types
    GET /api/integrations/available/<type>/schema     - Get connector config schema
"""

import logging
from flask import Blueprint, jsonify, current_app
from flask_jwt_extended import jwt_required

from src.api.middleware.error_handler import handle_errors
from src.api.utils.caching import cached_connector_discovery
from src.auth.middleware.rbac import require_admin

logger = logging.getLogger(__name__)

# Create blueprint
connectors_bp = Blueprint('connectors', __name__)


@connectors_bp.route('/available', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@cached_connector_discovery(timeout=300)  # Cache for 5 minutes
def list_available_connectors():
    """
    List all available connector types.

    Returns:
        200: List of connector metadata with count

    Response:
        {
            "success": True,
            "count": 5,
            "connectors": [
                {
                    "integration_type": "azure_ad",
                    "display_name": "Microsoft Entra ID",
                    "category": "Identity Management",
                    "description": "Collect device information from Microsoft Entra ID",
                    "logo_url": "/svg/azure-ad-logo.svg",
                    "config_schema": {...}
                }
            ]
        }
    """
    integration_service = current_app.integration_service
    connectors = integration_service.get_available_connectors()

    return jsonify({
        'success': True,
        'count': len(connectors),
        'connectors': connectors
    }), 200


@connectors_bp.route('/available/<integration_type>/schema', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@cached_connector_discovery(timeout=300)  # Cache for 5 minutes
def get_connector_schema(integration_type: str):
    """
    Get configuration schema for a specific connector type.

    Args:
        integration_type: Connector type (e.g., 'azure_ad')

    Returns:
        200: Configuration schema
        404: Connector type not found

    Response:
        {
            "success": True,
            "integration_type": "azure_ad",
            "schema": {...}
        }
    """
    integration_service = current_app.integration_service
    schema = integration_service.get_connector_config_schema(integration_type)

    if not schema:
        from src.api.middleware.error_handler import NotFoundError
        raise NotFoundError(f'No connector found with type: {integration_type}')

    return jsonify({
        'success': True,
        'integration_type': integration_type,
        'schema': schema
    }), 200
