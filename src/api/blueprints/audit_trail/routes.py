# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Audit Trail API Endpoints

Provides read-only access to the audit trail for admin users.

Endpoints:
- GET /api/audit-trail - List audit trail entries with filtering and pagination
"""

import math
import logging
from flask import Blueprint, request, current_app
from flask_jwt_extended import jwt_required

from src.api.middleware.error_handler import handle_errors
from src.api.utils.responses import response_wrapper
from src.auth.middleware.rbac import require_admin

logger = logging.getLogger(__name__)

audit_trail_bp = Blueprint('audit_trail', __name__, url_prefix='/api/audit-trail')


@audit_trail_bp.route('', methods=['GET'])
@jwt_required()
@require_admin()
@handle_errors
@response_wrapper
def get_audit_trail():
    """
    List audit trail entries with filtering and pagination.

    Query Parameters:
        page (int): Page number, default 1
        per_page (int): Items per page, default 50, max 100
        start_date (str): Filter entries from this timestamp
        end_date (str): Filter entries up to this timestamp
        user_id (str): Filter by user UUID
        method (str): Filter by HTTP method (GET, POST, PUT, DELETE, etc.)
        path (str): Filter by path (partial match)
        status_code (int): Filter by HTTP status code
        category (str): Filter by semantic category (exact match)
        resource_type (str): Filter by semantic resource type (exact match)
        resource_id (str): Filter by semantic resource id (exact match)
        action (str): Filter by semantic action verb (exact match)

    Returns:
        200: Paginated audit trail entries
    """
    # Pagination
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 100)

    # Build WHERE conditions
    conditions = []
    params = {}

    start_date = request.args.get('start_date')
    if start_date:
        conditions.append('a.timestamp >= %(start_date)s')
        params['start_date'] = start_date

    end_date = request.args.get('end_date')
    if end_date:
        conditions.append('a.timestamp <= %(end_date)s')
        params['end_date'] = end_date

    user_id = request.args.get('user_id')
    if user_id:
        conditions.append('a.user_id = %(user_id)s::uuid')
        params['user_id'] = user_id

    username = request.args.get('username')
    if username:
        conditions.append('u.username ILIKE %(username_filter)s')
        params['username_filter'] = f'%{username}%'

    method = request.args.get('method')
    if method:
        conditions.append('a.method = %(method)s')
        params['method'] = method.upper()

    path_filter = request.args.get('path')
    if path_filter:
        escaped = path_filter.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        conditions.append("a.path LIKE %(path_filter)s ESCAPE '\\'")
        params['path_filter'] = f'%{escaped}%'

    status_code = request.args.get('status_code', type=int)
    if status_code is not None:
        conditions.append('a.status_code = %(status_code)s')
        params['status_code'] = status_code

    # Semantic annotation columns (migration 144) — exact match. Rows nothing
    # annotated keep these NULL, so filtering by any of them naturally narrows
    # to annotated entries only.
    category = request.args.get('category')
    if category:
        conditions.append('a.category = %(category)s')
        params['category'] = category

    resource_type = request.args.get('resource_type')
    if resource_type:
        conditions.append('a.resource_type = %(resource_type)s')
        params['resource_type'] = resource_type

    resource_id = request.args.get('resource_id')
    if resource_id:
        conditions.append('a.resource_id = %(resource_id)s')
        params['resource_id'] = resource_id

    action = request.args.get('action')
    if action:
        conditions.append('a.action = %(action)s')
        params['action'] = action

    where_clause = ' AND '.join(conditions) if conditions else 'TRUE'

    # Count must mirror the data query's JOIN or pagination diverges.
    count_query = (
        f'SELECT COUNT(*) as total FROM audit_trail a '
        f'LEFT JOIN users u ON u.id = a.user_id '
        f'WHERE {where_clause}'
    )
    count_result = current_app.db_adapter.execute_query(count_query, params)
    total = count_result[0]['total'] if count_result else 0

    # Fetch page
    offset = (page - 1) * per_page
    params['limit'] = per_page
    params['offset'] = offset

    data_query = (
        f'SELECT a.id, a.timestamp, a.user_id, u.username, a.method, a.path, '
        f'a.status_code, a.ip_address, a.duration_ms, a.request_id, '
        f'a.request_body, a.response_summary, '
        f'a.category, a.resource_type, a.resource_id, a.action, a.details '
        f'FROM audit_trail a '
        f'LEFT JOIN users u ON u.id = a.user_id '
        f'WHERE {where_clause} '
        f'ORDER BY a.timestamp DESC LIMIT %(limit)s OFFSET %(offset)s'
    )
    results = current_app.db_adapter.execute_query(data_query, params)

    total_pages = math.ceil(total / per_page) if per_page > 0 else 0

    return {
        'entries': results,
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': total,
            'total_pages': total_pages
        }
    }
