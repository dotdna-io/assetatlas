# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Chat Tool Bindings

Wraps the existing MCP tool ``*_impl`` helpers as async closures and
registers them on a ``ToolRegistry`` for the chat orchestration layer.
The MCP server still uses the same ``*_impl`` helpers via its FastMCP
wrappers; these bindings let the chat blueprint dispatch tool calls
without going through the MCP server.
"""

from __future__ import annotations

from src.mcp.tools.alert_rules import list_alert_rules_impl
from src.mcp.tools.alerts import (
    get_alert_detail_impl,
    get_alert_history_impl,
    list_alerts_for_asset_impl,
)
from src.modules.assets.repositories.asset_repository import AssetRepository
# The asset query/aggregate/field-value wrappers + their formatters now live
# behind query_assets/export_assets (the old bindings below are thin shims);
# only the single-asset get_asset binding still composes assets.py directly.
from src.mcp.tools.assets import (
    format_asset_detail,
    get_asset_query,
    resolve_asset_identifier,
)
from src.mcp.tools._query import CHAT_LIST_FIELDS_ENCODED
from src.mcp.tools.charts_tool import run_chart, run_report
from src.mcp.tools.export_assets import export_assets
from src.mcp.tools.query_assets import query_assets
from src.mcp.tools.audit import get_audit_trail_impl
from src.mcp.tools.categories import get_asset_categories_impl
from src.mcp.tools.chat_usage_tool import get_chatbot_usage_impl
from src.mcp.tools.chronicle_queries import (
    get_asset_trend_impl,
    list_recent_changes_impl,
)
from src.mcp.tools.enrichment import list_enrichment_datasets_impl
from src.mcp.tools.eol import list_eol_assets_impl
from src.mcp.tools.field_registry import (
    describe_catalog_impl,
    get_field_registry_impl,
)
from src.mcp.tools.unified_fields import (
    get_unified_field_impl,
    list_unified_fields_impl,
)
from src.mcp.tools.integration_admin import (
    get_batch_impl,
    get_integration_mapping_impl,
    get_integration_schedule_impl,
    list_integration_batches_impl,
    list_integrations_impl,
)
from src.mcp.tools.integration_diagnostics import (
    get_integration_impl,
)
from src.mcp.tools.dashboard_tools import (
    get_dashboard_impl,
    get_dashboard_trend_impl,
    get_relationship_graph_impl,
)
from src.mcp.tools.metrics_tool import query_metrics_impl
from src.mcp.tools.monitoring import (
    get_alerts_impl,
    get_asset_history_impl,
    get_pipeline_status_impl,
    list_tag_rules_impl,
)
from src.mcp.tools.pipeline_runs import list_pipeline_runs_impl
from src.mcp.tools.posture import (
    get_compliance_matrix_impl,
    get_posture_compliance_impl,
    list_posture_rules_impl,
)
from src.mcp.tools.rules import (
    get_rule_history_impl,
    list_rules_impl,
    preview_rule_impl,
    preview_rule_output_impl,
)
from src.mcp.tools.reports import get_report_impl, list_reports_impl
from src.mcp.tools.scheduler import get_scheduler_status_impl
from src.mcp.tools.settings_tool import get_settings_impl
from src.api.modules.settings.settings_schema import get_confidence_thresholds
from src.mcp.tools.sources import get_source_coverage_impl
from src.mcp.tools.suggestions import semantic_suggest_impl

from .artifacts import (
    build_alerts_timeline_chart,
    build_table_from_alerts,
    build_table_from_asset,
    build_table_from_alert_assets,
)
from .repository import ChatRepository
from .semantic_cache import cached_semantic_suggest
from .tool_registry import ToolRegistry

# Chat list defaults: fetch enough to render a full small/medium "list all" in
# the table (DEFAULT_MAX_TABLE_ROWS), but keep the LLM-facing text to the first
# _CHAT_LIST_TEXT_ROWS rows so a 100-row fetch doesn't bloat the model context.
_CHAT_LIST_DEFAULT_LIMIT = 100
_CHAT_LIST_TEXT_ROWS = 25

# Fields the chat list_assets result actually displays — the union of the table
# artifact columns and the LLM-text formatter. Single-sourced in _query.py and
# shared verbatim with /api/assets/export.csv so the chat preview and the CSV
# download agree (see CHAT_LIST_FIELDS_ENCODED for the rationale + #742).
_CHAT_LIST_FIELDS_ENCODED = CHAT_LIST_FIELDS_ENCODED


def build_registry(*, db, suggestion_service=None,
                   suggestion_service_getter=None,
                   field_resolver=None,
                   unified_fields_service=None,
                   dashboard_service=None,
                   pipeline_service=None,
                   pipeline_phase_service=None,
                   scheduler_service=None,
                   integration_service=None,
                   mapping_preview_service=None,
                   integration_group_service=None,
                   alerts_service=None,
                   enrichment_service=None,
                   monitoring_service=None,
                   settings_manager=None,
                   query_engine=None,
                   tag_rule_service=None,
                   tag_preview_service=None,
                   posture_repository=None,
                   posture_category_read_service=None,
                   posture_preview_service=None,
                   posture_sub_rule_mgmt_service=None) -> ToolRegistry:
    """Build a ToolRegistry populated with all chat-callable MCP tool impls.

    ``suggestion_service`` / ``suggestion_service_getter``: the embedding
    suggestion service is preloaded in a background thread (factory.py),
    so at the moment the chat registry is built the attribute on ``app``
    is usually still None. Passing a callable via ``suggestion_service_getter``
    defers the lookup to tool-invoke time, after preload has finished.
    Tests that don't care about lazy resolution can still pass a direct
    ``suggestion_service=`` instance.
    """
    r = ToolRegistry()

    # --- query_assets: the primary asset tool (list / aggregate / ---
    # --- field_values behind a required `mode` enum). search_assets /  ---
    # --- list_assets / aggregate_assets / get_field_values are kept as   ---
    # --- thin shims onto this (below) for back-compat.                   ---
    async def _query_assets(
        mode: str, filters=None, logic="and", search=None, fields=None,
        sort_by=None, sort_dir="asc", page=1, limit=None,
        row_field=None, col_field=None, agg="COUNT", metric_field=None,
        show="count", row_limit=20, col_limit=30, sort="count",
        include_null=True, field=None,
    ):
        return query_assets(
            db, field_resolver, mode=mode, filters=filters, logic=logic,
            search=search, fields=fields, sort_by=sort_by, sort_dir=sort_dir,
            page=page, limit=limit, row_field=row_field, col_field=col_field,
            agg=agg, metric_field=metric_field, show=show, row_limit=row_limit,
            col_limit=col_limit, sort=sort, include_null=include_null,
            field=field,
        )
    r.register(
        "query_assets", _query_assets,
        "The primary tool for asset questions. One tool, three modes (set "
        "`mode`):\n"
        "- mode='list' — return matching asset rows (filters / search / sort, "
        "paged). `fields` picks the EXACT columns returned (any catalog field "
        "incl ext:/group:/tag:/posture/virtual); the user sees up to 100 rows "
        "in a table and can download the full set as CSV.\n"
        "- mode='aggregate' — server-side grouped counts (or SUM/AVG/MIN/MAX/"
        "COUNT_DISTINCT of `metric_field`) by `row_field` (+ optional "
        "`col_field` for a 2-D pivot). Use for 'how many / breakdown by / total "
        "of / average'. Far cheaper than paging list_assets. Each row/cell is "
        "drillable.\n"
        "- mode='field_values' — distinct values of one `field` with counts "
        "('what locations exist?', 'which OS versions?').\n"
        "Field names accept display ('OS Normalized'), snake ('os_normalized') "
        "or encoded ('static:Category') forms. Unknown fields/operators are "
        "rejected with a clear error — call describe_catalog / semantic_suggest "
        "to find the right name; never guess.",
        {"type": "object", "properties": {
            "mode": {
                "type": "string",
                "enum": ["list", "aggregate", "field_values"],
                "description": (
                    "REQUIRED. 'list' = asset rows; 'aggregate' = grouped "
                    "counts/pivot; 'field_values' = one field's distinct values."
                ),
            },
            "filters": {
                "type": "array",
                "description": (
                    "[{field, op, value}] (all modes). `op` is a code like eq, "
                    "neq, contains, gt, gte, lt, lte, any, all, none, between, "
                    "within_last, empty, notempty. Unknown field/op is rejected "
                    "loudly, never dropped."
                ),
            },
            "logic": {
                "type": "string", "enum": ["and", "or"],
                "description": "Combine `filters` (flat; default and).",
            },
            "search": {
                "type": "string",
                "description": (
                    "list: free-text across hostname/IP/MAC/serial/owner/"
                    "location/manufacturer/model/OS/data_source. field_values: "
                    "substring on the returned values."
                ),
            },
            "fields": {
                "type": "array",
                "description": (
                    "list mode: catalog field names or encoded ids = the exact "
                    "columns returned (and the table/CSV columns). Defaults to "
                    "the standard chat list set."
                ),
            },
            "sort_by": {
                "type": "string",
                "description": "list mode: field to sort by (unresolvable = "
                               "error, not a silently different order).",
            },
            "sort_dir": {"type": "string", "enum": ["asc", "desc"]},
            "page": {"type": "integer", "minimum": 1},
            "limit": {
                "type": "integer", "minimum": 1,
                "description": "list ≤200, field_values ≤500 (clamped).",
            },
            "row_field": {
                "type": "string",
                "description": "aggregate mode (REQUIRED there): field to group "
                               "rows by.",
            },
            "col_field": {
                "type": "string",
                "description": "aggregate mode: optional 2nd field → 2-D pivot.",
            },
            "agg": {
                "type": "string",
                "description": (
                    "aggregate function: COUNT (default), COUNT_DISTINCT, SUM, "
                    "AVG, MIN, MAX. All but COUNT require `metric_field`."
                ),
            },
            "metric_field": {
                "type": "string",
                "description": "aggregate mode: the numeric field SUM/AVG/MIN/"
                               "MAX/COUNT_DISTINCT operate on.",
            },
            "show": {
                "type": "string",
                "enum": ["count", "percent_total"],
                "description": "aggregate mode: cell display — raw count/value "
                               "or percent of the grand total (default count).",
            },
            "row_limit": {"type": "integer", "minimum": 1, "maximum": 30},
            "col_limit": {"type": "integer", "minimum": 1, "maximum": 30},
            "sort": {
                "type": "string", "enum": ["value", "count"],
                "description": "aggregate mode: order rows/cols by label "
                               "('value') or magnitude ('count', default).",
            },
            "include_null": {"type": "boolean"},
            "field": {
                "type": "string",
                "description": "field_values mode (REQUIRED there): the field "
                               "to enumerate distinct values for.",
            },
        }, "required": ["mode"]},
    )

    async def _export_assets(
        target="list", filters=None, logic="and", search=None, fields=None,
        sort_by=None, sort_dir="asc", row_field=None, col_field=None,
        agg="COUNT", metric_field=None, show="count",
    ):
        return export_assets(
            db, field_resolver, target=target, filters=filters, logic=logic,
            search=search, fields=fields, sort_by=sort_by, sort_dir=sort_dir,
            row_field=row_field, col_field=col_field, agg=agg,
            metric_field=metric_field, show=show,
        )
    r.register(
        "export_assets", _export_assets,
        "Export the full result set as a downloadable CSV — use this (not "
        "query_assets) when the user wants a file / spreadsheet / 'all of "
        "them' / 'download'. The rows do NOT enter the chat; the user gets a "
        "Download CSV button on the result.\n"
        "- target='list' — the matching asset rows; `fields` picks the EXACT "
        "CSV columns (any catalog field). Same filter/search/sort grammar as "
        "query_assets mode=list.\n"
        "- target='aggregate' — the row_field[×col_field] `agg` pivot matrix "
        "(incl row/column/grand totals), same grammar as query_assets "
        "mode=aggregate.",
        {"type": "object", "properties": {
            "target": {
                "type": "string", "enum": ["list", "aggregate"],
                "description": "What to export (default list).",
            },
            "filters": {"type": "array",
                        "description": "Standard [{field, op, value}] grammar."},
            "logic": {"type": "string", "enum": ["and", "or"]},
            "search": {"type": "string",
                       "description": "target=list: free-text search."},
            "fields": {"type": "array",
                       "description": "target=list: the exact CSV columns "
                                      "(catalog field names/encoded ids)."},
            "sort_by": {"type": "string", "description": "target=list."},
            "sort_dir": {"type": "string", "enum": ["asc", "desc"]},
            "row_field": {"type": "string",
                          "description": "target=aggregate (required there)."},
            "col_field": {"type": "string",
                          "description": "target=aggregate: optional pivot."},
            "agg": {"type": "string",
                    "description": "target=aggregate: COUNT (default), "
                                   "COUNT_DISTINCT, SUM, AVG, MIN, MAX."},
            "metric_field": {"type": "string",
                             "description": "target=aggregate: field the "
                                            "non-COUNT aggregates operate on."},
            "show": {"type": "string",
                     "enum": ["count", "percent_total"]},
        }, "required": []},
    )

    # --- run_chart / run_report: the ONLY chat path to /api/charts/query. ---
    # --- Passthrough to the charts engine; complements query_assets       ---
    # --- aggregate (the system prompt routes between them).               ---
    async def _run_chart(
        query_type, aggregate="COUNT", metric_field=None, dimension=None,
        segment=None, time_field=None, split_field=None, interval=None,
        last=None, start=None, end=None, dimension_limit=None,
        segment_limit=None, row_limit=None, col_limit=None, limit=None,
        percentage=False, filters=None, comparison_filters=None,
    ):
        return run_chart(
            db, field_resolver, settings_manager, query_type=query_type,
            aggregate=aggregate, metric_field=metric_field, dimension=dimension,
            segment=segment, time_field=time_field, split_field=split_field,
            interval=interval, last=last, start=start, end=end,
            dimension_limit=dimension_limit, segment_limit=segment_limit,
            row_limit=row_limit, col_limit=col_limit, limit=limit,
            percentage=percentage, filters=filters,
            comparison_filters=comparison_filters,
        )
    r.register(
        "run_chart", _run_chart,
        "Run an analytics chart over the asset inventory. Use this (not "
        "query_assets) for analytics the aggregate can't express:\n"
        "- query_type='distribution' — donut of COUNT by one `dimension`.\n"
        "- query_type='comparison' — stacked bar of COUNT by `dimension` × "
        "`segment`.\n"
        "- query_type='trend' — asset COUNT bucketed over time (`time_field` + "
        "`interval`); optional `split_field` for multiple series. Set "
        "percentage=true (or query_type='trend_percentage') for percent-over-"
        "time.\n"
        "- query_type='correlation' — flow/cross-tab COUNT between `dimension` "
        "(source) and `segment` (target).\n"
        "- query_type='metric' — a SINGLE aggregate number: `aggregate` "
        "(COUNT/COUNT_DISTINCT/SUM/AVG/MIN/MAX) of `metric_field`; "
        "percentage=true makes it a percent-of-base (denominator = "
        "`comparison_filters`).\n"
        "Prefer query_assets mode='aggregate' for a plain COUNT or top-N by one "
        "or two fields. `filters` use the SAME standard {field, op, value} "
        "grammar as query_assets. Each chart bucket is drillable.",
        {"type": "object", "properties": {
            "query_type": {
                "type": "string",
                "enum": ["distribution", "comparison", "correlation", "metric",
                         "trend", "trend_percentage"],
                "description": "REQUIRED. The chart shape (see the tool "
                               "description for each).",
            },
            "aggregate": {
                "type": "string",
                "enum": ["COUNT", "COUNT_DISTINCT", "SUM", "AVG", "MIN", "MAX"],
                "description": "metric mode: the aggregate (default COUNT). "
                               "Non-COUNT needs `metric_field`.",
            },
            "metric_field": {"type": "string",
                             "description": "metric mode: the numeric field the "
                                            "aggregate operates on."},
            "dimension": {"type": "string",
                          "description": "distribution: the field to break down "
                                         "by; comparison: x-axis; correlation: "
                                         "source."},
            "segment": {"type": "string",
                        "description": "comparison: the series to stack; "
                                       "correlation: target."},
            "time_field": {"type": "string",
                           "description": "trend: the timestamp/date field to "
                                          "bucket by (e.g. First Seen)."},
            "split_field": {"type": "string",
                            "description": "trend: optional field to split into "
                                           "multiple series."},
            "interval": {"type": "string",
                         "enum": ["hour", "day", "week", "month", "quarter",
                                  "year"],
                         "description": "trend: time bucket size (required for "
                                        "trends)."},
            "last": {"type": "string",
                     "description": "trend: relative window (e.g. '7d', '30d', "
                                    "'all'). Or use start/end."},
            "start": {"type": "string",
                      "description": "custom window start (ISO); needs "
                                     "time_field."},
            "end": {"type": "string", "description": "custom window end (ISO)."},
            "dimension_limit": {"type": "integer", "minimum": 1, "maximum": 20,
                                "description": "distribution: top-N slices; "
                                               "trend: max series."},
            "segment_limit": {"type": "integer", "minimum": 1, "maximum": 20,
                              "description": "comparison: max stacked segments."},
            "row_limit": {"type": "integer", "minimum": 1, "maximum": 30,
                          "description": "correlation: max source rows."},
            "col_limit": {"type": "integer", "minimum": 1, "maximum": 30,
                          "description": "correlation: max target columns."},
            "limit": {"type": "integer", "minimum": 1},
            "percentage": {"type": "boolean",
                           "description": "metric/trend: compute a percent of "
                                          "the comparison_filters base."},
            "filters": {"type": "array",
                        "description": "Standard [{field, op, value}] grammar "
                                       "(same as query_assets), AND-combined."},
            "comparison_filters": {"type": "array",
                                   "description": "percentage mode: the "
                                                  "denominator (base) filter "
                                                  "set."},
        }, "required": ["query_type"]},
    )

    async def _run_report(report_id, chart_index=None):
        return run_report(db, field_resolver, settings_manager,
                          report_id=report_id, chart_index=chart_index)
    r.register(
        "run_report", _run_report,
        "Re-run a saved report's charts and return their data. Replays each "
        "stored chart's query through the same engine as run_chart. Pass "
        "`chart_index` to run a single chart; omit it to summarise every chart "
        "(the first renders as the artifact). Find report ids with list_reports.",
        {"type": "object", "properties": {
            "report_id": {"type": "string", "description": "The report UUID."},
            "chart_index": {"type": "integer", "minimum": 0,
                            "description": "Optional: run only this chart "
                                           "(0-based)."},
        }, "required": ["report_id"]},
    )

    # --- Existing 12 MCP tools (assets + monitoring). Each wrapper is a ---
    # --- thin async closure that supplies the infra dependency.         ---

    # Deprecation shim → query_assets(mode='list', search=…). Kept so existing
    # callers/evals using the old name keep working for 1-2 releases.
    async def _search_assets(query: str, limit: int = 20):
        return query_assets(db, field_resolver, mode="list", search=query,
                            limit=limit)
    r.register("search_assets", _search_assets,
               "Search for assets by hostname, IP, MAC, serial, owner, or "
               "location. (Deprecated alias for query_assets mode='list' "
               "search=…; prefer query_assets.)",
               {"type": "object",
                "properties": {"query": {"type": "string"},
                               "limit": {"type": "integer"}},
                "required": ["query"]})

    async def _get_asset(identifier: str = None, asset_id: str = None,
                         source: str = None, field_search: str = None):
        ident = identifier or asset_id
        if not ident:
            return ("Provide `identifier` — a UUID, hostname, IP, FQDN, MAC, "
                    "or serial number."), None
        resolved, err = resolve_asset_identifier(db, field_resolver, ident)
        if err:
            return err, None
        asset = get_asset_query(db, field_resolver, asset_id=resolved)
        if asset is None:
            return f"Asset not found: {resolved}", None
        repo = AssetRepository(db, field_resolver)
        tags = repo.get_asset_tags(resolved)
        providers = repo.get_asset_providers(resolved)
        text = format_asset_detail(asset, asset_id=resolved, tags=tags,
                                   providers=providers, source=source,
                                   field_search=field_search)
        artifact = build_table_from_asset(
            asset, asset_id=resolved, providers=providers, tags=tags,
            source=source, field_search=field_search)
        return text, artifact
    r.register("get_asset", _get_asset,
               "Full detail for ONE asset — resolves a UUID OR a human "
               "identifier (hostname, IP, FQDN, MAC, or serial; an ambiguous "
               "term lists the matches so you can pick the UUID). Shows the "
               "private/public IP split + geo, the posture matrix with the "
               "data sources contributing to each check, each provider's "
               "extended-attribute VALUES + last sync, and any cross-source "
               "conflicts (this folds the old get_asset_source_breakdown). "
               "Use `source` to limit the extended-attribute detail to one "
               "data source, and `field_search` to limit it to fields matching "
               "a term (handy for heavily-integrated assets).",
               {"type": "object", "properties": {
                   "identifier": {
                       "type": "string",
                       "description": ("Asset UUID, or a hostname / IP / FQDN / "
                                       "MAC / serial number to look it up by."),
                   },
                   "asset_id": {
                       "type": "string",
                       "description": "Deprecated alias for `identifier`.",
                   },
                   "source": {
                       "type": "string",
                       "description": ("Limit the per-source extended-attribute "
                                       "output to this data source "
                                       "(case-insensitive)."),
                   },
                   "field_search": {
                       "type": "string",
                       "description": ("Limit extended attributes to keys / "
                                       "values containing this term."),
                   },
               }, "required": []})

    # Deprecation shim → query_assets(mode='list', …).
    async def _list_assets(filters=None, search=None, page=1,
                           limit=_CHAT_LIST_DEFAULT_LIMIT,
                           sort_by=None, sort_dir="asc", fields=None):
        return query_assets(db, field_resolver, mode="list", filters=filters,
                            search=search, page=page, limit=limit,
                            sort_by=sort_by, sort_dir=sort_dir, fields=fields)
    r.register("list_assets", _list_assets,
               "List assets with optional filters, pagination, and sort. "
               "For 'find by person' / 'find by owner' questions, prefer the "
               "`search` parameter — it already searches hostname, IP, MAC, "
               "serial, owner, location, and user_field in one call. Only "
               "use `filters` when a strict equality/operator check is"
               " needed. When asked to list all of something and the result"
               " reports more total than returned, raise `limit` (up to 200)"
               " or paginate with `page`, and tell the user how many of the"
               " total are shown when the full set won't fit."
               " The user can download the full result as CSV from the table,"
               " so you needn't paginate through everything to satisfy"
               " 'give me all of them'.",
               {"type": "object", "properties": {
                   "filters": {
                       "type": "array",
                       "description": (
                           "Structured filters. Each entry is "
                           "{field, op, value}. `field` is a bare field name "
                           "like 'Hostname', 'Category', 'Owner' — case "
                           "insensitive, underscores OK. `op` is one of "
                           "eq, neq, contains, ncontains, starts, ends, gt, "
                           "gte, lt, lte, any, all, none, between, "
                           "within_last, empty, notempty. any/all/none take a "
                           "list value; between takes [lo, hi] (number/date); "
                           "within_last takes a relative duration like '30d' "
                           "(date only). Example: "
                           "[{\"field\":\"Category\",\"op\":\"eq\","
                           "\"value\":\"Workstation\"}]"
                       ),
                   },
                   "search": {
                       "type": "string",
                       "description": (
                           "Free-text search — covers hostname, "
                           "IP, MAC, serial, owner, location, manufacturer, "
                           "model, OS, data_source. Prefer this for person / "
                           "hostname lookups."
                       ),
                   },
                   "page": {"type": "integer", "minimum": 1},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                   "sort_by": {"type": "string"},
                   "sort_dir": {"type": "string", "enum": ["asc", "desc"]},
               }, "required": []})

    # Deprecation shim → query_assets(mode='aggregate', …). query_assets builds
    # the same donut/stacked-bar/table artifact (COUNT) plus per-cell drilldown.
    async def _aggregate_assets(row_field: str, col_field: str | None = None,
                                filters=None):
        return query_assets(db, field_resolver, mode="aggregate",
                            row_field=row_field, col_field=col_field,
                            filters=filters)
    r.register("aggregate_assets", _aggregate_assets,
               "Group assets by one or two fields and return counts. Use "
               "this for 'how many', 'what percent of', or 'breakdown by' "
               "questions — it runs a server-side COUNT, which is far "
               "cheaper than paginating list_assets.",
               {"type": "object", "properties": {
                   "row_field": {
                       "type": "string",
                       "description": (
                           "Field to group rows by. Bare display "
                           "name like 'Category', 'OS Normalized', 'Owner' "
                           "(case-insensitive, underscores OK — "
                           "'os_normalized' works). For fields outside the "
                           "cheat sheet in the system prompt, call "
                           "semantic_suggest first to resolve the right name."
                       ),
                   },
                   "col_field": {
                       "type": "string",
                       "description": (
                           "Optional second field to pivot "
                           "columns by. Same format as row_field."
                       ),
                   },
                   "filters": {
                       "type": "array",
                       "description": (
                           "Optional filters. Same shape as "
                           "list_assets.filters ({field, op, value})."
                       ),
                   },
               }, "required": ["row_field"]})

    # Deprecation shim → query_assets(mode='field_values', …). Chat default 100
    # vs the MCP impl default 50: value discovery wants a fuller list.
    async def _get_field_values(field: str, search=None, limit=100,
                                filters=None):
        return query_assets(db, field_resolver, mode="field_values",
                            field=field, search=search, limit=limit,
                            filters=filters)
    r.register("get_field_values", _get_field_values,
               "Return distinct values of a field with occurrence counts. "
               "Use this to explore what values a field contains ('what "
               "locations exist?', 'which OS versions are deployed?') or "
               "to verify a field is populated. For counts grouped by "
               "another dimension, use aggregate_assets instead.",
               {"type": "object", "properties": {
                   "field": {
                       "type": "string",
                       "description": (
                           "Field to list values for. Bare "
                           "display name like 'Category', 'OS Normalized', "
                           "'Location' (case-insensitive, underscores OK). "
                           "For fields outside the cheat sheet, call "
                           "semantic_suggest first."
                       ),
                   },
                   "search": {
                       "type": "string",
                       "description": (
                           "Optional substring to filter the "
                           "returned values (not the assets)."
                       ),
                   },
                   "limit": {"type": "integer", "maximum": 500},
                   "filters": {
                       "type": "array",
                       "description": (
                           "Optional asset filters (same shape "
                           "as list_assets.filters) applied before distinct."
                       ),
                   },
               }, "required": ["field"]})

    async def _describe_catalog(view="fields", bucket=None, source=None,
                                search=None, offset=0, limit=50) -> str:
        return describe_catalog_impl(
            field_resolver, view=view, bucket=bucket, source=source,
            search=search, offset=offset, limit=limit)
    r.register(
        "describe_catalog", _describe_catalog,
        "Discover queryable fields and operators — the backbone for building "
        "query_assets / run_chart calls.\n"
        "- view='fields' (default): lists catalog fields WITH, per field, the "
        "operators it accepts (which list_fields omitted) plus its encoded_key, "
        "bucket, and data type. Filter with `bucket` (target / advanced / "
        "core_enrichment / extended_attribute / integration_group / tag / "
        "posture / virtual), `source` (e.g. 'Beszel' for that integration's "
        "extended attributes), and `search`. Paged via `offset`/`limit` with an "
        "explicit total + a 'N more' hint.\n"
        "- view='operators': the full operator list with aliases, arity, and "
        "value type.\n"
        "Use this to find data-source-specific fields (e.g. 'Beszel - cpu') and "
        "to confirm which operator a field accepts before filtering.",
        {"type": "object", "properties": {
            "view": {"type": "string", "enum": ["fields", "operators"],
                     "description": "'fields' (default) or 'operators'."},
            "bucket": {"type": "string",
                       "enum": ["target", "advanced", "core_enrichment",
                                "extended_attribute", "integration_group", "tag",
                                "posture", "virtual"],
                       "description": "fields view: restrict to one bucket."},
            "source": {"type": "string",
                       "description": "fields view: restrict to one "
                                      "integration's fields (e.g. 'Beszel')."},
            "search": {"type": "string",
                       "description": "fields view: substring on the field "
                                      "name."},
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        }, "required": []})

    # Deprecation shim → describe_catalog(view='fields'). Kept callable for a
    # release or two so existing callers/evals keep working; describe_catalog
    # adds per-field operators + bucket/source filters + paging.
    async def _list_fields(search=None, limit=50) -> str:
        return describe_catalog_impl(field_resolver, view="fields",
                                     search=search, limit=limit)
    r.register("list_fields", _list_fields,
               "Deprecated alias for describe_catalog (view='fields'). Prefer "
               "describe_catalog — it also lists each field's allowed operators "
               "and supports bucket/source filters + paging.",
               {"type": "object", "properties": {
                   "search": {"type": "string"},
                   "limit": {"type": "integer", "maximum": 200},
               }, "required": []})

    async def _get_asset_history(identifier: str = None, asset_id: str = None,
                                 time_range: str = "30d", start: str = None,
                                 end: str = None, sort: str = "date",
                                 search: str = None, limit: int = 50) -> str:
        ident = identifier or asset_id
        if not ident:
            return ("Provide `identifier` — a UUID, hostname, IP, FQDN, MAC, "
                    "or serial number.")
        resolved, err = resolve_asset_identifier(db, field_resolver, ident)
        if err:
            return err
        return get_asset_history_impl(
            db, asset_id=resolved, time_range=time_range, start=start, end=end,
            sort=sort, search=search, limit=limit)
    r.register("get_asset_history", _get_asset_history,
               "Timeline for ONE asset — chronicle field changes INTERLEAVED "
               "with the alerts that fired on it (the UI shows both). Resolves "
               "a UUID or a human identifier. Use `time_range` (24h/7d/30d/90d) "
               "OR a custom `start`/`end` (ISO dates) for an arbitrary window; "
               "`sort` by date/type/attribute/source and `search` to filter the "
               "merged stream (attribute names/values AND alert titles).",
               {"type": "object", "properties": {
                   "identifier": {
                       "type": "string",
                       "description": ("Asset UUID, or a hostname / IP / FQDN / "
                                       "MAC / serial to look it up by."),
                   },
                   "asset_id": {"type": "string",
                                "description": "Deprecated alias for "
                                               "`identifier`."},
                   "time_range": {"type": "string",
                                  "enum": ["24h", "7d", "30d", "90d"],
                                  "description": ("Preset window; ignored when "
                                                  "start/end are given.")},
                   "start": {"type": "string",
                             "description": ("Custom window start (ISO, e.g. "
                                             "'2026-01-01'); overrides "
                                             "time_range.")},
                   "end": {"type": "string",
                           "description": "Custom window end (ISO)."},
                   "sort": {"type": "string",
                            "enum": ["date", "type", "attribute", "source"]},
                   "search": {"type": "string",
                              "description": ("Case-insensitive substring over "
                                              "the merged stream.")},
                   "limit": {"type": "integer", "maximum": 200},
               }, "required": []})

    # Sectioned dashboard over DashboardService (UI-parity numbers). Registered
    # unconditionally for back-compat (callers expect get_dashboard to exist);
    # it degrades to a clean message if the service isn't wired.
    async def _get_dashboard(section="summary", view="individual", fields=None,
                             full=False, drilldown=False):
        medium, high = get_confidence_thresholds(settings_manager)
        return get_dashboard_impl(
            dashboard_service, db, field_resolver, section=section, view=view,
            fields=fields, full=full, drilldown=drilldown,
            medium=medium, high=high,
        )
    r.register("get_dashboard", _get_dashboard,
               "Homepage dashboard, by `section` — numbers match the UI "
               "exactly (same DashboardService).\n"
               "- summary — totals, added-today, data-quality rating, "
               "visibility, completeness.\n"
               "- posture_tiers — secured/monitored/discovered per asset "
               "category + week-over-week deltas.\n"
               "- confidence_breakdown — confirmed/under_covered/shadows per "
               "category (drillable to confidence_level).\n"
               "- data_source_health — per-source asset count, sync status, "
               "confidence (`view`=individual|grouped; drillable to Data "
               "Source).\n"
               "- reliability — most/least reliable source & category; "
               "`full`=true ranks them all.\n"
               "- data_quality — per-field coverage (`fields`=any catalog "
               "fields) + per-category confidence ranking.\n"
               "Set `drilldown`=true to attach a re-runnable query_assets "
               "filter to each bucket.",
               {"type": "object", "properties": {
                   "section": {
                       "type": "string",
                       "enum": ["summary", "posture_tiers",
                                "confidence_breakdown", "data_source_health",
                                "reliability", "data_quality"],
                       "description": "Which dashboard panel (default summary).",
                   },
                   "view": {"type": "string",
                            "enum": ["individual", "grouped"],
                            "description": "data_source_health: per-integration "
                                           "or grouped logical sources."},
                   "fields": {"type": "array",
                              "description": "data_quality: catalog fields to "
                                             "report completeness for."},
                   "full": {"type": "boolean",
                            "description": "reliability/data_quality: full "
                                           "ranking instead of the top extremes."},
                   "drilldown": {"type": "boolean",
                                 "description": "attach a per-bucket "
                                                "query_assets filter (§3.6)."},
               }, "required": []})

    async def _get_alerts(severity=None, alert_type=None, status=None,
                          time_range="7d", search=None, start=None, end=None,
                          page=1, per_page=None, limit=20,
                          include_summary=False, bucket=None):
        result = get_alerts_impl(
            db, severity=severity, alert_type=alert_type, status=status,
            time_range=time_range, search=search, start=start, end=end,
            page=page, per_page=per_page, limit=limit,
            include_summary=include_summary, bucket=bucket, return_data=True,
        )
        # A validation failure short-circuits to an error string (no data).
        if not isinstance(result, tuple):
            return result
        text, data = result
        # Pick the artifact by intent: a requested timeline shows the chart,
        # otherwise the alert list table (each row drillable via get_alert_detail).
        artifact = None
        if data.get("timeline"):
            artifact = build_alerts_timeline_chart(
                data["timeline"], bucket=data.get("bucket") or "day")
        if artifact is None:
            # Render the whole page (text shows all page rows), so the table
            # and the prose don't disagree when per_page exceeds the default
            # table cap.
            artifact = build_table_from_alerts(
                data["items"], total=data["total_items"],
                page=data["page"], per_page=data["per_page"],
                max_rows=max(data["per_page"], 1))
        return text, artifact
    r.register("get_alerts", _get_alerts,
               "List alerts (the same data as the Alerts page), with real "
               "pagination and counts. Filter by severity / alert_type / "
               "status (each validated — an unknown value errors, never "
               "silently returns nothing); `status` does NOT change the time "
               "window. `search` matches alert title + description. Window: "
               "`time_range` 24h/7d/30d/90d, 'all' for no window, OR custom "
               "ISO `start`/`end`. Page with `page`/`per_page`; the result "
               "reports total_items/total_pages. `include_summary`=true adds "
               "the by-type breakdown; `bucket`=hour|day returns a created-at "
               "timeline (severity-stacked chart). Each row carries the alert "
               "id — call get_alert_detail(alert_id) to see its assets.",
               {"type": "object", "properties": {
                   "severity": {"type": "string",
                                "enum": ["critical", "high", "medium", "low"]},
                   "alert_type": {"type": "string",
                                  "enum": ["security", "compliance",
                                           "operational", "data_quality"]},
                   "status": {"type": "string",
                              "enum": ["active", "acknowledged", "resolved"]},
                   "search": {"type": "string",
                              "description": "Substring match on alert title "
                                             "and description."},
                   "time_range": {"type": "string",
                                  "enum": ["24h", "7d", "30d", "90d", "all"],
                                  "description": "Preset window ('all' = no "
                                                 "window); ignored when "
                                                 "start/end are given."},
                   "start": {"type": "string",
                             "description": "Custom window start (ISO, e.g. "
                                            "'2026-01-01'); overrides "
                                            "time_range."},
                   "end": {"type": "string",
                           "description": "Custom window end (ISO)."},
                   "page": {"type": "integer", "minimum": 1},
                   "per_page": {"type": "integer", "minimum": 1,
                                "maximum": 200,
                                "description": "Page size (default 20, max "
                                               "200)."},
                   "limit": {"type": "integer", "minimum": 1,
                             "description": "Alias for per_page (back-compat); "
                                            "per_page wins if both are given."},
                   "include_summary": {"type": "boolean",
                                       "description": "Add by_type + "
                                                      "by_severity totals."},
                   "bucket": {"type": "string", "enum": ["hour", "day"],
                              "description": "Return a created-at timeline at "
                                             "this granularity."},
               }, "required": []})

    async def _get_pipeline_status(include_history: bool = True,
                                   limit: int = 5) -> str:
        return get_pipeline_status_impl(db, include_history=include_history,
                                        limit=limit)
    r.register("get_pipeline_status", _get_pipeline_status,
               "Return current and recent pipeline run status.",
               {"type": "object", "properties": {
                   "include_history": {"type": "boolean"},
                   "limit": {"type": "integer", "maximum": 20},
               }, "required": []})

    async def _list_tag_rules(enabled_only: bool = False) -> str:
        return list_tag_rules_impl(db, enabled_only=enabled_only)
    r.register("list_tag_rules", _list_tag_rules,
               "List tag rule definitions and execution statistics. "
               "(Prefer list_rules(rule_kind='tag') for full defs + ids.)",
               {"type": "object",
                "properties": {"enabled_only": {"type": "boolean"}},
                "required": []})

    # Deprecation shim → get_dashboard(section='data_quality'). Kept so existing
    # callers/evals using the old name keep working for 1-2 releases.
    async def _get_data_quality(category: str | None = None) -> str:
        medium, high = get_confidence_thresholds(settings_manager)
        return get_dashboard_impl(
            dashboard_service, db, field_resolver, section="data_quality",
            category=category, medium=medium, high=high,
        )
    r.register("get_data_quality", _get_data_quality,
               "Return per-field coverage + per-category confidence ranking. "
               "(Deprecated alias for get_dashboard section='data_quality'; "
               "prefer get_dashboard.)",
               {"type": "object",
                "properties": {"category": {"type": "string"}},
                "required": []})

    # --- New Phase 3 pilot tools ---

    async def _get_alert_detail(alert_id: str, offset: int = 0,
                                limit: int = 50):
        result = get_alert_detail_impl(db, alert_id=alert_id, offset=offset,
                                       limit=limit, return_data=True)
        if not isinstance(result, tuple):
            return result
        text, data = result
        artifact = build_table_from_alert_assets(
            data.get("assets") or [], total=data.get("asset_count") or 0,
            title=data.get("title") or "Alert")
        return text, artifact
    r.register("get_alert_detail", _get_alert_detail,
               "Full detail for a single alert: severity/type/status, the "
               "reason it fired, source (system/custom/prometheus), its "
               "threshold + current value, the affected-asset count by "
               "category, contributing data sources, and the affected assets "
               "(hostname, ip, model) with their asset ids. The asset list is "
               "paged via `offset`/`limit`. `alert_id` must be the UUID from "
               "get_alerts output (`id=...`), not the alert `type` slug.",
               {"type": "object",
                "properties": {
                    "alert_id": {
                        "type": "string",
                        "description": "Alert UUID (e.g. "
                            "'550e8400-e29b-41d4-a716-446655440000') taken from "
                            "the `id=` field in get_alerts output.",
                    },
                    "offset": {"type": "integer", "minimum": 0,
                               "description": "Affected-asset paging offset."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500,
                              "description": "Affected assets per page "
                                             "(default 50)."},
                },
                "required": ["alert_id"]})

    # get_alert_history wraps AlertsService.get_alert_history — register only
    # when that service is wired (a stub that answers "not available" would just
    # be retried by the LLM; absence is more honest, per the service-gated
    # convention). The Alerts drilldown endpoint is jwt-only, so non-admin.
    if alerts_service is not None:
        async def _get_alert_history(alert_id: str, days: int = 90) -> str:
            return get_alert_history_impl(
                alerts_service, alert_id=alert_id, days=days)
        r.register("get_alert_history", _get_alert_history,
                   "Per-run firing history for ONE alert — how its matched "
                   "asset_count and severity moved across recent pipeline runs "
                   "(the Alerts-page drilldown timechart). `alert_id` is the "
                   "UUID from get_alerts output (`id=`); `days` bounds the "
                   "look-back (1-730, default 90). Complements get_alert_detail "
                   "(current state) with the firing trend over time.",
                   {"type": "object", "properties": {
                       "alert_id": {
                           "type": "string",
                           "description": "Alert UUID from get_alerts output "
                                          "(`id=`).",
                       },
                       "days": {"type": "integer", "minimum": 1, "maximum": 730,
                                "description": "Look-back window in days "
                                               "(default 90)."},
                   }, "required": ["alert_id"]})

    def _run_semantic_suggest(**kwargs):
        svc = (
            suggestion_service_getter()
            if suggestion_service_getter
            else suggestion_service
        )
        return semantic_suggest_impl(svc, **kwargs)

    async def _semantic_suggest(query: str, limit: int = 8,
                                min_score: float = 0.5) -> str:
        return cached_semantic_suggest(
            _run_semantic_suggest,
            query=query, limit=limit, min_score=min_score,
        )
    r.register("semantic_suggest", _semantic_suggest,
               "Resolve user terminology to fields via semantic search.",
               {"type": "object", "properties": {
                   "query": {"type": "string"},
                   "limit": {"type": "integer", "maximum": 20},
                   "min_score": {"type": "number",
                                 "minimum": 0, "maximum": 1},
               }, "required": ["query"]})

    async def _get_asset_categories() -> str:
        return get_asset_categories_impl()
    r.register("get_asset_categories", _get_asset_categories,
               "List valid asset categories and subcategories.",
               {"type": "object", "properties": {}, "required": []})

    # --- Phase 6 Tier 1 tools ---

    async def _list_posture_rules(enabled_only: bool = False) -> str:
        return list_posture_rules_impl(db, enabled_only=enabled_only)
    r.register("list_posture_rules", _list_posture_rules,
               "List posture compliance categories and their sub-rules. "
               "(Prefer list_rules(rule_kind='posture') for full defs + ids.)",
               {"type": "object",
                "properties": {"enabled_only": {"type": "boolean"}},
                "required": []})

    # --- Rules (unified tag / alert / posture) — §2.D ---
    if (tag_rule_service is not None or alerts_service is not None
            or posture_category_read_service is not None):
        async def _list_rules(rule_kind, enabled_only=False, rule_type=None,
                              page=1, per_page=20, rule_id=None) -> str:
            return list_rules_impl(
                rule_kind, enabled_only=enabled_only, rule_type=rule_type,
                page=page, per_page=per_page, rule_id=rule_id,
                tag_rule_service=tag_rule_service, alerts_service=alerts_service,
                posture_category_read_service=posture_category_read_service)
        r.register(
            "list_rules", _list_rules,
            "List rule definitions WITH ids + reusable filters. REQUIRED "
            "`rule_kind`: 'tag' | 'alert' | 'posture'. Paged (real total + "
            "overflow hint); `rule_id` returns one rule's full detail. Each "
            "rule's target_filters/compliance_criteria are reusable as "
            "query_assets or preview_rule input. Unifies list_tag_rules / "
            "list_alert_rules / list_posture_rules.",
            {"type": "object", "properties": {
                "rule_kind": {"type": "string",
                              "enum": ["tag", "alert", "posture"]},
                "enabled_only": {"type": "boolean"},
                "rule_type": {"type": "string", "description": (
                    "Optional type filter (tag: compliance/classification; "
                    "alert: security/compliance/operational/data_quality; "
                    "posture: parent category name).")},
                "page": {"type": "integer", "minimum": 1},
                "per_page": {"type": "integer", "minimum": 1, "maximum": 100},
                "rule_id": {"type": "string",
                            "description": "Return one rule's full detail."},
            }, "required": ["rule_kind"]})

    if (tag_preview_service is not None or alerts_service is not None
            or posture_preview_service is not None):
        async def _preview_rule(rule_kind, target_filters,
                                compliance_criteria=None) -> str:
            return preview_rule_impl(
                rule_kind, target_filters, compliance_criteria,
                resolver=field_resolver,
                tag_preview_service=tag_preview_service,
                alerts_service=alerts_service,
                posture_preview_service=posture_preview_service)
        r.register(
            "preview_rule", _preview_rule,
            "Dry-run a candidate rule: how many assets match (plus pass/fail "
            "where the backend computes it). `rule_kind` tag|alert|posture; "
            "`target_filters` [{field, op, value}] (the population); optional "
            "`compliance_criteria` {logic, conditions:[{field, op, value}]} "
            "(the pass/fail test). Delegates to the canonical matcher; returns "
            "a re-runnable filter to list the matches.",
            {"type": "object", "properties": {
                "rule_kind": {"type": "string",
                              "enum": ["tag", "alert", "posture"]},
                "target_filters": {"type": "array", "description": (
                    "[{field, op, value}] — the population to evaluate "
                    "(standard filter grammar).")},
                "compliance_criteria": {"type": "object", "description": (
                    "{logic: 'all'|'any', conditions: [{field, op, value}]} — "
                    "the pass/fail test (tag/posture).")},
            }, "required": ["rule_kind", "target_filters"]},
            admin_required=True)

    if posture_preview_service is not None:
        async def _preview_rule_output(target_filters, output_mode,
                                       compliance_criteria=None,
                                       output_field_id=None,
                                       output_text=None) -> str:
            return preview_rule_output_impl(
                target_filters, compliance_criteria, output_mode=output_mode,
                output_field_id=output_field_id, output_text=output_text,
                resolver=field_resolver,
                posture_preview_service=posture_preview_service)
        r.register(
            "preview_rule_output", _preview_rule_output,
            "Preview a posture sub-rule's resolved OUTPUT values for passing "
            "assets (not just counts). `output_mode` 'field' (set "
            "output_field_id) or 'text' (set output_text). Returns "
            "matched/passed counts + sample hostnames with their output.",
            {"type": "object", "properties": {
                "target_filters": {"type": "array"},
                "compliance_criteria": {"type": "object"},
                "output_mode": {"type": "string", "enum": ["field", "text"]},
                "output_field_id": {"type": "string", "description": (
                    "Field whose value becomes each asset's output "
                    "(output_mode='field').")},
                "output_text": {"type": "string", "description": (
                    "Literal output value (output_mode='text').")},
            }, "required": ["target_filters", "output_mode"]},
            admin_required=True)

    if posture_sub_rule_mgmt_service is not None:
        async def _get_rule_history(rule_kind, rule_id) -> str:
            return get_rule_history_impl(
                rule_kind, rule_id,
                posture_sub_rule_mgmt_service=posture_sub_rule_mgmt_service)
        r.register(
            "get_rule_history", _get_rule_history,
            "Rule change-audit trail. `rule_kind` 'posture_sub' returns the "
            "full create/update/toggle/delete history (before/after state, who, "
            "when); 'tag' and 'alert' have no change-history backend and say so "
            "(use get_audit_trail for HTTP-level rule API changes).",
            {"type": "object", "properties": {
                "rule_kind": {"type": "string",
                              "enum": ["posture_sub", "tag", "alert"]},
                "rule_id": {"type": "string"},
            }, "required": ["rule_kind", "rule_id"]},
            admin_required=True)

    async def _list_enrichment_datasets(
        dataset_id=None, search=None, status=None, page=1,
        per_page=20, sample_limit=5, preview=False, ext_fields=False,
    ) -> str:
        return list_enrichment_datasets_impl(
            db, enrichment_service=enrichment_service,
            dataset_id=dataset_id, search=search, status=status,
            page=page, per_page=per_page, sample_limit=sample_limit,
            preview=preview, ext_fields=ext_fields)
    r.register("list_enrichment_datasets", _list_enrichment_datasets,
               "List enrichment datasets (paged, searchable), or one "
               "dataset's detail + sample rows (pass `dataset_id`). "
               "`preview=true` (with dataset_id) reports how many live assets "
               "the dataset would match before apply; `ext_fields=true` lists "
               "the extended-attribute paths usable as a match_field.",
               {"type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "search": {"type": "string"},
                    "status": {"type": "string"},
                    "page": {"type": "integer", "minimum": 1},
                    "per_page": {"type": "integer", "minimum": 1,
                                 "maximum": 100},
                    "sample_limit": {"type": "integer", "minimum": 0,
                                     "maximum": 100},
                    "preview": {"type": "boolean"},
                    "ext_fields": {"type": "boolean"},
                },
                "required": []},
               admin_required=True)

    if dashboard_service is not None:
        # Deprecation shim → get_dashboard(section='reliability').
        async def _get_reliability(full: bool = False):
            return get_dashboard_impl(
                dashboard_service, db, field_resolver, section="reliability",
                full=full,
            )
        r.register("get_reliability", _get_reliability,
                   "Return the most and least reliable data source and asset "
                   "category (full=true ranks them all). (Deprecated alias for "
                   "get_dashboard section='reliability'; prefer get_dashboard.)",
                   {"type": "object",
                    "properties": {"full": {"type": "boolean"}},
                    "required": []})

        async def _get_dashboard_trend(kind="posture", days=30, start=None,
                                       end=None, metric="srv_edr", bucket="day"):
            return get_dashboard_trend_impl(
                dashboard_service, kind=kind, days=days, start=start, end=end,
                metric=metric, bucket=bucket,
            )
        r.register("get_dashboard_trend", _get_dashboard_trend,
                   "Dashboard time-series by `kind`: posture (tier levels), "
                   "coverage (one posture-coverage metric: srv_edr/ws_edr/"
                   "mobile_mdm/vuln), or asset|timeline (added/removed). Window "
                   "= `days` (1-365) or `start`/`end` (YYYY-MM-DD, anchored to "
                   "today). `bucket`=day|week|month rolls daily points up "
                   "(flows summed, levels period-end).",
                   {"type": "object", "properties": {
                       "kind": {"type": "string",
                                "enum": ["posture", "coverage", "asset",
                                         "timeline"]},
                       "days": {"type": "integer", "minimum": 1,
                                "maximum": 365},
                       "start": {"type": "string",
                                 "description": "YYYY-MM-DD (with end)."},
                       "end": {"type": "string",
                               "description": "YYYY-MM-DD (with start)."},
                       "metric": {"type": "string",
                                  "enum": ["srv_edr", "ws_edr", "mobile_mdm",
                                           "vuln"],
                                  "description": "coverage kind only."},
                       "bucket": {"type": "string",
                                  "enum": ["day", "week", "month"]},
                   }, "required": []})

        async def _get_relationship_graph(version="v1", category=None,
                                          subcategory=None, os=None,
                                          integration_id=None):
            return get_relationship_graph_impl(
                dashboard_service, version=version, category=category,
                subcategory=subcategory, os=os, integration_id=integration_id,
            )
        r.register("get_relationship_graph", _get_relationship_graph,
                   "Cross-entity relationship graph (category ↔ subcategory ↔ "
                   "OS ↔ data source). version='v1' supports filters "
                   "(category/subcategory/os/integration_id); version='v2' is "
                   "any-to-any. Links resolve to entity names; the full graph "
                   "rides the artifact meta.",
                   {"type": "object", "properties": {
                       "version": {"type": "string", "enum": ["v1", "v2"]},
                       "category": {"type": "string",
                                    "description": "v1 filter."},
                       "subcategory": {"type": "string",
                                       "description": "v1 filter."},
                       "os": {"type": "string", "description": "v1 filter."},
                       "integration_id": {"type": "string",
                                          "description": "v1 filter (UUID)."},
                   }, "required": []})

    async def _list_reports(search: str | None = None, page: int = 1,
                            limit: int = 50, author: str | None = None,
                            sort: str = "updated",
                            sort_dir: str = "desc") -> str:
        return list_reports_impl(db, search=search, page=page, limit=limit,
                                 author=author, sort=sort, sort_dir=sort_dir)
    r.register("list_reports", _list_reports,
               "List saved reports (paginated, with a REAL total + how many "
               "more remain). `search` matches title/description; `author` "
               "matches the creating user's username; `sort` by "
               "updated/created/title with `sort_dir`. Pair with get_report "
               "(definitions) or run_report (executes them).",
               {"type": "object", "properties": {
                   "search": {"type": "string"},
                   "page": {"type": "integer", "minimum": 1},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                   "author": {"type": "string",
                              "description": "Partial creating-username match."},
                   "sort": {"type": "string",
                            "enum": ["updated", "created", "title"]},
                   "sort_dir": {"type": "string", "enum": ["asc", "desc"]},
               }, "required": []})

    async def _get_report(report_id: str) -> str:
        return get_report_impl(db, report_id=report_id)
    r.register("get_report", _get_report,
               "Show a saved report's definition — each chart's persisted "
               "queryConfig (type, dimensions, metric/aggregate, window, "
               "filters). This DESCRIBES the charts; use run_report to EXECUTE "
               "them and see the data.",
               {"type": "object",
                "properties": {"report_id": {"type": "string"}},
                "required": ["report_id"]})

    # --- Phase 7 Tier 2 admin tools ---

    async def _get_audit_trail(
        username=None, method=None, path=None,
        status_code=None, status_class=None, start_date=None, end_date=None,
        page=1, per_page=50,
    ) -> str:
        return get_audit_trail_impl(db, username=username, method=method,
                                    path=path, status_code=status_code,
                                    status_class=status_class,
                                    start_date=start_date, end_date=end_date,
                                    page=page, per_page=per_page)
    r.register("get_audit_trail", _get_audit_trail,
               "Query the API audit trail (paginated, real total). username "
               "and path are partial matches; filter by status_code OR "
               "status_class (e.g. '5xx'); start_date/end_date are ISO-8601. "
               "Entries include request_body/response_summary.",
               {"type": "object", "properties": {
                   "username": {"type": "string"},
                   "method": {"type": "string"},
                   "path": {"type": "string"},
                   "status_code": {"type": "integer"},
                   "status_class": {"type": "string",
                                    "enum": ["1xx", "2xx", "3xx", "4xx",
                                             "5xx"]},
                   "start_date": {"type": "string"},
                   "end_date": {"type": "string"},
                   "page": {"type": "integer", "minimum": 1},
                   "per_page": {"type": "integer", "minimum": 1,
                                "maximum": 100},
               }, "required": []},
               admin_required=True)

    # Service-dependent tools register only when their backing service is
    # configured on the app. A missing service means misconfiguration — skip
    # advertising the tool rather than letting the LLM call it and receive a
    # "not available" string back (which it'd just retry).
    if pipeline_service is not None:
        async def _list_pipeline_runs(
            run_id=None, status=None, start_date=None, end_date=None,
            sort="created", sort_dir="desc", page=1, per_page=20,
            phases=False,
        ) -> str:
            return list_pipeline_runs_impl(
                pipeline_service, phase_service=pipeline_phase_service,
                run_id=run_id, status=status, start_date=start_date,
                end_date=end_date, sort=sort, sort_dir=sort_dir,
                page=page, per_page=per_page, phases=phases)
        r.register("list_pipeline_runs", _list_pipeline_runs,
                   "List asset-generation pipeline runs (with run IDs) plus "
                   "the current run status, or full detail for one run. Pass "
                   "`run_id` for batch/stage/chronicle detail; add "
                   "`phases=true` for the per-stage timing breakdown (p95 + "
                   "slow flags). Filter with `status`/`start_date`/`end_date` "
                   "and order with `sort`/`sort_dir`.",
                   {"type": "object", "properties": {
                       "run_id": {"type": "string"},
                       "status": {"type": "string",
                                  "enum": ["pending", "running", "completed",
                                           "failed"]},
                       "start_date": {"type": "string"},
                       "end_date": {"type": "string"},
                       "sort": {"type": "string",
                                "enum": ["created", "started", "completed",
                                         "duration", "status"]},
                       "sort_dir": {"type": "string",
                                    "enum": ["asc", "desc"]},
                       "page": {"type": "integer", "minimum": 1},
                       "per_page": {"type": "integer", "minimum": 1,
                                    "maximum": 100},
                       "phases": {"type": "boolean"},
                   }, "required": []},
                   admin_required=True)

    if scheduler_service is not None:
        async def _get_scheduler_status(
            scope: str = "all", job_id=None, limit=100,
            stuck_hours=1, failure_hours=24,
        ) -> str:
            return get_scheduler_status_impl(
                scheduler_service, db, scope=scope, job_id=job_id,
                limit=limit, stuck_hours=stuck_hours,
                failure_hours=failure_hours)
        r.register("get_scheduler_status", _get_scheduler_status,
                   "Scheduler observability. `scope`: health, statistics "
                   "(per-job aggregates), alerts (stuck/failed jobs), history "
                   "(full execution log), job_health (per-job health, needs "
                   "`job_id`), or all. `limit` (≤1000) caps rows; "
                   "`stuck_hours`/`failure_hours` tune the alert windows.",
                   {"type": "object", "properties": {
                       "scope": {"type": "string",
                                 "enum": ["all", "health", "statistics",
                                          "alerts", "history", "job_health"]},
                       "job_id": {"type": "string"},
                       "limit": {"type": "integer", "minimum": 1,
                                 "maximum": 1000},
                       "stuck_hours": {"type": "number",
                                       "exclusiveMinimum": 0},
                       "failure_hours": {"type": "number",
                                         "exclusiveMinimum": 0},
                   }, "required": []},
                   admin_required=True)

    if integration_service is not None:
        async def _get_integration(integration, view="status",
                                   timeline_days=30, schedule_count=5) -> str:
            # Confidence thresholds are only consumed by the insights view, so
            # only read them there (avoids a settings read + failure surface on
            # the common status/config/timeline/schedule/stats views).
            medium = high = None
            if view == "insights" and settings_manager is not None:
                medium, high = get_confidence_thresholds(settings_manager)
            return get_integration_impl(
                integration_service, dashboard_service,
                integration=integration, view=view,
                timeline_days=timeline_days, schedule_count=schedule_count,
                medium=medium, high=high)
        r.register("get_integration", _get_integration,
                   "Inspect ONE integration (resolved by name — "
                   "case-insensitive — or UUID). view='status' (default): "
                   "health, asset count, last sync/run (from the same "
                   "dashboard the UI uses), plus last-error and "
                   "connection-test state. 'config': full connection config + "
                   "field mapping + transformations + data filters, with "
                   "credentials ALWAYS redacted. 'insights': "
                   "stale/missing-attribute indicators. 'timeline': recent "
                   "sync activity (timeline_days look-back). 'schedule': cron + "
                   "next runs. 'stats': batch/record/asset aggregates. For the "
                   "whole fleet use list_integrations or get_dashboard "
                   "(section='data_source_health').",
                   {"type": "object", "properties": {
                       "integration": {
                           "type": "string",
                           "description": "Integration name (case-insensitive) "
                                          "or UUID.",
                       },
                       "view": {"type": "string",
                                "enum": ["status", "config", "insights",
                                         "timeline", "schedule", "stats"]},
                       "timeline_days": {"type": "integer", "minimum": 1,
                                         "maximum": 365,
                                         "description": "timeline view: "
                                                        "look-back window."},
                       "schedule_count": {"type": "integer", "minimum": 1,
                                          "maximum": 20,
                                          "description": "schedule view: how "
                                                         "many upcoming runs."},
                   }, "required": ["integration"]},
                   admin_required=True)

        # --- Integration ingestion-debug & catalog (read-only, admin) ---
        async def _list_integration_batches(integration, status=None,
                                            limit=50, offset=0) -> str:
            return list_integration_batches_impl(
                integration_service, integration=integration,
                status=status, limit=limit, offset=offset)
        r.register("list_integration_batches", _list_integration_batches,
                   "List ingestion batch runs for one integration (resolved by "
                   "name — case-insensitive — or UUID): per-batch status, "
                   "start/complete time, duration, raw record count, mapped "
                   "count, assets created, and any error message. The core "
                   "'why did this sync fail / how much did the last run pull' "
                   "tool. Paginated with a real total.",
                   {"type": "object", "properties": {
                       "integration": {
                           "type": "string",
                           "description": "Integration name (case-insensitive) "
                                          "or UUID.",
                       },
                       "status": {"type": "string",
                                  "enum": ["running", "mapping", "completed",
                                           "failed", "partial"]},
                       "limit": {"type": "integer", "minimum": 1,
                                 "maximum": 200},
                       "offset": {"type": "integer", "minimum": 0},
                   }, "required": ["integration"]},
                   admin_required=True)

        async def _get_batch(batch_id, view="detail", limit=100,
                             offset=0) -> str:
            return get_batch_impl(
                integration_service, batch_id=batch_id, view=view,
                limit=limit, offset=offset)
        r.register("get_batch", _get_batch,
                   "Inspect one ingestion batch by its UUID (from "
                   "list_integration_batches). view='detail' for the batch "
                   "summary, 'raw' for the raw source records as ingested, "
                   "'mapped' for the records after field mapping. Records are "
                   "paginated with a real total.",
                   {"type": "object", "properties": {
                       "batch_id": {
                           "type": "string",
                           "description": "Batch UUID from "
                                          "list_integration_batches output.",
                       },
                       "view": {"type": "string",
                                "enum": ["detail", "raw", "mapped"]},
                       "limit": {"type": "integer", "minimum": 1,
                                 "maximum": 500},
                       "offset": {"type": "integer", "minimum": 0},
                   }, "required": ["batch_id"]},
                   admin_required=True)

        async def _get_integration_mapping(integration, view="mapping_preview",
                                           status="all", limit=50,
                                           offset=0) -> str:
            return get_integration_mapping_impl(
                integration_service, mapping_preview_service,
                integration=integration, view=view, status=status,
                limit=limit, offset=offset)
        r.register("get_integration_mapping", _get_integration_mapping,
                   "Show how an integration's raw records map to canonical "
                   "asset fields (integration resolved by name or UUID). "
                   "view='mapping_preview' for the per-record raw->canonical "
                   "mapping plus which records would be DROPPED and why "
                   "(status filter all/ok/dropped); 'raw_sample' for raw "
                   "records plus the inferred source schema; 'schema' for just "
                   "the inferred field schema. Answers 'why did this source "
                   "drop N records' / 'what fields does this source expose'.",
                   {"type": "object", "properties": {
                       "integration": {
                           "type": "string",
                           "description": "Integration name (case-insensitive) "
                                          "or UUID.",
                       },
                       "view": {"type": "string",
                                "enum": ["mapping_preview", "raw_sample",
                                         "schema"]},
                       "status": {"type": "string",
                                  "enum": ["all", "ok", "dropped"],
                                  "description": "mapping_preview only: all "
                                                 "records, only kept (ok), or "
                                                 "only dropped."},
                       "limit": {"type": "integer", "minimum": 1,
                                 "maximum": 500},
                       "offset": {"type": "integer", "minimum": 0},
                   }, "required": ["integration"]},
                   admin_required=True)

        async def _list_integrations(view="integrations") -> str:
            return list_integrations_impl(
                integration_service, integration_group_service, view=view)
        r.register("list_integrations", _list_integrations,
                   "The integration catalog. view='integrations' for the "
                   "configured integrations (with latest-batch record/asset "
                   "counts), 'groups' for integration groups and their "
                   "members, 'connectors' for the available connector types "
                   "and their config schema, 'stats' for global integration "
                   "counts and batch/record/mapped/asset totals. Read-only "
                   "admin catalog — for one integration's health/config use "
                   "get_integration.",
                   {"type": "object", "properties": {
                       "view": {"type": "string",
                                "enum": ["integrations", "groups",
                                         "connectors", "stats"]},
                   }, "required": []},
                   admin_required=True)

        async def _get_integration_schedule(integration, count=5) -> str:
            return get_integration_schedule_impl(
                integration_service, integration=integration, count=count)
        r.register("get_integration_schedule", _get_integration_schedule,
                   "Show an integration's ingestion schedule (resolved by name "
                   "or UUID): whether scheduling is enabled, the cron "
                   "expression with a human description, timezone, the stored "
                   "next_run_at, whether it is currently running, and the next "
                   "N computed runs.",
                   {"type": "object", "properties": {
                       "integration": {
                           "type": "string",
                           "description": "Integration name (case-insensitive) "
                                          "or UUID.",
                       },
                       "count": {"type": "integer", "minimum": 1, "maximum": 20,
                                 "description": "How many upcoming runs to "
                                                "compute (max 20)."},
                   }, "required": ["integration"]},
                   admin_required=True)

    if alerts_service is not None:
        async def _list_alert_rules() -> str:
            return list_alert_rules_impl(alerts_service)
        r.register("list_alert_rules", _list_alert_rules,
                   "List alert rules with type, severity, source, and enabled "
                   "state. (Prefer list_rules(rule_kind='alert') for full "
                   "defs + ids.)",
                   {"type": "object", "properties": {}, "required": []},
                   admin_required=True)

    if settings_manager is not None:
        async def _get_settings(category=None, key=None, view="values") -> str:
            return get_settings_impl(settings_manager, category=category,
                                     key=key, view=view)
        r.register("get_settings", _get_settings,
                   "Return system settings. view=values (default): all "
                   "categories, one category, or a single key. view=schema: "
                   "setting definitions. view=reliability: per-source "
                   "reliability scores. view=email: SMTP config (password "
                   "redacted). view=security-effective: resolved "
                   "public_base_url + source. Credential values are always "
                   "server-redacted.",
                   {"type": "object", "properties": {
                       "category": {"type": "string"},
                       "key": {"type": "string"},
                       "view": {"type": "string",
                                "enum": ["values", "schema", "reliability",
                                         "email", "security-effective"]},
                   }, "required": []},
                   admin_required=True)

    # query_metrics registers only when the Prometheus proxy is wired
    # (MONITORING_ENABLED). Without it the tool would only ever return
    # "not available", so don't advertise it (mirrors the other
    # service-gated tools above).
    if monitoring_service is not None:
        async def _query_metrics(group, time_range="1h", start=None, end=None,
                                 step=None, rate_window=None):
            return query_metrics_impl(
                monitoring_service, group=group, time_range=time_range,
                start=start, end=end, step=step, rate_window=rate_window)
        r.register("query_metrics", _query_metrics,
                   "Infrastructure/observability metrics from Prometheus, one "
                   "dashboard group at a time. `group`: system (HTTP "
                   "req/error/latency), database (PostgreSQL), redis, host "
                   "(node CPU/mem/disk/net), containers (per-container "
                   "CPU/mem/net), pipeline (asset-generation runs + scheduler "
                   "heartbeat). Window: a `time_range` preset (1h/6h/24h/7d) OR "
                   "custom `start`+`end` (Unix seconds or ISO-8601); "
                   "`step`/`rate_window` are optional overrides. Returns a "
                   "per-series latest/min/max/avg summary (time-series are "
                   "summarized into a table — line/gauge artifacts aren't "
                   "supported yet).",
                   {"type": "object", "properties": {
                       "group": {"type": "string",
                                 "enum": ["system", "database", "redis", "host",
                                          "containers", "pipeline"]},
                       "time_range": {"type": "string",
                                      "enum": ["1h", "6h", "24h", "7d"]},
                       "start": {"type": "string",
                                 "description": "Custom range start (Unix "
                                                "seconds or ISO-8601); requires "
                                                "end too."},
                       "end": {"type": "string",
                               "description": "Custom range end."},
                       "step": {"type": "string",
                                "description": "Query resolution, e.g. '5m'."},
                       "rate_window": {"type": "string",
                                       "description": "rate() window, e.g. "
                                                      "'15m'."},
                   }, "required": ["group"]},
                   admin_required=True)

    # Chatbot self-observability. The repo only needs the db adapter (the same
    # ChatRepository the /chat/admin/usage route uses), so register it
    # unconditionally — build_registry only runs when chat is enabled anyway.
    _chat_usage_repo = ChatRepository(db)

    async def _get_chatbot_usage(days=7):
        return get_chatbot_usage_impl(_chat_usage_repo, days=days)
    r.register("get_chatbot_usage", _get_chatbot_usage,
               "Chatbot self-observability for the last `days` days "
               "(1, 7, 30, or 90): totals (messages, sessions, active users, "
               "tokens in/out), top users, token spend by provider/model, and "
               "tool calls with error/timeout/denied counts + a computed error "
               "rate. Answers 'token spend by model' / 'which tools error "
               "most'.",
               {"type": "object", "properties": {
                   "days": {"type": "integer",
                            "description": "Look-back window: 1, 7, 30, or 90 "
                                           "days (default 7)."},
               }, "required": []},
               admin_required=True)

    async def _get_field_registry(field_name=None) -> str:
        return get_field_registry_impl(db, field_name=field_name)
    r.register("get_field_registry", _get_field_registry,
               "List asset field definitions, or return details for one "
               "field.",
               {"type": "object",
                "properties": {"field_name": {"type": "string"}},
                "required": []},
               admin_required=True)

    # --- Unified Fields (Settings → Data): user-defined merges that become ---
    # --- queryable virtual:<slug> catalog fields. Read-only; the endpoints ---
    # --- are jwt-only (readable by any user), so non-admin. Gated on the   ---
    # --- service like the other service-backed tools.                      ---
    if unified_fields_service is not None:
        async def _list_unified_fields() -> str:
            return list_unified_fields_impl(unified_fields_service)
        r.register("list_unified_fields", _list_unified_fields,
                   "List the user-defined Unified Fields (Settings → Data → "
                   "Unified Fields): each field's merge function, source "
                   "fields, resolved data type, and encoded catalog key. Each "
                   "becomes a queryable virtual:<slug> catalog field usable in "
                   "query_assets / describe_catalog.",
                   {"type": "object", "properties": {}, "required": []})

        async def _get_unified_field(field_id: str) -> str:
            return get_unified_field_impl(
                unified_fields_service, field_id=field_id)
        r.register("get_unified_field", _get_unified_field,
                   "Full detail for ONE Unified Field by its UUID (from "
                   "list_unified_fields): name, merge function, source fields, "
                   "resolved data type, and encoded catalog key.",
                   {"type": "object", "properties": {
                       "field_id": {
                           "type": "string",
                           "description": "Unified field UUID from "
                                          "list_unified_fields output.",
                       },
                   }, "required": ["field_id"]})

    # --- Chronicle-based aggregations (system-wide, not per-asset) ---

    async def _list_recent_changes(time_range: str = "7d",
                                   event_type: str | None = None,
                                   attribute: str | None = None,
                                   limit: int = 20,
                                   start: str | None = None,
                                   end: str | None = None) -> str:
        return list_recent_changes_impl(
            db,
            time_range=time_range, event_type=event_type,
            attribute=attribute, limit=limit, start=start, end=end,
        )
    r.register("list_recent_changes", _list_recent_changes,
               "System-wide change feed over a time window (rolls up the "
               "chronicle). Use this for 'what changed recently?', 'which "
               "attributes drift most?', or 'how many new / removed assets "
               "this week?' questions. Window = `time_range` (24h/7d/30d/90d) "
               "OR a custom `start`/`end` (ISO dates). An `attribute` that "
               "never changed in the window is flagged (likely a typo) instead "
               "of silently returning zero. `get_asset_history` is per-asset "
               "only — don't loop it.",
               {"type": "object", "properties": {
                   "time_range": {"type": "string",
                                  "enum": ["24h", "7d", "30d", "90d"],
                                  "description": "Preset window; ignored when "
                                                 "start/end are given."},
                   "event_type": {"type": "string",
                                  "enum": ["attribute_updated",
                                           "asset_created", "asset_removed",
                                           "asset_readded",
                                           "asset_reactivated",
                                           "asset_retired",
                                           "asset_went_inactive"]},
                   "attribute": {"type": "string",
                                 "description":
                                     "Optional. Only count events where "
                                     "this attribute name appears in "
                                     "changed_attributes (e.g. 'owner', "
                                     "'os_normalized'). Validated against the "
                                     "window's actual change vocabulary."},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                   "start": {"type": "string",
                             "description": "Custom window start (ISO, e.g. "
                                            "'2026-01-01'); overrides "
                                            "time_range."},
                   "end": {"type": "string",
                           "description": "Custom window end (ISO)."},
               }, "required": []})

    async def _get_asset_trend(time_range: str = "30d",
                               bucket: str = "day") -> str:
        return get_asset_trend_impl(db,
                                    time_range=time_range, bucket=bucket)
    r.register("get_asset_trend", _get_asset_trend,
               "Time-bucketed asset additions and removals. Use for 'new "
               "assets per week', 'when did we onboard these', 'fleet "
               "growth over 90 days'. `aggregate_assets` cannot bucket by "
               "time interval.",
               {"type": "object", "properties": {
                   "time_range": {"type": "string",
                                  "enum": ["24h", "7d", "30d", "90d"]},
                   "bucket": {"type": "string",
                              "enum": ["day", "week", "month"]},
               }, "required": []})

    # --- EOL exposure ---

    async def _list_eol_assets(scope: str = "all",
                               within_days: int | None = None,
                               breakdown_by: str = "os_normalized",
                               start: str | None = None,
                               end: str | None = None,
                               offset: int = 0, limit: int = 15):
        return list_eol_assets_impl(
            db, scope=scope, within_days=within_days,
            breakdown_by=breakdown_by, start=start, end=end,
            offset=offset, limit=limit, with_artifact=True,
        )
    r.register("list_eol_assets", _list_eol_assets,
               "EOL exposure roll-up with urgency buckets (overdue / next "
               "30d / 31-90d / 91-365d / beyond) and a top-N breakdown by "
               "OS, category, owner, or location. Use for 'what's past "
               "support?', 'how many Windows 10 machines EOL in the next "
               "30 days?', 'EOL exposure by category'. Richer than "
               "aggregate_assets(row_field='OS EOL Status') — that only "
               "gives enum counts, no date bucketing. Bound the 'upcoming' "
               "horizon with `within_days` OR a custom `start`/`end` "
               "(ISO dates). Page the breakdown with `offset`/`limit` (a "
               "total-distinct line reports how many values exist). Each "
               "breakdown row and urgency bucket is drillable — 'show me "
               "those N' re-runs as a query_assets call.",
               {"type": "object", "properties": {
                   "scope": {"type": "string",
                             "enum": ["past", "upcoming", "all"]},
                   "within_days": {"type": "integer",
                                   "minimum": 1, "maximum": 3650,
                                   "description": (
                                       "Optional horizon for the "
                                       "'upcoming' / 'all' scopes (overridden "
                                       "by start/end)."
                                   )},
                   "breakdown_by": {"type": "string",
                                    "enum": ["os_normalized", "category",
                                             "owner", "location"]},
                   "start": {"type": "string",
                             "description": "ISO date lower bound on "
                                            "os_eol_date (upcoming window)."},
                   "end": {"type": "string",
                           "description": "ISO date upper bound on "
                                          "os_eol_date (upcoming window)."},
                   "offset": {"type": "integer", "minimum": 0,
                              "description": "Breakdown-table row offset."},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 100,
                             "description": "Breakdown-table rows per page "
                                            "(default 15)."},
               }, "required": []})

    # --- Source coverage & lineage ---

    async def _get_source_coverage(source_a: str | None = None,
                                   source_b: str | None = None,
                                   by_category: bool = False):
        return get_source_coverage_impl(
            db, source_a=source_a, source_b=source_b,
            by_category=by_category, with_artifact=True)
    r.register("get_source_coverage", _get_source_coverage,
               "Coverage and set-differences across data sources. Call "
               "with no args for the multi-vs-single-source distribution. "
               "Call with source_a for that source's asset count. Call "
               "with both source_a and source_b for the A-only / B-only / "
               "A∩B set sizes ('which workstations aren't in Defender', "
               "'Intune assets missing from Entra'). Call with "
               "by_category=true for per-category distinct-source coverage. "
               "Source names are matched case-insensitively against the "
               "exact strings in assets.data_source[]; a source that matches "
               "zero assets is flagged loudly (likely a typo). Each set / "
               "bucket is drillable via query_assets.",
               {"type": "object", "properties": {
                   "source_a": {"type": "string"},
                   "source_b": {"type": "string"},
                   "by_category": {"type": "boolean",
                                   "description": "Per-category "
                                                  "distinct-source view."},
               }, "required": []})

    # The standalone per-source breakdown is now folded into get_asset (which
    # shows each provider's extended-attribute VALUES side-by-side + the
    # cross-source conflict roll-up). Kept as a thin deprecation shim onto
    # get_asset so existing callers/evals using the old name keep working.
    async def _get_asset_source_breakdown(asset_id: str):
        return await _get_asset(identifier=asset_id)
    r.register("get_asset_source_breakdown", _get_asset_source_breakdown,
               "Per-source view of one asset (what each ingesting source "
               "reports, side-by-side, with conflicts flagged). Deprecated "
               "alias for get_asset, which now includes this — prefer "
               "get_asset.",
               {"type": "object", "properties": {
                   "asset_id": {"type": "string"},
               }, "required": ["asset_id"]})

    # --- Posture compliance matrix (§2.D) ---

    if posture_repository is not None:
        async def _get_compliance_matrix(category=None, asset_category=None,
                                         group_by="none", drilldown=False):
            return get_compliance_matrix_impl(
                posture_repository=posture_repository,
                posture_category_read_service=posture_category_read_service,
                posture_preview_service=posture_preview_service,
                query_engine=query_engine, db=db,
                category=category, asset_category=asset_category,
                group_by=group_by, drilldown=drilldown)
        r.register(
            "get_compliance_matrix", _get_compliance_matrix,
            "Posture compliance as a covered/total matrix. group_by='none' "
            "(default): per posture category covered/fail/n_a/total. "
            "group_by='asset_category': the posture × asset-category matrix, "
            "one cell per (posture, asset). group_by='sub_rule': per sub-rule "
            "matched/pass/fail (computed live). Optional `category` (one "
            "posture category, name or id) / `asset_category` scope. Each cell "
            "carries a re-runnable drilldown filter; `drilldown=true` also "
            "resolves the matching asset ids.",
            {"type": "object", "properties": {
                "category": {"type": "string", "description": (
                    "Optional posture category (name or id, e.g. 'secured').")},
                "asset_category": {"type": "string", "description": (
                    "Optional asset category to scope to (e.g. 'server').")},
                "group_by": {"type": "string",
                             "enum": ["none", "asset_category", "sub_rule"]},
                "drilldown": {"type": "boolean"},
            }, "required": []})

    # --- Posture compliance (per-asset state roll-up) ---

    async def _get_posture_compliance(
        category: str | None = None,
        asset_category: str | None = None,
    ) -> str:
        return get_posture_compliance_impl(
            db, category=category, asset_category=asset_category,
        )
    r.register("get_posture_compliance", _get_posture_compliance,
               "Per-asset posture-compliance state counts (pass / fail / "
               "n/a / null) across the posture categories (secured, "
               "monitored, domain_joined, device_management, "
               "vulnerability_scanning). Optionally scope to one posture "
               "category and/or one asset category. (Prefer "
               "get_compliance_matrix for the category×category matrix, "
               "per-sub-rule breakdown, and drilldown.) `list_posture_rules` "
               "returns rule DEFINITIONS only; this tool returns per-asset "
               "STATE.",
               {"type": "object", "properties": {
                   "category": {"type": "string",
                                "description":
                                    "Optional posture category "
                                    "(e.g. 'secured', 'monitored')."},
                   "asset_category": {"type": "string",
                                      "description":
                                          "Optional assets.category "
                                          "filter (e.g. 'server', "
                                          "'workstation')."},
               }, "required": []})

    # --- Alerts per asset (reverse of get_alerts) ---

    async def _list_alerts_for_asset(asset_id: str | None = None,
                                     status: str = "active",
                                     limit: int = 10) -> str:
        return list_alerts_for_asset_impl(
            db, asset_id=asset_id, status=status, limit=limit,
        )
    r.register("list_alerts_for_asset", _list_alerts_for_asset,
               "Asset-first alert view. With asset_id: list the alerts "
               "referencing that asset. Without asset_id: return the "
               "top-N assets ranked by number of alerts (e.g. 'which hosts "
               "have the most alerts?'). `get_alerts` is alert-first and "
               "can't answer these cheaply.",
               {"type": "object", "properties": {
                   "asset_id": {"type": "string"},
                   "status": {"type": "string",
                              "enum": ["active", "acknowledged",
                                       "resolved", "any"]},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 50},
               }, "required": []})

    # find_tools: the routing safety net. Returns gated tools matching a free-
    # text capability query so the model can discover and (via the loop) load
    # one when the advertised core doesn't cover the question. The loop reads
    # the same router to actually load them; this handler only renders the list.
    async def _find_tools(query: str) -> str:
        # Resolve the router from the contextvar (set per-turn in
        # service._stream_inner), NOT current_app: this handler runs in the
        # daemon worker thread with no Flask app context, where current_app
        # raises RuntimeError — getattr(current_app, ..., None) does NOT swallow
        # that (only AttributeError), so the old code errored on every call and
        # the routing recovery net was dead whenever the flag was on (PR #803).
        from src.api.blueprints.chat.tool_router import (
            current_chat_role, current_tool_router)
        router = current_tool_router.get()
        if router is None:
            return "Tool discovery is unavailable."
        role = current_chat_role.get()
        matches = router.find(query, role)
        if not matches:
            return f"No additional tools match {query!r}."
        lines = [f"- {s.name}: {((s.description or '').splitlines() or [''])[0]}"
                 for s in matches]
        return ("Loaded these tools — you can now call them directly:\n"
                + "\n".join(lines))

    r.register("find_tools", _find_tools,
               "Discover and load a tool for a capability that isn't in your "
               "current tool list. Call this with a short description of what "
               "you need (e.g. 'integration connector health', 'scheduler "
               "jobs', 'audit log') BEFORE telling the user you can't do "
               "something — the matching tools become callable on your next "
               "step.",
               {"type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"]})

    # Deprecated aliases: keep them invocable (a model may still call the old
    # name out of habit — erroring would just cost a retry iteration), but stop
    # advertising their schemas to the LLM. ~1.5k input tokens saved per turn.
    # Canonical replacements: query_assets (list / aggregate / field_values),
    # describe_catalog (view='fields'), get_dashboard (section=…), get_asset.
    r.hide("search_assets", "list_assets", "aggregate_assets", "get_field_values",
           "list_fields", "get_data_quality", "get_reliability",
           "get_asset_source_breakdown")

    return r
