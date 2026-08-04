# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ``query_assets`` keystone primitive (plan §2.A).

One tool, three modes (``list`` | ``aggregate`` | ``field_values``) gated by a
REQUIRED ``mode`` enum. It is the single highest-frequency asset tool and the
primary interface for "find / count / breakdown / distinct-values" questions.

It is NOT a new query engine. Every mode COMPOSES the existing thin wrappers in
``src/mcp/tools/assets.py`` (``list_assets_query`` / ``aggregate_assets_query`` /
``get_field_values_query``), which delegate field resolution to ``_query.py`` /
``FieldCatalog`` and operator resolution to ``OPERATOR_REGISTRY``. The only new
SQL in this whole feature is the aggregate-function threading already added to
``repo.aggregate_assets`` (§3.3); the top-N / sort / include_null / totals /
percent shaping below is pure Python post-processing over that bounded result.

Loud-error-over-silent-drop is mandatory (§4): an unknown field/op/sort or a bad
aggregate combination is surfaced as an LLM-facing string, never dropped (a
dropped filter silently widens the result — a correctness AND over-disclosure
risk). Each aggregate row/cell carries a ``drilldown`` (§3.6) that ``mode=list``
re-runs verbatim, so no count is emitted that can't be substantiated.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from src.mcp.tools._common import parse_filters_arg
from src.mcp.tools._query import (
    CHAT_LIST_FIELDS_ENCODED,
    _normalize_field_list,
    resolve_list_columns,
)
from src.mcp.tools.assets import (
    FieldResolutionError,
    aggregate_assets_query,
    format_list_assets,
    get_field_values_query,
    list_assets_query,
)
from src.modules.fields.operators import OperatorResolutionError

from src.api.blueprints.chat.artifacts import (
    _jsonify,
    _num,
    build_donut_from_aggregate,
    build_stacked_bar_from_aggregate,
    build_table_from_aggregate,
    build_table_from_assets,
)

# Match the existing chat list binding: fetch enough to render a full small/
# medium "list all" in the table, but keep the LLM-facing text lean.
_CHAT_LIST_DEFAULT_LIMIT = 100
_CHAT_LIST_TEXT_ROWS = 25
_LIST_MAX_LIMIT = 200

# field_values matches the UI (≤500, up from the old 200 chat cap).
_FIELD_VALUES_DEFAULT_LIMIT = 100
_FIELD_VALUES_MAX_LIMIT = 500

# Aggregate top-N defaults (hard caps so a high-cardinality pivot can't blow the
# context); ``sort`` orders within them. Mirrors the plan's 20-row / 30-col caps.
_DEFAULT_ROW_LIMIT = 20
_DEFAULT_COL_LIMIT = 30
_HARD_AXIS_CAP = 30

# Aggregates whose cell values SUM to a meaningful total. AVG/MIN/MAX/distinct
# counts do NOT (you can't sum averages), so for those we surface no grand/row/
# col totals rather than a misleading number.
_ADDITIVE_AGGS = {"COUNT", "SUM"}

_ARTIFACT_SOURCE = "query_assets"

ToolReturn = Tuple[str, Optional[Dict[str, Any]]]


