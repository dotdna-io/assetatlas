# SPDX-License-Identifier: AGPL-3.0-or-later
"""External query API — GET /api/v1/assets.

Authenticated via X-API-Key header. Designed for machine consumers
(SIEM, SOAR, CMDB) performing asset enrichment lookups.
"""

import logging
from flask import Blueprint, request, current_app

from src.auth.api_key_auth import require_api_key
from src.api.middleware.error_handler import handle_errors, ValidationError
from src.api.utils.responses import response_wrapper
from src.modules.fields import (
    InvalidFieldError, build_external_fields_response, EXTERNAL_FIELD_KINDS,
)

logger = logging.getLogger(__name__)

external_assets_bp = Blueprint('external_assets', __name__, url_prefix='/api/v1')

FILTER_PARAMS = ('ip', 'hostname', 'fqdn', 'mac', 'sn', 'q')
MAX_LIMIT = 100
DEFAULT_LIMIT = 10

# Full-inventory pull (paginated) for machine consumers.
INVENTORY_DEFAULT_LIMIT = 50
INVENTORY_MAX_LIMIT = 500


@external_assets_bp.route('/assets', methods=['GET'])
@require_api_key
@handle_errors
@response_wrapper
def query_assets():
    """Query assets by IP, hostname, FQDN, MAC, serial number, or free text."""
    # Extract filter params
    filters = {k: request.args.get(k) for k in FILTER_PARAMS if request.args.get(k)}

    if not filters:
        raise ValidationError(
            'At least one filter parameter is required (ip, hostname, fqdn, mac, sn, or q)'
        )

    # q is mutually exclusive with specific filters
    if 'q' in filters and len(filters) > 1:
        raise ValidationError(
            'Parameter "q" (free search) cannot be combined with specific field filters'
        )

    # Parse limit
    limit = request.args.get('limit', DEFAULT_LIMIT, type=int)
    limit = max(1, min(limit, MAX_LIMIT))

    # Parse optional fields
    fields_param = request.args.get('fields')
    fields = [f.strip() for f in fields_param.split(',')] if fields_param else None

    # Query via service
    service = current_app.asset_service
    results, total = service.query_assets(**filters, fields=fields, limit=limit)

    return {
        'assets': results,
        'count': len(results),
        'total': total,
        'query': filters,
    }


@external_assets_bp.route('/assets/inventory', methods=['GET'])
@require_api_key(rate_limit_setting='EXTERNAL_API_INVENTORY_RATE_LIMIT')
@handle_errors
@response_wrapper
def inventory_assets():
    """Full paginated asset inventory for machine consumers.

    Returns the same dynamic-field response shape as the UI's GET /api/assets:
    a top-level ``assets`` array (each row keyed by canonical percent-encoded
    FieldId strings, plus ``id``) and a top-level ``pagination`` block including
    ``pages``. Intended for the risk-app connector to page the entire inventory
    with a read-only API key — loop until ``page >= pagination.pages``.

    Query params:
        fields (required): CSV of FieldId strings, e.g.
            "static:Hostname,static:OS Normalized,static:Confidence Score".
        page:  1-based page number (default 1).
        limit: rows per page, 1..500 (default 50).
    """
    fields = [f.strip() for f in request.args.get('fields', '').split(',') if f.strip()]
    if not fields:
        raise ValidationError(
            'The "fields" parameter is required: a CSV of FieldId strings '
            '(e.g. "static:Hostname,static:OS Normalized").'
        )

    page = max(1, request.args.get('page', 1, type=int))
    limit = request.args.get('limit', INVENTORY_DEFAULT_LIMIT, type=int)
    limit = max(1, min(limit, INVENTORY_MAX_LIMIT))

    # Same service method the UI's GET /api/assets uses, so the response shape
    # (success/assets/pagination, encoded FieldId keys) is identical. An unknown
    # FieldId resolves to InvalidFieldError — surface it as a 400 (client error),
    # matching the JWT GET /api/assets path rather than a generic 500.
    service = current_app.asset_service
    try:
        return service.list_assets_dynamic(page=page, limit=limit, fields=fields)
    except InvalidFieldError as e:
        raise ValidationError(str(e))


@external_assets_bp.route('/fields', methods=['GET'])
@require_api_key
@handle_errors
@response_wrapper
def list_fields():
    """Field-catalog enumeration for machine consumers — every selectable
    FieldId with display name, kind, and data type.

    This is the enumeration half of the external contract: the inventory route
    can only VERIFY a guessed FieldId (200/400), and guessing fails on casing
    alone (ext:beszel:cpu 400s while ext:Beszel:cpu exists). Read-only; no
    pagination (the catalog is small and tenant-scoped). Filter-only fields
    are never served — they cannot be projected by the inventory route.

    Query params:
        kind (optional): CSV of FieldId prefixes to include —
            static,core,ext,group,tag,posture_state,posture_output,virtual.
            An unknown value is a 400 naming the valid set.
    """
    kinds = None
    kind_param = request.args.get('kind')
    if kind_param:
        kinds = {k.strip() for k in kind_param.split(',') if k.strip()}
        unknown = sorted(kinds - set(EXTERNAL_FIELD_KINDS))
        if unknown:
            raise ValidationError(
                f"Unknown kind value(s): {', '.join(unknown)}. "
                f"Valid kinds: {', '.join(EXTERNAL_FIELD_KINDS)}"
            )
        kinds = kinds or None

    return build_external_fields_response(
        current_app.field_resolver.catalog(), kinds=kinds
    )
