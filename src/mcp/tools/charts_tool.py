# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ``run_chart`` / ``run_report`` analytics tools (plan §2.B).

``run_chart`` is the ONLY chat path to ``POST /api/charts/query`` and the
analytics counterpart to ``query_assets``. It is a PASSTHROUGH to the existing
charts engine — it builds a ``ChartQueryRequest`` and runs it through the SAME
``QueryBuilder`` + ``ChartRepository`` + ``transform_results`` the HTTP route
uses (``src/api/blueprints/charts/routes.py``). It does NOT reimplement the
charts compiler; the charts engine is a distinct compiler with its own joins,
kept separate from ``query_assets`` deliberately (§2.B rationale).

The model speaks ONE filter grammar everywhere (the standard §3.1
``{field, op, value}``); the charts engine uses its own ``FilterOperator`` wire
enum (``equals``/``not_equals``/…). The translation seam is hidden here (§3.4):
a small static code→enum map, applied at the boundary. Unknown or
untranslatable input is surfaced LOUDLY (§4) — never dropped or coerced, since a
dropped/changed filter runs a DIFFERENT query than asked (a correctness AND
over-disclosure risk).

The charts engine and its Flask blueprint form an import cycle
(``query_builder`` ⇄ ``blueprints.__init__`` ⇄ ``routes``); to keep this module
safe to import anywhere (it is pulled in by ``chat/tool_bindings.py``), every
charts-engine import is LAZY (inside the execution functions). The module-level
imports are all pure leaves.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from src.api.blueprints.chat.artifacts import (
    _jsonify,
    _num,
    build_drilldown,
    drilldown_for_aggregate_cell,
)
# FieldResolutionError is defined in _query.py and re-exported by assets.py; the
# ToolRegistry boundary turns it (a ValueError subclass) into a clean LLM string.
from src.mcp.tools.assets import FieldResolutionError
from src.mcp.tools._common import parse_filters_arg
from src.mcp.tools._query import (
    _UNKNOWN_FIELD_HINT,
    _normalize_field_name,
    _normalize_filters,
)
from src.modules.fields.encoding import coerce_field_id, decode, encode
from src.modules.fields.operators import resolve as _resolve_operator

logger = logging.getLogger(__name__)

ToolReturn = Tuple[str, Optional[Dict[str, Any]]]


# ---------------------------------------------------------------------------
# §3.4 — standard grammar → charts FilterOperator wire enum
# ---------------------------------------------------------------------------
# Canonical OPERATOR_REGISTRY code → charts ``FilterOperator`` wire value
# (src/api/blueprints/charts/models.py::FilterOperator). The model speaks the
# standard grammar; this is the ONLY place the two enums meet. Operators with no
# charts equivalent (all / within_last / is_true / is_false) are intentionally
# ABSENT so they raise a loud error rather than silently mapping to the wrong op.
_CODE_TO_CHARTS_OP: Dict[str, str] = {
    "eq": "equals",
    "neq": "not_equals",
    "contains": "contains",
    "ncontains": "not_contains",
    "starts": "starts_with",
    "ends": "ends_with",
    "gt": "gt",
    "gte": "gte",
    "lt": "lt",
    "lte": "lte",
    "between": "between",
    "any": "in",        # any-of  → IN (= ANY(...))
    "none": "not_in",   # none-of → NOT IN (= != ALL(...))
    "empty": "is empty",
    "notempty": "is not empty",
}

# The charts FilterOperator wire values, hardcoded so this module never imports
# the charts blueprint at module load (avoids the import cycle). A drift guard in
# the tests asserts this equals ``{op.value for op in FilterOperator}``.
_CHARTS_OPERATOR_VALUES = frozenset({
    "equals", "not_equals", "contains", "not_contains", "starts_with",
    "ends_with", "gt", "gte", "lt", "lte", "in", "not_in",
    "is empty", "is not empty", "between",
})


def _to_charts_operator(spelling: str) -> str:
    """Translate any operator spelling to the charts ``FilterOperator`` wire form.

    Two callers, two input shapes:
    - ``run_chart`` (model grammar): a canonical code / alias (``eq``, ``any``,
      ``any of``) → resolved via ``OPERATOR_REGISTRY`` → mapped.
    - ``run_report`` (stored config): an already-charts-format value
      (``not_in``, ``is not empty``) → passed through. ``not_in`` in particular
      is NOT an ``OPERATOR_REGISTRY`` spelling, so the pass-through is required.

    Raises ``ValueError`` for an operator the charts engine can't express
    (``all``/``within_last``/``is_true``/``is_false``) and
    ``OperatorResolutionError`` for an entirely unknown spelling — both LOUD so
    the model can correct or fall back to ``query_assets`` (§4)."""
    key = str(spelling).strip()
    if key in _CHARTS_OPERATOR_VALUES:
        return key
    code = _resolve_operator(spelling).code  # raises OperatorResolutionError
    charts = _CODE_TO_CHARTS_OP.get(code)
    if charts is None:
        raise ValueError(
            f"Operator '{spelling}' (canonical '{code}') is not supported by the "
            "chart engine. Charts filters support: eq, neq, contains, ncontains, "
            "starts, ends, gt, gte, lt, lte, between, any (→in), none (→not_in), "
            f"empty, notempty. For '{code}', use query_assets instead."
        )
    return charts