def query_assets(
    db,
    resolver,
    *,
    mode: str,
    # common
    filters: Any = None,
    logic: str = "and",
    search: Optional[str] = None,
    # list
    fields: Any = None,
    sort_by: Optional[str] = None,
    sort_dir: str = "asc",
    page: int = 1,
    limit: Optional[int] = None,
    # aggregate
    row_field: Optional[str] = None,
    col_field: Optional[str] = None,
    agg: str = "COUNT",
    metric_field: Optional[str] = None,
    show: str = "count",
    row_limit: int = _DEFAULT_ROW_LIMIT,
    col_limit: int = _DEFAULT_COL_LIMIT,
    sort: str = "count",
    include_null: bool = True,
    # field_values
    field: Optional[str] = None,
) -> ToolReturn:
    """Dispatch to the requested mode, returning ``(text, artifact)``.

    ``mode`` is required and validated by the ToolRegistry schema gate, but we
    re-check here so a direct/programmatic caller gets the same loud error
    instead of a crash."""
    if mode == "list":
        return _query_list(
            db, resolver, filters=filters, logic=logic, search=search,
            fields=fields, sort_by=sort_by, sort_dir=sort_dir, page=page,
            limit=limit,
        )
    if mode == "aggregate":
        return _query_aggregate(
            db, resolver, row_field=row_field, col_field=col_field, agg=agg,
            metric_field=metric_field, filters=filters, logic=logic, show=show,
            row_limit=row_limit, col_limit=col_limit, sort=sort,
            include_null=include_null,
        )
    if mode == "field_values":
        return _query_field_values(
            db, resolver, field=field, search=search, filters=filters,
            limit=limit, logic=logic,
        )
    return (
        f"Unknown mode {mode!r}. query_assets requires mode = "
        "'list' | 'aggregate' | 'field_values'.",
        None,
    )


# ---------------------------------------------------------------------------
# mode=list
# ---------------------------------------------------------------------------

def _coerce_fields(fields: Any) -> Optional[str]:
    """Accept ``fields`` as a comma string OR a list (LLMs send both) and return
    the comma-joined form ``list_assets_query`` expects, or None."""
    if fields is None:
        return None
    if isinstance(fields, str):
        return fields or None
    if isinstance(fields, (list, tuple)):
        return ",".join(str(f) for f in fields if f) or None
    return None


def _query_list(
    db, resolver, *, filters, logic, search, fields, sort_by, sort_dir, page,
    limit,
) -> ToolReturn:
    parsed_filters, err = parse_filters_arg(filters)
    if err:
        return err, None

    limit = _CHAT_LIST_DEFAULT_LIMIT if limit is None else limit
    # Request the fields the table + text actually show; the repo default omits
    # IP/Owner/Data Source/MAC/Serial/Location (#742). A custom set drives both.
    effective_fields = _coerce_fields(fields) or CHAT_LIST_FIELDS_ENCODED
    try:
        results, total = list_assets_query(
            db, resolver, filters=parsed_filters or [], search=search,
            page=page, limit=limit, sort_by=sort_by, sort_dir=sort_dir,
            fields=effective_fields, logic=logic,
        )
    except (OperatorResolutionError, FieldResolutionError) as exc:
        return str(exc), None

    # Drive BOTH the table columns and the text rows off the SAME resolved field
    # set, so any selected column (incl ext:/group:/tag:/posture/virtual) renders
    # with its catalog display label, not an opaque encoded key (§3.2).
    columns = resolve_list_columns(
        _normalize_field_list(effective_fields, resolver), resolver,
    )
    clamped_limit = max(1, min(limit, _LIST_MAX_LIMIT))
    clamped_page = max(1, page)
    text = format_list_assets(
        results, total=total, page=clamped_page, limit=clamped_limit,
        max_rows=_CHAT_LIST_TEXT_ROWS, columns=columns,
    )
    artifact = build_table_from_assets(
        results, total=total, source_tool=_ARTIFACT_SOURCE, columns=columns,
        query={
            "filters": filters or None,
            "search": search or None,
            "sort_by": sort_by or None,
            "sort_dir": sort_dir,
            "fields": effective_fields,
            "logic": logic,
        },
    ) if results else None
    return text, artifact


# ---------------------------------------------------------------------------
# mode=aggregate
# ---------------------------------------------------------------------------

def _fmt(v: Any) -> str:
    """Render an aggregate cell value: ints plain, fractional values to 2 dp."""
    if v is None:
        return "—"
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        return f"{v:,.2f}" if v != int(v) else f"{int(v):,}"
    return str(v)


