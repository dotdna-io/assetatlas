# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Assets Blueprint

Handles all asset inventory endpoints including detail view, list, and search.

Endpoints (6 total):
- GET /api/assets/fields - Get available fields for Explorer columns
- GET /api/assets/<asset_id> - Get asset details
- GET /api/assets/<asset_id>/history - Get asset change history
- GET /api/assets - List assets with pagination and filtering
- GET /api/assets/search - Search assets by hostname, IP, MAC, or serial number
- GET /api/assets/export.csv - Stream the full filtered asset list as CSV
"""

import logging
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterator, List, Tuple

from flask import Blueprint, request, current_app, Response, stream_with_context
from flask_jwt_extended import jwt_required

from src.api.middleware.error_handler import handle_errors, NotFoundError, ValidationError
from src.api.utils.csv_export import stream_csv
from src.mcp.tools.assets import (
    aggregate_assets_query,
    list_assets_query,
    FieldResolutionError,
)
from src.mcp.tools._common import parse_filters_arg
from src.mcp.tools._query import (
    CHAT_LIST_FIELDS_ENCODED,
    _normalize_field_list,
    resolve_list_columns,
)
from src.modules.fields.operators import OperatorResolutionError
from src.api.utils.responses import response_wrapper
from src.api.utils.validation import ensure_uuid, validate_query
from src.api.models.requests import (
    AssetListParams, AssetSearchParams, AssetFilter,
    AssetValuesParams, AssetAggregateParams, ExplorerFieldsParams
)
from src.api.utils.caching import get_cached_group_mapping
from src.modules.fields import build_catalog_response, InvalidFieldError
from src.modules.integrations.utils.field_grouping import FieldGroupResolver

logger = logging.getLogger(__name__)

# Create blueprint
assets_bp = Blueprint('assets', __name__, url_prefix='/api/assets')


# =============================================================================
# Field Metadata Endpoint
# =============================================================================

@assets_bp.route('/fields', methods=['GET'])
@jwt_required()
@handle_errors
@validate_query(ExplorerFieldsParams)
@response_wrapper
def get_explorer_fields(query_params: ExplorerFieldsParams):
    """
    Get available fields for Explorer table columns.

    Returns every live catalog entry — all eight buckets (target, advanced,
    enrichment, extended_attributes, tags, posture, virtual, unified).
    Explorer uses this to build its column picker and filter UI.

    Filter-only fields (filterable, never projectable/aggregatable/sortable)
    are EXCLUDED unless include_filter_only is set, so that a consumer which
    has not opted in cannot offer one as a column, pivot dimension or sort key.

    Query Parameters:
        include_filter_only (bool): Include filter-only fields (default false)

    Returns:
        200: FieldCatalog entries (see build_catalog_response)
        500: Server error
    """
    return build_catalog_response(
        current_app.field_resolver.catalog(),
        include_filter_only=query_params.include_filter_only,
    )


# =============================================================================
# Values & Aggregation Endpoints (must be before /<asset_id> catch-all)
# =============================================================================

@assets_bp.route('/values', methods=['GET'])
@jwt_required()
@handle_errors
@validate_query(AssetValuesParams)
@response_wrapper
def get_field_values(query_params: AssetValuesParams):
    """
    Get distinct values and counts for a specific field.

    Used by Explorer for:
    - Search value suggestions (autocomplete)
    - Column filter dropdown population

    Query Parameters:
        field (str): Field key to get values for
        limit (int): Max values to return (default: 50)
        search (str): Optional prefix filter on values
        filters (str): Optional JSON-encoded structured filters for context

    Returns:
        200: Field values with counts
    """
    asset_service = current_app.asset_service

    # Parse structured filters if provided. Each filter is validated against
    # AssetFilter which requires a typed FieldId (discriminator: kind).
    filters_list = _parse_structured_filters(query_params.filters)

    try:
        result = asset_service.get_field_values(
            field=query_params.field,
            limit=query_params.limit,
            search=query_params.search,
            filters=filters_list
        )
    except InvalidFieldError as e:
        raise ValidationError(str(e))

    return result


@assets_bp.route('/aggregate', methods=['GET'])
@jwt_required()
@handle_errors
@validate_query(AssetAggregateParams)
@response_wrapper
def aggregate_assets(query_params: AssetAggregateParams):
    """
    Aggregate assets by one or two dimensions for pivot table.

    Query Parameters:
        row_field (str): Encoded FieldId to group rows by
        col_field (str): Optional encoded FieldId to group columns by
        filters (str): Optional JSON array of AssetFilter objects
        alert_id (str): Optional alert UUID — narrow the aggregate to that
            alert's assets so a pivot export under a drilldown matches the
            scoped table rather than the whole fleet (#1013)

    Returns:
        200: Aggregated data with rows, columns, data matrix, grand_total
        400: Malformed alert_id
    """
    asset_service = current_app.asset_service

    # Canonicalise before the repository casts it to ::uuid: Postgres rejects
    # spellings Python's UUID() accepts, and psycopg2's error is not a
    # ValueError, so a client typo would leave handle_errors answering 500
    # instead of 400 (#973/#999).
    alert_id = ensure_uuid(query_params.alert_id, 'alert') if query_params.alert_id else None

    filters_list = _parse_structured_filters(query_params.filters)

    try:
        result = asset_service.aggregate_assets(
            row_field=query_params.row_field,
            col_field=query_params.col_field,
            filters=filters_list,
            alert_id=alert_id,
        )
    except InvalidFieldError as e:
        raise ValidationError(str(e))

    return result


def _parse_structured_filters(filters_json):
    """Parse query-string 'filters' JSON into a list of validated filter dicts.

    Each entry must conform to AssetFilter (field_id + op + value). Returns
    None if the input is falsy; raises ValidationError on malformed JSON or
    invalid filter shape.
    """
    if not filters_json:
        return None
    try:
        raw_filters = json.loads(filters_json)
    except json.JSONDecodeError as e:
        raise ValidationError(f'Invalid filters parameter: {e}')
    if not isinstance(raw_filters, list):
        raise ValidationError('Invalid filters parameter: expected a JSON array')
    try:
        return [AssetFilter(**f).model_dump() for f in raw_filters]
    except Exception as e:
        raise ValidationError(f'Invalid filters parameter: {e}')


# =============================================================================
# CSV Export Endpoint
# =============================================================================

# Columns for the chat CSV export — same set/order as the chat list table
# (src/api/blueprints/chat/artifacts.py:_LIST_ASSET_COLUMNS) so the downloaded
# file matches what the user saw. A drift test pins them equal.
_EXPORT_COLUMNS: List[Tuple[str, str]] = [
    ("hostname", "Hostname"),
    ("ip", "IP"),
    ("category", "Category"),
    ("subcategory", "Subcategory"),
    ("os_normalized", "OS"),
    ("owner", "Owner"),
    ("data_source", "Data Source"),
    ("last_seen", "Last Seen"),
]

# Encoded FieldIds passed to list_assets_query so the query SELECTs the columns
# the CSV renders (the repo default omits ip/owner/data_source). Single-sourced
# with the chat list preview via CHAT_LIST_FIELDS_ENCODED so the download agrees
# with what the user saw. That shared set is a superset of _EXPORT_COLUMNS — the
# extra fields it fetches (Location/MAC/Serial/Manufacturer/Model/Confidence) are
# simply not emitted by stream_csv (column-driven CSV export arrives in Phase 1).
_EXPORT_FIELDS_ENCODED = CHAT_LIST_FIELDS_ENCODED

# Safety bound so an accidental unfiltered export can't stream the whole DB.
# Generous — a chat "list all X" is already filtered.
_EXPORT_MAX_ROWS = 50_000
_EXPORT_PAGE_SIZE = 200  # matches list_assets_query's internal clamp


@assets_bp.route('/export.csv', methods=['GET'])
@jwt_required()
@handle_errors
def export_assets_csv():
    """Stream the full filtered asset list as CSV.

    Reuses the shared MCP list query (``list_assets_query``) — the exact path
    the chat table artifact is built from — so exported rows match what was
    shown. Accepts the chat/MCP filter shape ({field, op, value} as a JSON
    string); paginates server-side and streams to keep memory flat. No
    @response_wrapper: returns a raw streaming Response, not the JSON envelope.
    stream_with_context keeps the request (and its DB scope) alive until the
    generator is exhausted.

    Note: a DB error mid-stream (e.g. on page 3) cannot change the 200
    response already sent; the client receives a truncated file.  The
    exception is logged server-side via the module logger at ERROR level.

    """
    db = current_app.db_adapter
    resolver = current_app.field_resolver

    filters = request.args.get('filters')   # JSON string; list_assets_query parses
    search = request.args.get('search')
    sort_by = request.args.get('sort_by')
    sort_dir = request.args.get('sort_dir', 'asc')
    fields_param = request.args.get('fields')
    logic = request.args.get('logic', 'and')

    # GET /assets honours ?alert_id=, this does not: the export runs through
    # list_assets_query (the MCP compiler), which has no alert entry point. Say
    # so instead of streaming the whole fleet under a param the caller believes
    # narrowed it — a silently wider CSV is the one failure an export cannot
    # signal, since the 200 is already sent by the time rows are wrong (#907).
    if request.args.get('alert_id'):
        raise ValidationError(
            'export.csv cannot filter by alert_id. Export from an unfiltered '
            'Explorer, or use GET /api/assets?alert_id=... for the JSON list.'
        )

    # When a `fields` projection is supplied (chat column-pick / export_assets),
    # the CSV columns == those fields, resolved to (row_key, label) pairs via the
    # catalog so the download matches exactly what the user saw on screen. Absent
    # → the legacy fixed projection + column set (drift-tested == the chat
    # table's default columns), so existing callers are byte-for-byte unchanged.
    if fields_param:
        resolved_cols = resolve_list_columns(
            _normalize_field_list(fields_param, resolver), resolver,
        )
        if resolved_cols:
            effective_fields = fields_param
            export_columns = resolved_cols
        else:
            # Every requested field was unresolvable → fall back to the default
            # projection for BOTH the fetch and the emitted columns so they stay
            # aligned (else we'd SELECT the user fields but emit default columns,
            # yielding blank columns).
            effective_fields = _EXPORT_FIELDS_ENCODED
            export_columns = _EXPORT_COLUMNS
    else:
        effective_fields = _EXPORT_FIELDS_ENCODED
        export_columns = _EXPORT_COLUMNS

    # Fail fast on a malformed filters param. Without this, list_assets_query
    # swallows the parse error and returns ([], 0), so the client would get a
    # 200 with a header-only CSV and no indication the filter was rejected.
    _, filters_err = parse_filters_arg(filters)
    if filters_err:
        raise ValidationError(filters_err)

    # Resolve page 1 BEFORE constructing the streaming Response, so any query
    # error — an unresolvable filter field/operator or a bad sort_by, all of
    # which list_assets_query/_normalize_filters now RAISE — surfaces as a clean
    # 400 via @handle_errors instead of a truncated CSV after the 200 + headers
    # are already flushed. (A bad sort/field used to be silently dropped; now it
    # is loud, mirroring GET /api/assets.) Pages 2+ stay lazy in the generator;
    # a resolution error can only happen on page 1, so this catches them all.
    try:
        first_results, total = list_assets_query(
            db, resolver,
            filters=filters, search=search,
            page=1, limit=_EXPORT_PAGE_SIZE,
            sort_by=sort_by, sort_dir=sort_dir,
            fields=effective_fields, logic=logic,
        )
    except (FieldResolutionError, OperatorResolutionError, InvalidFieldError) as exc:
        raise ValidationError(str(exc))

    def _rows() -> Iterator[Dict[str, Any]]:
        emitted = 0
        page = 1
        results = first_results
        try:
            while results:
                for row in results:
                    if emitted >= _EXPORT_MAX_ROWS:
                        return
                    yield row
                    emitted += 1
                if page * _EXPORT_PAGE_SIZE >= total:
                    break
                page += 1
                results, _ = list_assets_query(
                    db, resolver,
                    filters=filters, search=search,
                    page=page, limit=_EXPORT_PAGE_SIZE,
                    sort_by=sort_by, sort_dir=sort_dir,
                    fields=effective_fields, logic=logic,
                )
        except Exception:
            logger.exception(
                "export_assets_csv: streaming failed after %d rows", emitted
            )
            raise

    filename = f"assetatlas-chat-export-{datetime.now(timezone.utc):%Y-%m-%d}.csv"
    return Response(
        stream_with_context(stream_csv(_rows(), export_columns)),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


def _aggregate_csv_payload(
    result: Dict[str, Any], row_field: str, col_field: Any, agg: str,
) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]:
    """Flatten an ``aggregate_assets`` result into ``(rows, columns)`` for
    ``stream_csv``. Emits the FULL matrix (an export never truncates to top-N)
    plus row/grand totals for the additive aggregates (COUNT/SUM); the
    non-additive ones (AVG/MIN/MAX/COUNT_DISTINCT) omit totals because summing
    averages/min/max/distinct counts is meaningless. ``_row`` is the synthetic
    row-label key; 2-D column keys are the col values themselves."""
    rows_in = result.get("rows") or []
    cols_in = result.get("columns") or []
    data = result.get("data") or {}
    agg_norm = (agg or "COUNT").upper()
    additive = agg_norm in ("COUNT", "SUM")
    is_2d = bool(cols_in)
    value_label = "Count" if agg_norm == "COUNT" else agg_norm.replace("_", " ").title()

    out_rows: List[Dict[str, Any]] = []
    if is_2d:
        columns: List[Tuple[str, str]] = [("_row", row_field)]
        columns += [(c, c) for c in cols_in]
        if additive:
            columns.append(("_total", "Total"))
        for r in rows_in:
            d = data.get(r) or {}
            entry: Dict[str, Any] = {"_row": r}
            total = 0
            for c in cols_in:
                v = d.get(c, 0) or 0
                entry[c] = v
                # include Decimal — psycopg2 returns it for SUM over a NUMERIC
                # column; excluding it makes the row Total under-count.
                if additive and isinstance(v, (int, float, Decimal)):
                    total += v
            if additive:
                entry["_total"] = total
            out_rows.append(entry)
        if additive:
            tot_entry: Dict[str, Any] = {"_row": "Total"}
            grand = 0
            for c in cols_in:
                col_sum = sum((data.get(r) or {}).get(c, 0) or 0 for r in rows_in)
                tot_entry[c] = col_sum
                grand += col_sum
            tot_entry["_total"] = grand
            out_rows.append(tot_entry)
    else:
        columns = [("_row", row_field), ("_value", value_label)]
        for r in rows_in:
            out_rows.append(
                {"_row": r, "_value": (data.get(r) or {}).get("count", 0)}
            )
        if additive:
            grand = sum(
                (data.get(r) or {}).get("count", 0) or 0 for r in rows_in
            )
            out_rows.append({"_row": "Total", "_value": grand})
    return out_rows, columns


@assets_bp.route('/aggregate.csv', methods=['GET'])
@jwt_required()
@handle_errors
def export_aggregate_csv():
    """Stream an asset aggregate (pivot) as CSV: the full row×col matrix plus
    row/grand totals for additive aggregates.

    Mirrors export.csv (a raw streaming Response, no @response_wrapper) and
    reuses the shared ``aggregate_assets_query`` so the downloaded file matches
    the on-screen matrix exactly. The matrix is bounded (the repo groups), so it
    is built in memory then streamed row-by-row.
    """
    db = current_app.db_adapter
    resolver = current_app.field_resolver

    row_field = request.args.get('row_field')
    if not row_field:
        raise ValidationError("row_field is required for an aggregate export.")
    col_field = request.args.get('col_field')
    agg = request.args.get('agg', 'COUNT')
    metric_field = request.args.get('metric_field')
    filters = request.args.get('filters')
    logic = request.args.get('logic', 'and')

    _, filters_err = parse_filters_arg(filters)
    if filters_err:
        raise ValidationError(filters_err)

    # aggregate_assets_query returns a clean error string (never raises) for an
    # unknown field/metric/operator or a bad agg combo — surface it as a 400.
    result, error_hint = aggregate_assets_query(
        db, resolver, row_field=row_field, col_field=col_field,
        filters=filters, agg=agg, metric_field=metric_field, logic=logic,
    )
    if error_hint is not None:
        raise ValidationError(error_hint)

    out_rows, columns = _aggregate_csv_payload(result, row_field, col_field, agg)
    filename = (
        f"assetatlas-chat-aggregate-{datetime.now(timezone.utc):%Y-%m-%d}.csv"
    )
    return Response(
        stream_with_context(stream_csv(out_rows, columns)),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


# =============================================================================
# Asset Endpoints
# =============================================================================

@assets_bp.route('/<asset_id>', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def get_asset_detail(asset_id: str):
    """
    Get detailed information for a specific asset.
    ---
    tags:
      - Assets
    summary: Get asset details
    description: |
      Retrieves comprehensive information for a specific asset including:
      - Core asset attributes (hostname, IPs, MACs, serial numbers, etc.)
      - Provider attribution (which integration provided each field)
      - Confidence scores and explanations
      - Tags and relationships
    parameters:
      - name: asset_id
        in: path
        type: string
        required: true
        description: Asset UUID
        example: "550e8400-e29b-41d4-a716-446655440000"
    responses:
      200:
        description: Asset details retrieved successfully
        schema:
          type: object
          properties:
            id:
              type: string
              example: "550e8400-e29b-41d4-a716-446655440000"
            hostname:
              type: string
              example: "workstation-001"
            primary_ip:
              type: string
              example: "192.168.1.100"
            mac_address:
              type: string
              example: "00:11:22:33:44:55"
            serial_number:
              type: string
              example: "ABC123456"
            os_type:
              type: string
              example: "Windows"
            asset_type:
              type: string
              example: "Desktop"
            confidence_level:
              type: string
              enum: [high, medium, low]
            providers:
              type: array
              items:
                type: object
      404:
        description: Asset not found
        schema:
          type: object
          properties:
            error:
              type: string
            message:
              type: string
      500:
        description: Internal server error
    """
    asset_service = current_app.asset_service

    asset_detail = asset_service.get_asset_detail(asset_id)

    if not asset_detail:
        raise NotFoundError(f'Asset {asset_id} not found', details={'asset_id': asset_id})

    return asset_detail.model_dump(mode='json')


@assets_bp.route('/<asset_id>/merge-keys', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def get_merge_keys(asset_id: str):
    """
    Get the merge-key components that were glued together to form this
    asset, decoded into semantic type/value pairs.

    Read-only diagnostic view available to any authenticated user (not just
    admins) so an operator can see which identifier is behind a bad merge
    before denylisting it.

    Args:
        asset_id: Asset UUID

    Returns:
        200: {"keys": [{type, value, first_seen, last_seen}, ...],
              "tell": {"serial_count": n, "over_merged": bool,
                       "concurrent": {type: [values]}}}
        400: asset_id is not a UUID

    over_merged is set when one identity type has 2+ concurrently-active
    values, which "concurrent" names; see concurrent_identifiers() (#973).
    """
    return current_app.asset_service.get_merge_keys(ensure_uuid(asset_id, 'asset'))


@assets_bp.route('/<asset_id>/history', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
def get_asset_history(asset_id: str):
    """
    Get change history for a specific asset.
    ---
    tags:
      - Assets
    summary: Get asset change history
    description: |
      Returns chronological attribute change events for an asset,
      transformed from consolidated chronicle data.
    parameters:
      - name: asset_id
        in: path
        type: string
        required: true
        description: Asset UUID
      - name: time_range
        in: query
        type: string
        required: false
        description: Time window (24h, 7d, 30d, 90d)
        default: "30d"
      - name: limit
        in: query
        type: integer
        required: false
        description: Max chronicle events to fetch
        default: 500
    responses:
      200:
        description: Asset history retrieved successfully
      404:
        description: Asset not found
    """
    history_service = current_app.asset_history_service
    time_range = request.args.get('time_range', '30d')
    limit = request.args.get('limit', 500, type=int)

    result = history_service.get_asset_history(asset_id, time_range=time_range, limit=limit)

    if not result:
        raise NotFoundError(f'Asset {asset_id} not found', details={'asset_id': asset_id})

    return result


@assets_bp.route('', methods=['GET'])
@jwt_required()
@handle_errors
@validate_query(AssetListParams)
@response_wrapper
def list_assets(query_params: AssetListParams):
    """
    List assets with pagination and filtering.

    Query Parameters:
        page (int): Page number (default: 1)
        limit (int): Results per page (default: 50, max: 10000)
        os_type (str): Filter by OS type
        asset_type (str): Filter by asset type
        tag (str): Filter by tag
        provider (str): Filter by provider
        search (str): Search term (hostname, IP, MAC, serial number)
        alert_id (str): Alert UUID — narrow to the assets that alert names (#907)
        fields (str): Comma-separated field keys for dynamic columns (Explorer)

    Returns:
        200: List of assets with pagination
        400: Malformed alert_id
        500: Server error
    """
    asset_service = current_app.asset_service

    # Canonicalise before the repository casts it to ::uuid: Postgres rejects
    # spellings Python's UUID() accepts, and psycopg2's error is not a
    # ValueError, so a client typo would leave handle_errors answering 500
    # instead of 400 (#973/#999).
    alert_id = ensure_uuid(query_params.alert_id, 'alert') if query_params.alert_id else None

    # Parse fields parameter (CSV of encoded FieldId strings)
    fields_list = None
    if query_params.fields:
        fields_list = [f.strip() for f in query_params.fields.split(',') if f.strip()]

    # Parse structured filters (JSON array of AssetFilter objects)
    filters_list = _parse_structured_filters(query_params.filters)

    # Use dynamic method when fields are specified (Explorer use case)
    if fields_list:
        try:
            result = asset_service.list_assets_dynamic(
                page=query_params.page,
                limit=query_params.limit,
                category=query_params.asset_type,
                search=query_params.search,
                fields=fields_list,
                sort_by=query_params.sort_by,
                sort_dir=query_params.sort_dir,
                filters=filters_list,
                alert_id=alert_id
            )
        except InvalidFieldError as e:
            raise ValidationError(str(e))

        # Deduplicate Data Source arrays using group mapping. The repository
        # always emits the SELECT alias in canonical encoded form regardless
        # of what the client sent, so the response key is stable.
        group_mapping = get_cached_group_mapping()
        if group_mapping and result.get("assets"):
            resolver = FieldGroupResolver(group_mapping)
            data_source_key = "static:Data%20Source"
            for asset in result["assets"]:
                ds = asset.get(data_source_key)
                if ds and isinstance(ds, list):
                    asset[data_source_key] = resolver.deduplicate_sources(ds)

        return result

    # Standard list for backwards compatibility
    asset_list = asset_service.list_assets(
        page=query_params.page,
        limit=query_params.limit,
        category=query_params.asset_type,
        search=query_params.search,
        alert_id=alert_id
    )

    return asset_list.model_dump(mode='json')


@assets_bp.route('/search', methods=['GET'])
@jwt_required()
@handle_errors
@validate_query(AssetSearchParams)
@response_wrapper
def search_assets(query_params: AssetSearchParams):
    """
    Search for assets by hostname, IP, MAC, or serial number.
    ---
    tags:
      - Assets
    summary: Search assets
    description: |
      Fuzzy search for assets across multiple fields:
      - Hostname (partial match)
      - IP addresses (exact or CIDR match)
      - MAC addresses (normalized match)
      - Serial numbers (exact match)

      Results are ranked by relevance and include confidence scores.
    parameters:
      - name: q
        in: query
        type: string
        required: true
        description: Search query (hostname, IP, MAC, or serial number)
        example: "workstation-001"
      - name: field
        in: query
        type: string
        required: false
        description: Specific field to search (if omitted, searches all fields)
        enum: [hostname, ip, mac, serial]
        example: "hostname"
      - name: limit
        in: query
        type: integer
        required: false
        description: Maximum number of results to return
        default: 20
        minimum: 1
        maximum: 100
        example: 20
    responses:
      200:
        description: Search results
        schema:
          type: object
          properties:
            results:
              type: array
              items:
                type: object
                properties:
                  id:
                    type: string
                  hostname:
                    type: string
                  primary_ip:
                    type: string
                  mac_address:
                    type: string
                  serial_number:
                    type: string
                  match_score:
                    type: number
            count:
              type: integer
            query:
              type: string
      400:
        description: Invalid query parameters
        schema:
          type: object
          properties:
            error:
              type: string
            message:
              type: string
      500:
        description: Internal server error
    """
    asset_service = current_app.asset_service

    # Search assets using validated query params
    search_results = asset_service.search_assets(
        query=query_params.q,
        field=query_params.field,
        limit=query_params.limit
    )

    return search_results.model_dump(mode='json')
