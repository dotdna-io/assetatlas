# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Integration Groups Blueprint

Manages integration groups: CRUD operations and member management.

Endpoints:
    GET    /api/integrations/groups                              - List groups
    POST   /api/integrations/groups                              - Create group
    GET    /api/integrations/groups/<group_id>                   - Get group
    PUT    /api/integrations/groups/<group_id>                   - Update group
    DELETE /api/integrations/groups/<group_id>                   - Delete group
    POST   /api/integrations/groups/<group_id>/members           - Add members
    DELETE /api/integrations/groups/<group_id>/members/<int_id>  - Remove member
"""

import logging
from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required

from src.api.middleware.error_handler import handle_errors
from src.api.utils.caching import invalidate_group_mapping_cache
from src.auth.middleware.rbac import require_admin

logger = logging.getLogger(__name__)

integration_groups_bp = Blueprint('integration_groups', __name__)


def _refresh_grouped_view():
    """Refresh the grouped provider coverage materialized view after membership changes."""
    try:
        db = current_app.db_adapter
        db.execute_query(
            "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_provider_coverage_grouped",
            fetch=False
        )
    except Exception as e:
        logger.warning(f"Failed to refresh grouped view: {e}")


@integration_groups_bp.route('/groups', methods=['GET'])
@jwt_required()
@handle_errors
def list_groups():
    """List all integration groups.

    Read-only and intentionally not admin-gated: the dashboard homepage
    renders Data Source Groups for every role (mutations stay admin-only).
    """
    service = current_app.integration_group_service
    groups = service.list_groups()
    return jsonify({'success': True, 'groups': groups}), 200


@integration_groups_bp.route('/groups', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
def create_group():
    """Create a new integration group."""
    data = request.get_json()
    if not data or not data.get('name'):
        return jsonify({'success': False, 'message': 'name is required'}), 400
    if not data.get('integration_type'):
        return jsonify({'success': False, 'message': 'integration_type is required'}), 400

    service = current_app.integration_group_service
    group = service.create_group(data['name'], data['integration_type'])
    _refresh_grouped_view()
    invalidate_group_mapping_cache()
    return jsonify({'success': True, 'group': group}), 201


@integration_groups_bp.route('/groups/<group_id>', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
def get_group(group_id):
    """Get integration group by ID."""
    service = current_app.integration_group_service
    group = service.get_group(group_id)
    if not group:
        return jsonify({'success': False, 'message': 'Group not found'}), 404
    return jsonify({'success': True, 'group': group}), 200


@integration_groups_bp.route('/groups/<group_id>', methods=['PUT'])
@jwt_required()
@require_admin()
@handle_errors
def update_group(group_id):
    """Update integration group."""
    data = request.get_json()
    if not data or not data.get('name'):
        return jsonify({'success': False, 'message': 'name is required'}), 400

    service = current_app.integration_group_service
    group = service.update_group(group_id, data['name'])
    if not group:
        return jsonify({'success': False, 'message': 'Group not found'}), 404
    _refresh_grouped_view()
    invalidate_group_mapping_cache()
    return jsonify({'success': True, 'group': group}), 200


@integration_groups_bp.route('/groups/<group_id>', methods=['DELETE'])
@jwt_required()
@require_admin()
@handle_errors
def delete_group(group_id):
    """Delete integration group."""
    service = current_app.integration_group_service
    try:
        service.delete_group(group_id)
    except ValueError:
        return jsonify({'success': False, 'message': 'Group not found'}), 404
    _refresh_grouped_view()
    invalidate_group_mapping_cache()
    return jsonify({'success': True}), 200


@integration_groups_bp.route('/groups/<group_id>/members', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
def add_members(group_id):
    """Add integrations to a group."""
    data = request.get_json()
    if not data or not data.get('integration_ids'):
        return jsonify({'success': False, 'message': 'integration_ids is required'}), 400

    service = current_app.integration_group_service
    try:
        service.add_members(group_id, data['integration_ids'])
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    _refresh_grouped_view()
    invalidate_group_mapping_cache()
    return jsonify({'success': True}), 200


@integration_groups_bp.route('/groups/<group_id>/members/<integration_id>', methods=['DELETE'])
@jwt_required()
@require_admin()
@handle_errors
def remove_member(group_id, integration_id):
    """Remove an integration from a group."""
    service = current_app.integration_group_service
    try:
        service.remove_member(group_id, integration_id)
    except ValueError:
        return jsonify({'success': False, 'message': 'Group not found'}), 404
    _refresh_grouped_view()
    invalidate_group_mapping_cache()
    return jsonify({'success': True}), 200


@integration_groups_bp.route('/groups/mapping', methods=['GET'])
@jwt_required()
@handle_errors
def get_group_mapping():
    """Get integration name to group name mapping for display resolution."""
    service = current_app.integration_group_service
    mapping = service.get_group_mapping()
    return jsonify({'success': True, 'mapping': mapping}), 200