def _query_aggregate(
    db, resolver, *, row_field, col_field, agg, metric_field, filters, logic,
    show, row_limit, col_limit, sort, include_null,
) -> ToolReturn:
    if not row_field:
        return (
            "aggregate mode requires 'row_field' (the field to group rows by).",
            None,
        )

    result, error_hint = aggregate_assets_query(
        db, resolver, row_field=row_field, col_field=col_field,
        filters=filters or [], agg=agg, metric_field=metric_field, logic=logic,
    )
    if error_hint is not None:
        return error_hint, None
    assert result is not None

    agg_norm = (agg or "COUNT").upper()
    shaped = _shape_aggregate(
        result, agg=agg_norm, row_limit=row_limit, col_limit=col_limit,
        sort=sort, include_null=include_null,
    )
    if not shaped["rows"]:
        return f"No data found for aggregation by '{row_field}'.", None

    text = _format_aggregate_text(
        shaped, row_field=row_field, col_field=col_field, agg=agg_norm,
        metric_field=metric_field, show=show,
    )

    # Build the chart/table from the trimmed (top-N) matrix; the builder adds
    # the per-row/cell drilldown, scoped by the aggregate's own filters so the
    # drill-down reproduces the cell's population (§3.6).
    base_filters, _ = parse_filters_arg(filters)
    trimmed = {
        "rows": shaped["rows"], "columns": shaped["columns"],
        "data": shaped["data"], "grand_total": shaped["raw_grand"],
    }
    artifact: Optional[Dict[str, Any]] = None
    if shaped["additive"] and not shaped["is_2d"]:
        artifact = build_donut_from_aggregate(
            trimmed, row_field, with_drilldown=True, base_filters=base_filters,
        )
    elif shaped["additive"] and shaped["is_2d"]:
        artifact = build_stacked_bar_from_aggregate(
            trimmed, row_field, col_field, with_drilldown=True,
            base_filters=base_filters,
        )
    if artifact is None:
        # Non-additive aggregates (AVG/MIN/MAX/distinct) and any unchartable
        # shape render as a value table — honest, no misleading proportions and
        # (show_total=False) no meaningless sum-of-averages Total column.
        artifact = build_table_from_aggregate(
            trimmed, row_field, col_field, with_drilldown=True,
            base_filters=base_filters, show_total=shaped["additive"],
        )

    if artifact is not None:
        meta = artifact.setdefault("meta", {})
        meta["source_tool"] = _ARTIFACT_SOURCE
        # Label as an aggregate so the table fallback isn't mislabeled 'list'
        # (build_table_from_aggregate hardcodes layout='list').
        meta["layout"] = "aggregate"
        meta["agg"] = agg_norm
        meta["metric_field"] = metric_field
        meta["show"] = show
        meta["total"] = shaped["grand_total"]          # additive or None
        meta["row_totals"] = shaped["row_totals"]
        meta["col_totals"] = shaped["col_totals"]
        meta["omitted_rows"] = shaped["omitted_rows"]
        meta["omitted_cols"] = shaped["omitted_cols"]
        # The full top-N matrix, never lost to the chart's slice/stack caps.
        meta["matrix"] = {
            "rows": shaped["rows"], "columns": shaped["columns"],
            "data": shaped["data"],
        }
    return text, artifact


