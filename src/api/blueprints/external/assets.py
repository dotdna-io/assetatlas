# SPDX-License-Identifier: AGPL-3.0-or-later
"""External query API — GET /api/v1/assets, /assets/inventory, /assets/search,
/fields.

Authenticated via X-API-Key header. Designed for machine consumers
(SIEM, SOAR, CMDB, LLM agents) performing asset enrichment lookups.
"""

import logging
from flask import Blueprint, request, current_app
from psycopg2 import DataError

from src.auth.api_key_auth import require_api_key
from src.api.blueprints.external import search_params
from src.api.middleware.error_handler import handle_errors, ValidationError
from src.api.utils.responses import response_wrapper
from src.mcp.tools.assets import list_assets_query
from src.mcp.tools._query import FieldResolutionError
from src.modules.fields import (
    InvalidFieldError, build_external_fields_response, EXTERNAL_FIELD_KINDS,
)
from src.modules.fields.operators import OperatorResolutionError
from src.modules.shared.confidence_thresholds import get_confidence_thresholds

logger = logging.getLogger(__name__)

external_assets_bp = Blueprint('external_assets', __name__, url_prefix='/api/v1')

FILTER_PARAMS = ('ip', 'hostname', 'fqdn', 'mac', 'sn', 'q')
MAX_LIMIT = 100
DEFAULT_LIMIT = 10

# Full-inventory pull (paginated) for machine consumers.
INVENTORY_DEFAULT_LIMIT = 50
INVENTORY_MAX_LIMIT = 500

# GET /api/v1/fields ?q= substring bound.
FIELDS_Q_MAX_CHARS = 128


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


@external_assets_bp.route('/assets/search', methods=['GET'])
@require_api_key(rate_limit_setting='EXTERNAL_API_SEARCH_RATE_LIMIT', own_window=True)
@handle_errors
@response_wrapper
def search_assets():
    """Find assets by free text and/or structured filters (read-only).

    The Explorer's own query path: ``search`` is the keyword box
    (AssetRepository._build_search_clause — every whitespace term must match,
    partially, against one trigram-indexed text of hostname, IP, MAC, serial,
    FQDN, manufacturer, model, data source, owner, location, user field,
    category, OS and extended attributes); ``filters`` are the Explorer's
    structured filters (``[{field, op, value}]``, operators per GET
    /api/v1/fields). Both run through list_assets_query, the function behind the
    chat list and the CSV export, so no second query engine exists.

    Query params: search, filters (JSON), logic (and|or), fields (CSV),
    sort_by, sort_dir (asc|desc), page (1-based), limit (1..100, default 20).
    At least one of ``search`` / ``filters`` is required — full dumps are the
    inventory route's job. Response is top-level (no ``data`` envelope):
    ``assets``, ``pagination`` (``total`` is the true match count) and
    ``query`` (the normalised request). Row keys: ``id``, static fields by
    snake_case column name, every other kind by its encoded FieldId.

    Every input is bounded and every unresolvable field/operator/value — or
    unknown parameter — is a 400 whose message names the fix: the engine would
    otherwise drop the clause and silently widen the result (see search_params).
    Search terms match literally (``%``/``_`` are not wildcards).
    """
    resolver = current_app.field_resolver
    args = request.args
    search_params.check_params(args)

    search = search_params.parse_search(args.get('search'))
    engine_filters, filters_echo = search_params.parse_filters(args.get('filters'), resolver)
    if search is None and engine_filters is None:
        raise ValidationError(
            'Provide "search" (free text) and/or "filters" (a JSON array of '
            '{field, op, value}). To page the whole inventory use '
            'GET /api/v1/assets/inventory.'
        )
    logic = search_params.parse_logic(args.get('logic'))
    fields = search_params.parse_fields(args.get('fields'), resolver)
    sort_by = search_params.parse_sort_by(args.get('sort_by'), resolver)
    sort_dir = search_params.parse_sort_dir(args.get('sort_dir'))
    page, limit = search_params.parse_paging(args.get('page'), args.get('limit'))

    # `fields=id` alone: fetch one cheap column and return id only.
    keep = None if fields else {'id'}
    query_fields = fields or ['static:Hostname']

    # Configured thresholds, as the Explorer passes them, so a
    # virtual:confidence_level filter or column buckets identically.
    thresholds = get_confidence_thresholds(current_app.settings_manager)
    try:
        rows, total = list_assets_query(
            current_app.db_adapter, resolver,
            filters=engine_filters,
            search=search_params.escape_search(search) if search else None,
            page=page, limit=limit,
            sort_by=sort_by, sort_dir=sort_dir,
            fields=','.join(query_fields), logic=logic,
            confidence_thresholds=thresholds,
        )
    except (FieldResolutionError, OperatorResolutionError, InvalidFieldError) as exc:
        raise ValidationError(str(exc))
    except DataError as exc:
        # Backstop for a filter value Postgres cannot cast that the up-front
        # checks did not anticipate. Without filters the caller sent no value
        # to cast, so the fault is in stored data: let it be a 500. First line
        # only — the rest echoes SQL.
        if engine_filters is None:
            raise
        raise ValidationError(
            "Postgres could not evaluate this query (a filter value may not "
            "fit its field's type): " + str(exc).strip().splitlines()[0]
        )

    return {
        'success': True,
        'assets': [search_params.project_row(r, keep) for r in rows],
        'pagination': {
            'total': total,
            'page': page,
            'limit': limit,
            'pages': (total + limit - 1) // limit if total > 0 else 0,
        },
        'query': {
            'search': search,
            'filters': filters_echo,
            'logic': logic,
            'sort_by': sort_by,
            # No sort_by: the engine's own order (most recent activity first).
            'sort_dir': sort_dir if sort_by else None,
            'fields': fields,
        },
    }


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

    Each entry also carries ``operators``: the filter operator codes GET
    /api/v1/assets/search accepts for that field.

    Query params:
        kind (optional): CSV of FieldId prefixes to include —
            static,core,ext,group,tag,posture_state,posture_output,virtual.
            An unknown value is a 400 naming the valid set.
        q (optional): case-insensitive substring over field_id and
            display_name (max 128 characters).
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

    q = (request.args.get('q') or '').strip() or None
    if q is not None and len(q) > FIELDS_Q_MAX_CHARS:
        raise ValidationError(
            f'"q" is {len(q)} characters; the maximum is {FIELDS_Q_MAX_CHARS}.'
        )

    return build_external_fields_response(
        current_app.field_resolver.catalog(), kinds=kinds, q=q
    )