def _translate_filters(
    filters: Any, resolver,
) -> List[Dict[str, Any]]:
    """Translate standard §3.1 ``{field, op, value}`` filters into charts
    ``FilterCondition`` dicts ``{field_id, operator, value}``.

    Delegates field + operator RESOLUTION to ``_normalize_filters`` (the same
    loud-error path ``query_assets`` uses — unknown field/op raises), then maps
    the canonical operator to the charts wire enum. Returns ``[]`` for empty
    input. Never silently drops a clause."""
    normalized = _normalize_filters(filters, resolver)  # loud; None when empty
    out: List[Dict[str, Any]] = []
    for f in normalized or []:
        out.append({
            "field_id": f["field_id"],
            "operator": _to_charts_operator(f["op"]),
            "value": f.get("value"),
        })
    return out


def _resolve_select_field(raw: Any, resolver) -> Optional[Dict[str, Any]]:
    """Resolve a SELECT-slot field (dimension/segment/time/source/target/metric)
    from a bare name, an encoded id, or a ``{field_id}`` dict into the field_id
    dict the charts ``SelectClause`` expects. ``None`` passes through.

    A bare name that resolves to no live catalog field raises
    ``FieldResolutionError`` (LOUD, §4) — never silently omitted, which would run
    a chart over a different/wider grouping than asked. Encoded ids and dicts are
    decoded/coerced here; their catalog validity is enforced downstream by the
    QueryBuilder's resolver (surfaced as a loud error by the caller)."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return coerce_field_id(raw).model_dump()
    if isinstance(raw, str) and ":" not in raw:
        enc = _normalize_field_name(raw, resolver)
        if enc is None:
            raise FieldResolutionError(
                _UNKNOWN_FIELD_HINT.format(param="chart field", name=raw)
            )
        return decode(enc).model_dump()
    # Already an encoded FieldId (e.g. "ext:Beszel:cpu").
    try:
        return decode(raw).model_dump()
    except Exception as exc:
        raise FieldResolutionError(
            _UNKNOWN_FIELD_HINT.format(param="chart field", name=raw)
        ) from exc


# ---------------------------------------------------------------------------
# request building (model-friendly inputs → ChartQueryRequest body)
# ---------------------------------------------------------------------------

_VALID_QUERY_TYPES = frozenset({
    "distribution", "comparison", "correlation", "metric", "trend",
    "trend_percentage",
})
_VALID_AGGS = frozenset({"COUNT", "COUNT_DISTINCT", "SUM", "AVG", "MIN", "MAX"})

# Labels the charts engine emits for rolled-up / empty buckets. They are NOT real
# field values, so they must never receive a drilldown (drilling "Other" would
# re-run query_assets with a filter that matches nothing).
_SYNTHETIC_LABELS = frozenset({"other", "Other", "(Empty)", "(empty)", ""})

# A donut is a top-N viz; cap distinct slices so a high-cardinality dimension
# doesn't render 100 illegible wedges (the engine rolls the rest into "Other").
_DEFAULT_DONUT_SLICES = 12
# Trends can span many buckets (e.g. a year of days); match the UI's 1000 so a
# full series isn't truncated at the engine's 100 default.
_DEFAULT_TREND_LIMIT = 1000
# Text-preview row cap (full data rides the artifact); table-artifact row cap.
_TEXT_ROW_CAP = 20
_TABLE_ROW_CAP = 200


def _metric_spec(aggregate, metric_field, resolver) -> Dict[str, Any]:
    """Build the charts ``MetricSpec`` dict. COUNT needs no field; every other
    aggregate requires a resolvable ``metric_field`` (loud error otherwise)."""
    agg = (aggregate or "COUNT").upper()
    if agg not in _VALID_AGGS:
        raise ValueError(
            f"Unknown aggregate '{aggregate}'. One of: {sorted(_VALID_AGGS)}."
        )
    if agg == "COUNT":
        return {"agg": "COUNT"}
    if not metric_field:
        raise ValueError(
            f"Aggregate {agg} requires `metric_field` (the numeric field to "
            f"{agg.lower()})."
        )
    return {"agg": agg, "field_id": _resolve_select_field(metric_field, resolver)}


def _build_chart_request(
    *, query_type, resolver, aggregate, metric_field, dimension, segment,
    time_field, split_field, interval, last, start, end, dimension_limit,
    segment_limit, row_limit, col_limit, limit, percentage, filters,
    comparison_filters,
) -> Dict[str, Any]:
    """Assemble a ``ChartQueryRequest`` body from the model's friendly inputs.

    Each query_type places fields in the slots the engine reads (§2.B), the
    standard-grammar ``filters`` are translated at the boundary (§3.4), and the
    knobs map to ``QueryOptions``. Pure — no DB, no engine. Raises a loud
    ValueError for a missing required field per query_type (§4)."""
    qtype = "trend_percentage" if (percentage and query_type == "trend") else query_type

    dim = _resolve_select_field(dimension, resolver)
    seg = _resolve_select_field(segment, resolver)
    tf = _resolve_select_field(time_field, resolver)
    split = _resolve_select_field(split_field, resolver)

    select: Dict[str, Any] = {}
    options: Dict[str, Any] = {"sortOrder": "desc"}

    if qtype == "distribution":
        if dim is None:
            raise ValueError(
                "distribution charts require `dimension` (the field to break "
                "down by)."
            )
        select["dimension"] = dim
        # dimension_limit caps the donut's top-N slices (engine rolls the rest
        # into 'Other'); default keeps the wedge count legible.
        options["limit"] = dimension_limit or limit or _DEFAULT_DONUT_SLICES
    elif qtype == "comparison":
        if dim is None or seg is None:
            raise ValueError(
                "comparison charts require `dimension` (x-axis) and `segment` "
                "(the series to stack/group)."
            )
        select["xAxis"] = dim
        select["yAxis"] = seg
        if limit is not None:
            options["limit"] = limit
        if segment_limit is not None:
            options["segmentLimit"] = segment_limit
    elif qtype == "correlation":
        if dim is None or seg is None:
            raise ValueError(
                "correlation charts require `dimension` (source) and `segment` "
                "(target)."
            )
        select["sourceField"] = dim
        select["targetField"] = seg
        if row_limit is not None:
            options["rowLimit"] = row_limit
        if col_limit is not None:
            options["columnLimit"] = col_limit
    elif qtype in ("trend", "trend_percentage"):
        if tf is None:
            raise ValueError(
                "trend charts require `time_field` (the timestamp/date field to "
                "bucket by, e.g. First Seen / Last Seen)."
            )
        if not interval:
            raise ValueError(
                "trend charts require `interval` (hour|day|week|month|quarter|"
                "year)."
            )
        select["timeField"] = tf
        options["interval"] = interval
        options["limit"] = limit or _DEFAULT_TREND_LIMIT
        # The series split (multi-line trend) is the engine's `dimension`; accept
        # either `split_field` (explicit) or `dimension`.
        series_field = split if split is not None else dim
        if series_field is not None:
            select["dimension"] = series_field
            if dimension_limit is not None:
                options["dimensionLimit"] = dimension_limit
    elif qtype == "metric":
        select["metric"] = _metric_spec(aggregate, metric_field, resolver)
        if percentage:
            # percentage-of-base: comparison_filters are the denominator set.
            options["metricType"] = "percentage"
    else:
        raise ValueError(
            f"Unknown query_type '{query_type}'. One of: "
            f"{sorted(_VALID_QUERY_TYPES)}."
        )

    # Custom time window. The engine exposes only the relative `last` option, so
    # a custom start/end rides the filter path as a between/gte/lte on the time
    # field (these are already charts-format dicts — no translation needed).
    extra: List[Dict[str, Any]] = []
    if start or end:
        if tf is None:
            raise ValueError("`start`/`end` require `time_field`.")
        if start and end:
            extra.append({"field_id": tf, "operator": "between",
                          "value": [start, end]})
        elif start:
            extra.append({"field_id": tf, "operator": "gte", "value": start})
        else:
            extra.append({"field_id": tf, "operator": "lte", "value": end})
    elif last:
        options["last"] = last

    request: Dict[str, Any] = {
        "queryType": qtype,
        "from": {"table": "assets", "joins": []},
        "select": select,
        "filters": _translate_filters(filters, resolver) + extra,
        "options": options,
    }
    comp = _translate_filters(comparison_filters, resolver)
    if comp:
        request["comparisonFilters"] = comp
    return request


# ---------------------------------------------------------------------------
# execution (real engine, lazily imported to dodge the charts import cycle)
# ---------------------------------------------------------------------------

def _summarize_validation_error(exc) -> str:
    """Render a Pydantic ValidationError as a short, SAFE message — only the
    error ``msg`` text (which names the missing/invalid slot), never the raw
    input value, so nothing sensitive leaks into the transcript (§4)."""
    try:
        errs = exc.errors()
        if errs:
            return "Invalid chart request: " + "; ".join(
                str(e.get("msg")) for e in errs[:3]) + "."
    except Exception:
        pass
    return "Invalid chart request parameters."


def _execute_chart_request(db, resolver, settings_manager, request_dict):
    """Validate, build, execute, and transform a chart request through the REAL
    charts engine — the SAME ``ChartQueryRequest`` → ``QueryBuilder`` →
    ``ChartRepository`` → ``transform_results`` path the HTTP route uses. NOT a
    reimplementation. Returns ``(transformed_data, query_type)``.

    Imports are lazy because ``query_builder`` ⇄ the charts blueprint form an
    import cycle; deferring to call time keeps this module import-safe. Raises a
    ValueError-family error on bad input; the caller converts to a loud string."""
    from pydantic import ValidationError
    from src.api.blueprints.charts.models import ChartQueryRequest
    from src.api.modules.settings.settings_schema import get_confidence_thresholds
    from src.modules.charts.repositories.chart_repository import ChartRepository
    from src.modules.charts.services.query_builder import QueryBuilder
    from src.modules.fields import InvalidFieldError

    try:
        req = ChartQueryRequest(**request_dict)
    except ValidationError as exc:
        raise ValueError(_summarize_validation_error(exc)) from exc

    builder = QueryBuilder(
        confidence_thresholds=get_confidence_thresholds(settings_manager),
        resolver=resolver,
    )
    try:
        sql, params = builder.build_query(req)
    except InvalidFieldError as exc:
        # Unknown FieldId in a SELECT slot — surface it loudly, same channel as
        # an unknown filter field (§4), rather than dropping to a wider chart.
        raise FieldResolutionError(str(exc)) from exc

    repo = ChartRepository(db)
    results, _ms = repo.execute_query(sql, params)
    return (repo.transform_results(results, req.queryType.value),
            req.queryType.value)


# ---------------------------------------------------------------------------
# rendering (charts data → text preview + validated donut/stacked_bar/table)
# ---------------------------------------------------------------------------
# NOTE: the chat artifact envelope (artifacts.validate_envelope) currently
# validates ONLY donut / stacked_bar / table. The plan lists line/punchcard/
# sankey for trend/correlation; until the envelope grows those types (a Phase-0
# `artifacts.py` change, out of this slice's file scope) trend renders as a time
# table and correlation as a flow table — the DATA is exact; only the glyph
# differs. Flagged in the PR.

def _field_label(fid: Any) -> str:
    """Human label for a field_id dict (title / 'source - attribute' / category),
    without a resolver round-trip."""
    if not isinstance(fid, dict):
        return str(fid)
    name = fid.get("name")
    if name:
        return name
    src = fid.get("source") or fid.get("group")
    attr = fid.get("attribute")
    if src and attr:
        return f"{src} - {attr}"
    return fid.get("category") or "field"


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        if not math.isfinite(v):
            # int(nan)/int(inf) raises; a NUMERIC 'NaN'/'Infinity' aggregate can
            # reach here as a Python nan/inf. Render the token, don't crash.
            return str(v)
        return f"{v:,.2f}" if v != int(v) else f"{int(v):,}"
    return str(v)


def _render_chart(query_type, data, request, base_filters) -> ToolReturn:
    if query_type == "distribution":
        return _render_distribution(data, request, base_filters)
    if query_type == "comparison":
        return _render_comparison(data, request, base_filters)
    if query_type == "correlation":
        return _render_correlation(data, request, base_filters)
    if query_type in ("trend", "trend_percentage"):
        return _render_trend(data, request, base_filters)
    if query_type == "metric":
        return _render_metric(data, request, base_filters)
    return f"Chart computed (query_type '{query_type}').", None


def _render_distribution(data, request, base_filters) -> ToolReturn:
    dim = request["select"]["dimension"]
    dim_ref = encode(dim)
    rows = [r for r in (data or []) if r.get("label") is not None]
    if not rows:
        return f"No data for distribution by {_field_label(dim)}.", None
    total = sum(_num(r.get("value")) for r in rows)
    lines = [f"Distribution by {_field_label(dim)} "
             f"({len(rows)} groups, total {_fmt(total)}):"]
    for r in rows[:_TEXT_ROW_CAP]:
        pct = r.get("percentage")
        pct_s = f", {pct}%" if pct is not None else ""
        lines.append(f"  {r.get('label')}: {_fmt(_num(r.get('value')))}{pct_s}")
    if len(rows) > _TEXT_ROW_CAP:
        lines.append(f"  [{len(rows) - _TEXT_ROW_CAP} more groups in the chart.]")
    series: List[Dict[str, Any]] = []
    for r in rows:
        label = r.get("label")
        item: Dict[str, Any] = {"label": label, "value": _jsonify(r.get("value"))}
        if label not in _SYNTHETIC_LABELS:
            item["drilldown"] = drilldown_for_aggregate_cell(
                dim_ref, label, base_filters=base_filters)
        series.append(item)
    artifact = {
        "type": "donut",
        "title": f"Assets by {_field_label(dim)}",
        "series": series,
        "meta": {"source_tool": "run_chart", "query_type": "distribution",
                 "total": total, "row_field": dim_ref},
    }
    return "\n".join(lines), artifact


def _render_comparison(data, request, base_filters) -> ToolReturn:
    x_fid = request["select"]["xAxis"]
    y_fid = request["select"]["yAxis"]
    x_ref, y_ref = encode(x_fid), encode(y_fid)
    grid: Dict[Any, Dict[Any, Any]] = {}
    order: List[Any] = []
    for r in (data or []):
        x = r.get("x_axis")
        if x is None:
            continue
        if x not in grid:
            grid[x] = {}
            order.append(x)
        grid[x][r.get("y_axis")] = _num(r.get("value"))
    if not order:
        return f"No data for {_field_label(x_fid)} × {_field_label(y_fid)}.", None
    lines = [f"{_field_label(x_fid)} × {_field_label(y_fid)} (counts):"]
    for x in order[:_TEXT_ROW_CAP]:
        cells = ", ".join(f"{y}={_fmt(v)}" for y, v in grid[x].items())
        lines.append(f"  {x}: {cells}")
    if len(order) > _TEXT_ROW_CAP:
        lines.append(f"  [{len(order) - _TEXT_ROW_CAP} more rows in the chart.]")
    series: List[Dict[str, Any]] = []
    for x in order:
        stacks: List[Dict[str, Any]] = []
        for y, v in grid[x].items():
            stack: Dict[str, Any] = {"label": y, "value": _jsonify(v)}
            if x not in _SYNTHETIC_LABELS and y not in _SYNTHETIC_LABELS:
                stack["drilldown"] = drilldown_for_aggregate_cell(
                    x_ref, x, y_ref, y, base_filters=base_filters)
            stacks.append(stack)
        series.append({"x_label": x, "stacks": stacks})
    artifact = {
        "type": "stacked_bar",
        "title": f"Assets by {_field_label(x_fid)} × {_field_label(y_fid)}",
        "series": series,
        "meta": {"source_tool": "run_chart", "query_type": "comparison",
                 "row_field": x_ref, "col_field": y_ref},
    }
    return "\n".join(lines), artifact


def _render_correlation(data, request, base_filters) -> ToolReturn:
    s_fid = request["select"]["sourceField"]
    t_fid = request["select"]["targetField"]
    s_ref, t_ref = encode(s_fid), encode(t_fid)
    rows = [r for r in (data or []) if r.get("source") is not None]
    if not rows:
        return (f"No correlation data for {_field_label(s_fid)} → "
                f"{_field_label(t_fid)}.", None)
    capped = rows[:_TABLE_ROW_CAP]
    lines = [f"{_field_label(s_fid)} → {_field_label(t_fid)} ({len(rows)} pairs):"]
    for r in capped[:_TEXT_ROW_CAP]:
        lines.append(f"  {r.get('source')} → {r.get('target')}: "
                     f"{_fmt(_num(r.get('value')))}")
    if len(rows) > _TEXT_ROW_CAP:
        lines.append(f"  [{len(rows) - _TEXT_ROW_CAP} more pairs in the table.]")
    series: List[Dict[str, Any]] = []
    for r in capped:
        src, tgt = r.get("source"), r.get("target")
        row: Dict[str, Any] = {"source": _jsonify(src), "target": _jsonify(tgt),
                               "value": _jsonify(r.get("value"))}
        # A scalar source→target pair drills to its asset set; a list target
        # (the engine can emit arrays) has no single eq value, so no drilldown.
        # isinstance check FIRST: a list in a frozenset membership test raises
        # TypeError (unhashable), so it must short-circuit before `tgt not in`.
        if (not isinstance(tgt, list) and not isinstance(src, list)
                and src not in _SYNTHETIC_LABELS
                and tgt not in _SYNTHETIC_LABELS):
            row["drilldown"] = drilldown_for_aggregate_cell(
                s_ref, src, t_ref, tgt, base_filters=base_filters)
        series.append(row)
    artifact = {
        "type": "table",
        "title": f"{_field_label(s_fid)} → {_field_label(t_fid)}",
        "columns": [{"key": "source", "label": _field_label(s_fid)},
                    {"key": "target", "label": _field_label(t_fid)},
                    {"key": "value", "label": "Count"}],
        "series": series,
        "meta": {"source_tool": "run_chart", "query_type": "correlation",
                 "truncated": len(rows) > _TABLE_ROW_CAP},
    }
    return "\n".join(lines), artifact


def _render_trend(data, request, base_filters) -> ToolReturn:
    tf = request["select"]["timeField"]
    rows = data or []
    if not rows:
        return f"No trend data over {_field_label(tf)}.", None
    multi = any("series" in r for r in rows)
    capped = rows[:_TABLE_ROW_CAP]
    lines = [f"Trend of asset count over {_field_label(tf)} ({len(rows)} points):"]
    for r in capped[:_TEXT_ROW_CAP]:
        ser = f" [{r.get('series')}]" if multi and r.get("series") else ""
        lines.append(f"  {r.get('timestamp')}{ser}: {_fmt(_num(r.get('value')))}")
    if len(rows) > _TEXT_ROW_CAP:
        lines.append(f"  [{len(rows) - _TEXT_ROW_CAP} more points in the table.]")
    if multi:
        columns = [{"key": "timestamp", "label": "Time"},
                   {"key": "series", "label": "Series"},
                   {"key": "value", "label": "Count"}]
        series = [{"timestamp": _jsonify(r.get("timestamp")),
                   "series": _jsonify(r.get("series")),
                   "value": _jsonify(r.get("value"))} for r in capped]
    else:
        columns = [{"key": "timestamp", "label": "Time"},
                   {"key": "value", "label": "Count"}]
        series = [{"timestamp": _jsonify(r.get("timestamp")),
                   "value": _jsonify(r.get("value"))} for r in capped]
    artifact = {
        "type": "table",
        "title": f"Asset count over {_field_label(tf)}",
        "columns": columns,
        "series": series,
        "meta": {"source_tool": "run_chart", "query_type": request["queryType"],
                 "truncated": len(rows) > _TABLE_ROW_CAP},
    }
    return "\n".join(lines), artifact


def _render_metric(data, request, base_filters) -> ToolReturn:
    spec = request["select"].get("metric") or {}
    agg = spec.get("agg", "COUNT")
    fid = spec.get("field_id")
    is_pct = request["options"].get("metricType") == "percentage"
    # percentage mode computes a COUNT ratio — the engine IGNORES select.metric
    # (the agg/field), so label it as a percentage rather than mislabel a ratio
    # as "AVG of X". Check is_pct FIRST, before the agg/field label.
    metric_label = ("Percentage of base" if is_pct
                    else f"{agg} of {_field_label(fid)}" if fid else agg)
    row0 = data[0] if data else {}
    raw = row0.get("value")
    # _fmt(raw) (not _num) so a NULL aggregate (AVG/MIN/MAX/SUM over zero rows
    # returns NULL) renders "—", not a fabricated "0" the model reports as real.
    display = row0.get("formattedValue") or _fmt(raw)
    suffix = "%" if is_pct else ""
    text = f"{metric_label}: {display}{suffix}"
    row: Dict[str, Any] = {"metric": metric_label, "value": _jsonify(raw)}
    # The single number is substantiated by the same filters that produced it.
    if base_filters:
        row["drilldown"] = build_drilldown(base_filters)
    artifact = {
        "type": "table",
        "title": metric_label,
        "columns": [{"key": "metric", "label": "Metric"},
                    {"key": "value", "label": "Value"}],
        "series": [row],
        "meta": {"source_tool": "run_chart", "query_type": "metric",
                 "metric": metric_label, "percentage": is_pct},
    }
    return text, artifact


# ---------------------------------------------------------------------------
# public tool
# ---------------------------------------------------------------------------

def run_chart(
    db, resolver, settings_manager=None, *, query_type,
    aggregate: str = "COUNT", metric_field: Optional[str] = None,
    dimension: Optional[str] = None, segment: Optional[str] = None,
    time_field: Optional[str] = None, split_field: Optional[str] = None,
    interval: Optional[str] = None, last: Optional[str] = None,
    start: Optional[str] = None, end: Optional[str] = None,
    dimension_limit: Optional[int] = None, segment_limit: Optional[int] = None,
    row_limit: Optional[int] = None, col_limit: Optional[int] = None,
    limit: Optional[int] = None, percentage: bool = False,
    filters: Any = None, comparison_filters: Any = None,
) -> ToolReturn:
    """Run a chart query through the charts engine, returning ``(text, artifact)``.

    A PASSTHROUGH to ``POST /api/charts/query`` (§2.B): build a request, run it
    through the real ``QueryBuilder`` + ``ChartRepository``, render a compact
    text preview + a validated donut/stacked_bar/table artifact with per-bucket
    drilldowns. All errors are loud LLM-facing strings, never silent (§4)."""
    if query_type not in _VALID_QUERY_TYPES:
        return (f"Unknown query_type '{query_type}'. One of: "
                f"{sorted(_VALID_QUERY_TYPES)}.", None)
    # The chat often serialises filters as a JSON string; parse once here (loud
    # on bad JSON, §4) and reuse the SAME parsed list for the chart AND the
    # drilldown base, so they can never disagree.
    filters, ferr = parse_filters_arg(filters)
    if ferr:
        return ferr, None
    comparison_filters, cerr = parse_filters_arg(comparison_filters)
    if cerr:
        return cerr, None
    try:
        request = _build_chart_request(
            query_type=query_type, resolver=resolver, aggregate=aggregate,
            metric_field=metric_field, dimension=dimension, segment=segment,
            time_field=time_field, split_field=split_field, interval=interval,
            last=last, start=start, end=end, dimension_limit=dimension_limit,
            segment_limit=segment_limit, row_limit=row_limit, col_limit=col_limit,
            limit=limit, percentage=percentage,
            filters=filters, comparison_filters=comparison_filters,
        )
    except (ValueError, KeyError, LookupError) as exc:
        return str(exc), None

    try:
        data, qtype = _execute_chart_request(db, resolver, settings_manager,
                                             request)
    except (ValueError, KeyError, LookupError) as exc:
        return str(exc), None
    except TimeoutError:
        return ("The chart query timed out — narrow the filters or time window "
                "and retry.", None)
    except Exception:
        logger.exception("run_chart execution failed (query_type=%s)", query_type)
        return ("The chart query failed to execute. Re-check the field names "
                "(call describe_catalog) and try a simpler query.", None)

    return _render_chart(qtype, data, request, filters or [])


# ---------------------------------------------------------------------------
# run_report — replay a saved report's persisted chart queryConfigs
# ---------------------------------------------------------------------------
# Reverse of _CODE_TO_CHARTS_OP — charts wire op → canonical registry code — so a
# replayed report's stored filters can be re-expressed in the standard grammar
# for the drilldown base (so drilling a slice keeps the report's filter scope).
_CHARTS_OP_TO_CODE: Dict[str, str] = {v: k for k, v in _CODE_TO_CHARTS_OP.items()}


def _coerce_stored_field_id(v: Any) -> Any:
    """A persisted select/filter field id is an encoded STRING (editor form) or a
    dict; return the field_id dict the charts models expect."""
    if isinstance(v, dict):
        return coerce_field_id(v).model_dump()
    if isinstance(v, str):
        return decode(v).model_dump()
    return v


def _coerce_stored_filter(f: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce one persisted filter into a charts ``FilterCondition`` dict.

    Keeps ONLY {field_id, operator, value} (FilterCondition forbids extras like a
    UI ``id``/``label``), decodes the field id, and normalizes the operator —
    which may be a canonical code, an alias, or an already-charts-wire value
    (incl. ``not_in``) — to the charts enum."""
    return {
        "field_id": _coerce_stored_field_id(f.get("field_id")),
        "operator": _to_charts_operator(f.get("operator")),
        "value": f.get("value"),
    }