def _shape_aggregate(
    result: Dict[str, Any], *, agg: str, row_limit: int, col_limit: int,
    sort: str, include_null: bool,
) -> Dict[str, Any]:
    """Apply include_null / sort / top-N / totals to the raw repo matrix.

    Pure post-processing (no SQL). ``additive`` (COUNT/SUM) gates whether
    row/col/grand totals are surfaced — summing averages/min/max/distinct counts
    is meaningless, so for those they are None. Rows/cols are ranked by cell
    magnitude (sum of cells for 2-D) for top-N selection; the omitted tail's
    magnitude is reported (additive) so truncation is never silent (§4)."""
    rows: List[str] = list(result.get("rows") or [])
    columns: List[str] = list(result.get("columns") or [])
    data: Dict[str, Dict[str, Any]] = result.get("data") or {}
    # _num: psycopg2 returns Decimal for SUM/AVG/MIN/MAX over a NUMERIC column;
    # coerce to a JSON-safe number so meta/totals never carry a Decimal (plain
    # json.dumps in the chat repository can't serialize it).
    raw_grand = _num(result.get("grand_total") or 0)
    additive = (agg or "COUNT").upper() in _ADDITIVE_AGGS
    is_2d = bool(columns)
    empty = "(empty)"

    if not include_null:
        rows = [r for r in rows if r != empty]
        columns = [c for c in columns if c != empty]

    def disp_cell(r: str, c: str) -> Any:
        # JSON-safe display value: Decimal→float, MIN/MAX text/dates preserved
        # (NOT coerced to 0), missing→0.
        return _jsonify((data.get(r) or {}).get(c, 0))

    def num_cell(r: str, c: str) -> float:
        # Numeric value for ranking/totals; non-numeric (e.g. MIN over a text
        # column) → 0 so float() never raises on a string cell.
        return _num((data.get(r) or {}).get(c, 0))

    def row_rank(r: str) -> float:
        if is_2d:
            return sum(num_cell(r, c) for c in columns)
        return _num((data.get(r) or {}).get("count", 0))

    def col_rank(c: str) -> float:
        return sum(num_cell(r, c) for r in rows)

    if (sort or "count") == "value":
        rows = sorted(rows)
    else:
        rows = sorted(rows, key=row_rank, reverse=True)
    if is_2d:
        columns = (
            sorted(columns) if (sort or "count") == "value"
            else sorted(columns, key=col_rank, reverse=True)
        )

    row_limit = max(1, min(int(row_limit or _DEFAULT_ROW_LIMIT), _HARD_AXIS_CAP))
    col_limit = max(1, min(int(col_limit or _DEFAULT_COL_LIMIT), _HARD_AXIS_CAP))
    kept_rows, tail_rows = rows[:row_limit], rows[row_limit:]
    omitted_row_sum = (
        sum(row_rank(r) for r in tail_rows) if additive and tail_rows else None
    )
    if is_2d:
        kept_cols, tail_cols = columns[:col_limit], columns[col_limit:]
        omitted_col_sum = (
            sum(col_rank(c) for c in tail_cols)
            if additive and tail_cols else None
        )
    else:
        kept_cols, tail_cols, omitted_col_sum = [], [], None

    trimmed: Dict[str, Dict[str, Any]] = {}
    for r in kept_rows:
        if is_2d:
            trimmed[r] = {c: disp_cell(r, c) for c in kept_cols}
        else:
            trimmed[r] = {"count": _jsonify((data.get(r) or {}).get("count", 0))}

    if additive:
        if is_2d:
            row_totals = {
                r: sum(num_cell(r, c) for c in kept_cols) for r in kept_rows
            }
            col_totals = {
                c: sum(num_cell(r, c) for r in kept_rows) for c in kept_cols
            }
        else:
            row_totals = {
                r: _num((data.get(r) or {}).get("count", 0)) for r in kept_rows
            }
            col_totals = None
        grand_total = raw_grand
    else:
        row_totals = col_totals = grand_total = None

    return {
        "rows": kept_rows, "columns": kept_cols, "data": trimmed,
        "raw_grand": raw_grand, "grand_total": grand_total,
        "row_totals": row_totals, "col_totals": col_totals,
        "omitted_rows": len(tail_rows), "omitted_row_sum": omitted_row_sum,
        "omitted_cols": len(tail_cols), "omitted_col_sum": omitted_col_sum,
        "additive": additive, "is_2d": is_2d, "sort": (sort or "count"),
    }