def _coerce_stored_query_config(qc: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce a persisted chart ``queryConfig`` into a ``ChartQueryRequest`` body.

    Mirrors the UI's ``transformToAPIRequest`` so a saved chart replays
    identically: decode encoded-string select ids, map punchcard
    rowField/columnField → source/target, normalize stored filter operators, and
    fold the trend template's ``timeRange`` into ``last``."""
    select_in = qc.get("select") or {}
    options = dict(qc.get("options") or {})
    select: Dict[str, Any] = {}
    for slot in ("dimension", "segment", "timeField", "xAxis", "yAxis",
                 "sourceField", "targetField"):
        if select_in.get(slot) is not None:
            select[slot] = _coerce_stored_field_id(select_in[slot])
    if select_in.get("metric"):
        metric = dict(select_in["metric"])
        if metric.get("field_id") is not None:
            metric["field_id"] = _coerce_stored_field_id(metric["field_id"])
        select["metric"] = metric
    # Punchcard stores rowField/columnField; the engine reads source/target.
    if "sourceField" not in select and select_in.get("rowField") is not None:
        select["sourceField"] = _coerce_stored_field_id(select_in["rowField"])
    if "targetField" not in select and select_in.get("columnField") is not None:
        select["targetField"] = _coerce_stored_field_id(select_in["columnField"])
    # Comparison is often saved as dimension/segment (the engine's model_validator
    # aliases them ON THE MODEL, but the renderer reads the request dict's
    # xAxis/yAxis). Alias them here too so the replay renders, not KeyErrors.
    if qc.get("queryType") == "comparison":
        if "xAxis" not in select and "dimension" in select:
            select["xAxis"] = select["dimension"]
        if "yAxis" not in select and "segment" in select:
            select["yAxis"] = select["segment"]
    # The trend template stores the window as ``timeRange``; the engine reads
    # ``last``. Without this the custom window is silently dropped.
    if options.get("timeRange") is not None:
        options["last"] = options.pop("timeRange")

    request: Dict[str, Any] = {
        "queryType": qc.get("queryType"),
        "from": qc.get("from") or {"table": "assets", "joins": []},
        "select": select,
        "filters": [_coerce_stored_filter(f) for f in (qc.get("filters") or [])],
        "options": options,
    }
    comp = [_coerce_stored_filter(f) for f in (qc.get("comparisonFilters") or [])]
    if comp:
        request["comparisonFilters"] = comp
    return request


def _charts_filters_to_standard(
    charts_filters: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Re-express charts ``FilterCondition`` dicts as standard §3.1
    ``{field, op, value}`` filters (encoded field + canonical op) — the drilldown
    base, so a replayed chart's drill-down keeps the report's filter scope."""
    out: List[Dict[str, Any]] = []
    for f in charts_filters:
        code = _CHARTS_OP_TO_CODE.get(f.get("operator"))
        if code is None:
            continue
        out.append({"field": encode(f["field_id"]), "op": code,
                    "value": f.get("value")})
    return out


def run_report(
    db, resolver, settings_manager=None, *, report_id,
    chart_index: Optional[int] = None,
) -> ToolReturn:
    """Replay a saved report's charts: load the report, re-run each persisted
    ``queryConfig`` through the SAME engine as ``run_chart``, and render each
    chart's data. ``chart_index`` replays a single chart; otherwise all charts'
    data is summarised and the first chartable one becomes the artifact (one
    tool call carries one artifact)."""
    from src.mcp.tools._common import validate_uuid

    err = validate_uuid(report_id, "report")
    if err:
        return err, None

    rows = db.execute_query(
        "SELECT id, title, charts FROM reports WHERE id = %(id)s::uuid",
        {"id": report_id},
    ) or []
    if not rows:
        return f"Report {report_id} not found.", None
    report = rows[0]
    title = report.get("title") or report_id
    charts: List[Dict[str, Any]] = report.get("charts") or []
    if not charts:
        return f"Report '{title}' has no charts defined.", None

    if chart_index is not None:
        if chart_index < 0 or chart_index >= len(charts):
            return (f"Report '{title}' has {len(charts)} chart(s) "
                    f"(0..{len(charts) - 1}); chart_index {chart_index} is out "
                    "of range.", None)
        targets = [(chart_index, charts[chart_index])]
    else:
        targets = list(enumerate(charts))

    lines = [f"Report '{title}' — {len(charts)} chart(s):"]
    artifact: Optional[Dict[str, Any]] = None
    for idx, chart in targets:
        ctitle = chart.get("title") or chart.get("name") or f"chart {idx}"
        qc = chart.get("queryConfig")
        if not qc:
            lines.append(f"\n[{idx}] {ctitle}: definition only (no queryConfig).")
            continue
        try:
            request = _coerce_stored_query_config(qc)
            data, qtype = _execute_chart_request(db, resolver, settings_manager,
                                                 request)
            base = _charts_filters_to_standard(request.get("filters") or [])
            ctext, cart = _render_chart(qtype, data, request, base)
        except (ValueError, KeyError, LookupError) as exc:
            lines.append(f"\n[{idx}] {ctitle}: could not replay — {exc}")
            continue
        except Exception:
            # One bad chart must not sink the whole report — degrade per-chart.
            logger.exception("run_report: chart %s replay failed", idx)
            lines.append(f"\n[{idx}] {ctitle}: failed to replay.")
            continue
        lines.append(f"\n[{idx}] {ctitle} ({qtype}):\n{ctext}")
        if artifact is None and cart is not None:
            artifact = cart
    return "\n".join(lines), artifact