def _format_aggregate_text(
    shaped: Dict[str, Any], *, row_field, col_field, agg, metric_field, show,
) -> str:
    rows = shaped["rows"]
    data = shaped["data"]
    is_2d = shaped["is_2d"]
    raw_grand = shaped["raw_grand"]

    additive = shaped["additive"]
    lines: List[str] = []
    if agg == "COUNT":
        # Plain int (no thousands separator) — the system prompt + evals tell
        # the model to quote "COUNT: <N>"; a comma would break number extraction
        # and diverge from the historical formatter.
        lines.append(f"COUNT: {int(raw_grand)} assets match the filter.")
        lines.append(f"Breakdown by {row_field}:")
    elif agg == "SUM":
        lines.append(
            f"SUM of {metric_field} by {row_field} — total {_fmt(raw_grand)}."
        )
    else:
        lines.append(
            f"{agg.replace('_', ' ').title()} of {metric_field} by {row_field}:"
        )

    # Percent only makes sense as a share of a meaningful total — i.e. for the
    # additive aggregates (COUNT/SUM). For AVG/MIN/MAX/distinct it's omitted.
    pct = (show or "count") == "percent_total" and additive and bool(raw_grand)

    def _pct(v: Any) -> str:
        return f" ({_num(v) / raw_grand * 100:.1f}%)" if pct else ""

    for r in rows:
        if is_2d:
            row_data = data[r]
            cells = ", ".join(
                f"{c}={_fmt(v)}{_pct(v)}" for c, v in row_data.items()
            )
            # A row total is only meaningful for additive aggregates; summing
            # averages/min/max would mislead, so it's suppressed otherwise.
            if additive:
                row_total = sum(_num(v) for v in row_data.values())
                lines.append(f"  {r}: {cells}  (row total {_fmt(row_total)})")
            else:
                lines.append(f"  {r}: {cells}")
        else:
            v = data[r]["count"]
            lines.append(f"  {r}: {_fmt(v)}{_pct(v)}")

    if shaped["omitted_rows"] > 0:
        order = "alphabetically" if shaped.get("sort") == "value" else "by magnitude"
        extra = ""
        if shaped["omitted_row_sum"] is not None:
            extra = f", together summing {_fmt(shaped['omitted_row_sum'])}"
        lines.append(
            f"[{shaped['omitted_rows']} more {row_field} value(s) not shown "
            f"(top {len(rows)} {order}{extra}) — narrow with a filter.]"
        )
    if is_2d and shaped["omitted_cols"] > 0:
        lines.append(
            f"[{shaped['omitted_cols']} more {col_field} column(s) not shown.]"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# mode=field_values
# ---------------------------------------------------------------------------

def _query_field_values(
    db, resolver, *, field, search, filters, limit, logic,
) -> ToolReturn:
    if not field:
        return (
            "field_values mode requires 'field' (the field whose distinct "
            "values to enumerate).",
            None,
        )
    eff_limit = (
        _FIELD_VALUES_DEFAULT_LIMIT if limit is None
        else max(1, min(int(limit), _FIELD_VALUES_MAX_LIMIT))
    )
    values, total_distinct, error_hint = get_field_values_query(
        db, resolver, field=field, search=search, limit=eff_limit,
        filters=filters or [], logic=logic,
    )
    if error_hint is not None:
        return error_hint, None
    if not values:
        return f"No values found for field '{field}'.", None

    top_n = len(values)
    lines = [
        f"Field '{field}': {total_distinct} distinct values "
        f"(showing top {top_n}):"
    ]
    for v in values:
        lines.append(f"  {v['value']}  ({v['count']})")
    if total_distinct and total_distinct > top_n:
        lines.append(
            f"[{total_distinct - top_n} more values not shown — narrow with "
            "`search` or a filter, or raise `limit` (≤500).]"
        )

    artifact = {
        "type": "table",
        "title": f"{field}: distinct values ({total_distinct})",
        "columns": [
            {"key": "value", "label": "Value"},
            {"key": "count", "label": "Count"},
        ],
        "series": [
            {"value": v["value"], "count": v["count"]} for v in values
        ],
        "meta": {
            "source_tool": _ARTIFACT_SOURCE,
            "layout": "field_values",
            "field": field,
            "total_distinct": total_distinct,
        },
    }
    return "\n".join(lines), artifact
