# SPDX-License-Identifier: AGPL-3.0-or-later
"""Chat artifact builders.

Tool invocations optionally produce a structured ``artifact`` alongside
their text summary; the chat service persists it on ``chat_tool_calls``
and forwards it to the frontend for inline rendering (a donut chart, in
v1). Envelope shape is generic and discriminated by ``type`` so future
chart kinds (bar, line, stacked_bar) drop in without a schema break.
"""

from __future__ import annotations

import copy
import datetime as _dt
import decimal as _decimal
import logging
from typing import Any, Dict, List, Literal, Optional, Tuple

from src.mcp.tools.assets import (
    canonical_field_display_name,
    compute_asset_sections,
)

logger = logging.getLogger(__name__)


# Extend this union when new chart types ship (e.g. ``"bar" | "line"``).
ArtifactType = Literal["donut", "stacked_bar", "table"]

# Keys every envelope must carry. Per-type so table envelopes can require
# ``columns`` while donut / stacked_bar do not. The chat ToolRegistry
# drops payloads missing any of these rather than let a malformed
# artifact fail the turn.
_REQUIRED_KEYS_BY_TYPE: Dict[str, Tuple[str, ...]] = {
    "donut":       ("type", "title", "series"),
    "stacked_bar": ("type", "title", "series"),
    "table":       ("type", "title", "series", "columns"),
}

# Max distinct slices (excluding the rolled-up "Other" bucket). Anything
# higher packs the donut and kills legend legibility.
DEFAULT_MAX_SLICES = 6

# Stacked-bar caps: legend gets noisy beyond MAX_STACKS columns; x-axis
# gets cramped beyond MAX_X_BUCKETS rows.
DEFAULT_MAX_STACKS = 8
DEFAULT_MAX_X_BUCKETS = 10

# Table cap: keep the chat panel scrollable. 100 fits a full "list all" of a
# small/medium set in the (scrollable) table; larger sets truncate + offer CSV.
DEFAULT_MAX_TABLE_ROWS = 100


def _safe_int(val: Any) -> int:
    """Coerce to int, returning 0 for None / non-numeric / falsy values.
    Used by aggregate-derived builders so a single malformed cell can't
    crash artifact construction."""
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


def _num(val: Any) -> Any:
    """Coerce an aggregate cell/total to a JSON-safe number WITHOUT truncating.

    int/float pass through; Decimal (psycopg2 returns it for SUM/AVG/MIN/MAX
    over a NUMERIC column) becomes float; None / bool / non-numeric become 0.
    Unlike ``_safe_int`` this preserves fractional values, so a SUM/AVG renders
    its real value (e.g. 0.83) instead of its floor (0)."""
    if val is None or isinstance(val, bool):
        return 0
    if isinstance(val, (int, float)):
        return val
    if isinstance(val, _decimal.Decimal):
        return float(val)
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0


def _jsonify(val: Any) -> Any:
    """Make ``val`` safe to serialize via ``json.dumps``. Repo rows can
    carry datetime / date objects (e.g. last_seen, first_seen); the chat
    repository persists artifacts as JSON, so non-serializable types
    must be coerced before they land in ``series``. Containers recurse;
    primitives pass through; unknown types fall back to ``str(val)``."""
    if val is None or isinstance(val, (str, int, float, bool)):
        return val
    if isinstance(val, _decimal.Decimal):
        # NUMERIC columns (e.g. confidence) arrive as Decimal, which json.dumps
        # can't serialize. Coerce to float so the cell is a real number (the
        # frontend can format it) rather than a string via the str() fallback.
        return float(val)
    if isinstance(val, (_dt.datetime, _dt.date)):
        return val.isoformat()
    if isinstance(val, list):
        return [_jsonify(v) for v in val]
    if isinstance(val, tuple):
        return [_jsonify(v) for v in val]
    if isinstance(val, dict):
        return {k: _jsonify(v) for k, v in val.items()}
    return str(val)


def validate_envelope(
    payload: Any, *, tool_name: str,
) -> Optional[Dict[str, Any]]:
    """Return ``payload`` if it matches the minimal envelope shape, else
    ``None`` with a warning. Never raises — a bad artifact must not fail
    the tool call that produced it. Called by the ToolRegistry before
    persistence so every charted payload is checked at one boundary."""
    if payload is None:
        return None
    if not isinstance(payload, dict):
        logger.warning(
            "Tool %r returned non-dict artifact (%s); dropping.",
            tool_name, type(payload).__name__,
        )
        return None
    art_type = payload.get("type")
    if not isinstance(art_type, str):
        logger.warning(
            "Tool %r artifact has invalid 'type' field (%r); dropping.",
            tool_name, art_type,
        )
        return None
    required = _REQUIRED_KEYS_BY_TYPE.get(art_type)
    if required is None:
        logger.warning(
            "Tool %r artifact has unknown type %r; dropping.",
            tool_name, art_type,
        )
        return None
    missing = [k for k in required if k not in payload]
    if missing:
        logger.warning(
            "Tool %r artifact missing required keys %r; dropping.",
            tool_name, missing,
        )
        return None
    if not isinstance(payload.get("series"), list):
        logger.warning(
            "Tool %r artifact.series is not a list; dropping.", tool_name,
        )
        return None
    if art_type == "table" and not isinstance(payload.get("columns"), list):
        logger.warning(
            "Tool %r artifact.columns is not a list; dropping.", tool_name,
        )
        return None
    return payload


def _humanize_field(field: str) -> str:
    # Prefer the catalog's canonical display name so acronyms stay intact
    # ("OS Normalized", not "Os Normalized"). Fallback path covers fields
    # outside the static/virtual catalog (e.g. ext:... / tag:...).
    canonical = canonical_field_display_name(field)
    if canonical is not None:
        return canonical
    if " " in field:
        return field
    return field.replace("_", " ").title()


def build_donut_from_aggregate(
    result: Dict[str, Any],
    row_field: str,
    *,
    max_slices: int = DEFAULT_MAX_SLICES,
    with_drilldown: bool = False,
    base_filters: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Turn a 1-D ``aggregate_assets`` repo result into a donut envelope,
    or return ``None`` when the input isn't chartable (empty, 2-D pivot,
    zero total, or every count is zero). Top ``max_slices`` rows by
    count are kept; the remainder rolls into a single ``"Other"`` slice
    when its sum is > 0.

    ``with_drilldown`` attaches a ``drilldown`` descriptor (§3.6) to every
    concrete slice (not the rolled-up ``"Other"``) so "show me those N" is a
    single deterministic ``query_assets mode=list`` re-run."""
    if not result:
        return None

    rows = result.get("rows") or []
    columns = result.get("columns") or []
    data = result.get("data") or {}

    # _num (not int) so a fractional SUM total (e.g. 0.9) still passes the >0
    # guard and is charted, instead of int()-flooring to 0 and bailing.
    grand_total = _num(result.get("grand_total") or 0)

    if not rows or columns or grand_total <= 0:
        return None

    scored: List[tuple] = []
    for row in rows:
        row_data = data.get(row) or {}
        count = _num(row_data.get("count", 0))
        scored.append((row, count))
    scored.sort(key=lambda pair: pair[1], reverse=True)

    if len(scored) > max_slices:
        top = scored[:max_slices]
        tail = scored[max_slices:]
        other_value = sum(value for _, value in tail)
        series = [{"label": label, "value": value} for label, value in top]
        if other_value > 0:
            series.append({"label": "Other", "value": other_value})
            other_count = len(tail)
        else:
            other_count = 0
    else:
        series = [{"label": label, "value": value} for label, value in scored]
        other_count = 0

    # Guard against the degenerate case where grand_total is non-zero but
    # every per-row count came back zero — never ship an empty chart.
    if not any(slice_["value"] > 0 for slice_ in series):
        return None

    if with_drilldown:
        for slice_ in series:
            if slice_["label"] != "Other":
                slice_["drilldown"] = drilldown_for_aggregate_cell(
                    row_field, slice_["label"], base_filters=base_filters,
                )

    artifact_type: ArtifactType = "donut"
    return {
        "type": artifact_type,
        "title": f"Assets by {_humanize_field(row_field)}",
        "series": series,
        "meta": {
            "source_tool": "aggregate_assets",
            "total": grand_total,
            "other_count": other_count,
            "row_field": row_field,
        },
    }


def build_stacked_bar_from_aggregate(
    result: Dict[str, Any],
    row_field: str,
    col_field: str,
    *,
    max_stacks: int = DEFAULT_MAX_STACKS,
    max_x: int = DEFAULT_MAX_X_BUCKETS,
    with_drilldown: bool = False,
    base_filters: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """2-D ``aggregate_assets`` result → stacked-bar envelope, or ``None``
    if the input is 1-D, empty, or zero-total. Top ``max_x`` rows by row
    total become x-axis buckets; tail rolls into ``"Other"``. Within each
    x bucket, the top ``max_stacks`` columns by per-row count are kept;
    tail collapses into a single ``"Other"`` stack.

    ``with_drilldown`` attaches a ``drilldown`` descriptor (§3.6) to every
    concrete stack (row×col cell), excluding the rolled-up ``"Other"`` bucket
    and stacks, so each cell re-runs via ``query_assets mode=list``."""
    if not result:
        return None
    rows = result.get("rows") or []
    columns = result.get("columns") or []
    data = result.get("data") or {}
    grand_total = _num(result.get("grand_total") or 0)
    if not rows or not columns or grand_total <= 0:
        return None

    # Rank rows by total count and cap. _num (not _safe_int) so fractional
    # SUM cells aren't floored before ranking/charting.
    def row_total(r: str) -> float:
        d = data.get(r) or {}
        return sum(_num(d.get(c, 0)) for c in columns)

    ranked_rows = sorted(rows, key=row_total, reverse=True)
    top_rows = ranked_rows[:max_x]
    tail_rows = ranked_rows[max_x:]

    def stacks_for(row_keys: List[str]) -> List[Dict[str, Any]]:
        # Sum each col across the given rows, then rank + cap.
        col_totals = {
            c: sum(_num((data.get(r) or {}).get(c, 0)) for r in row_keys)
            for c in columns
        }
        ranked = sorted(columns, key=lambda c: col_totals[c], reverse=True)
        top = ranked[:max_stacks]
        tail = ranked[max_stacks:]
        stacks: List[Dict[str, Any]] = [
            {"label": c, "value": col_totals[c]}
            for c in top
            if col_totals[c] > 0
        ]
        tail_sum = sum(col_totals[c] for c in tail)
        if tail_sum > 0:
            stacks.append({"label": "Other", "value": tail_sum})
        return stacks

    series: List[Dict[str, Any]] = [
        {"x_label": r, "stacks": stacks_for([r])} for r in top_rows
    ]
    if tail_rows:
        tail_stacks = stacks_for(tail_rows)
        if tail_stacks:
            series.append({"x_label": "Other", "stacks": tail_stacks})

    # Strip x buckets that ended up with no stacks (all-zero rows).
    series = [s for s in series if s["stacks"]]
    if not series:
        return None

    if with_drilldown:
        for entry in series:
            if entry["x_label"] == "Other":
                continue
            for stack in entry["stacks"]:
                if stack["label"] != "Other":
                    stack["drilldown"] = drilldown_for_aggregate_cell(
                        row_field, entry["x_label"], col_field, stack["label"],
                        base_filters=base_filters,
                    )

    artifact_type: ArtifactType = "stacked_bar"
    return {
        "type": artifact_type,
        "title": (
            f"Assets by {_humanize_field(row_field)} × "
            f"{_humanize_field(col_field)}"
        ),
        "series": series,
        "meta": {
            "source_tool": "aggregate_assets",
            "total": grand_total,
            "row_field": row_field,
            "col_field": col_field,
        },
    }


def build_table_from_aggregate(
    result: Optional[Dict[str, Any]],
    row_field: str,
    col_field: Optional[str],
    *,
    max_rows: int = DEFAULT_MAX_TABLE_ROWS,
    with_drilldown: bool = False,
    base_filters: Optional[List[Dict[str, Any]]] = None,
    show_total: bool = True,
) -> Optional[Dict[str, Any]]:
    """Render any aggregate result as a generic count/value table. Used as the
    donut/stacked-bar fallback AND as the primary artifact for non-additive
    aggregates (AVG/MIN/MAX/COUNT_DISTINCT), where a proportional chart would
    mislead. Pure presentation.

    Cell values are coerced through ``_jsonify`` (preserving fractional
    SUM/AVG values and MIN/MAX strings/dates, NOT flooring them to int) so the
    rendered table matches the LLM text. ``show_total`` controls the 2-D Total
    column — pass ``False`` for non-additive aggregates where summing the cells
    (e.g. averages) is meaningless. ``with_drilldown`` attaches a row-level
    ``drilldown`` (§3.6); ``base_filters`` is prepended so the drill-down keeps
    the aggregate's own filter scope."""
    if not result:
        return None
    rows = result.get("rows") or []
    columns = result.get("columns") or []
    data = result.get("data") or {}
    # Presence of rows — not a positive grand total — is the "has data" signal:
    # AVG<1, MIN of negatives, or a zero SUM are all valid results to render.
    if not rows:
        return None

    truncated = len(rows) > max_rows
    shown_rows = rows[:max_rows]
    row_key = row_field

    if columns:
        # 2-D pivot. Columns: row_field, each col_field value, (Total if additive).
        cols_def: List[Dict[str, str]] = (
            [{"key": row_key, "label": _humanize_field(row_field)}]
            + [{"key": c, "label": c} for c in columns]
            + ([{"key": "_total", "label": "Total"}] if show_total else [])
        )
        series: List[Dict[str, Any]] = []
        for r in shown_rows:
            row_data = data.get(r) or {}
            entry: Dict[str, Any] = {row_key: r}
            total = 0
            for c in columns:
                raw = row_data.get(c, 0)
                entry[c] = _jsonify(raw)
                total += _num(raw)
            if show_total:
                entry["_total"] = total
            series.append(entry)
        title = (f"Assets by {_humanize_field(row_field)} × "
                 f"{_humanize_field(col_field)}")
    else:
        # 1-D flat. Columns: row_field, count.
        cols_def = [
            {"key": row_key, "label": _humanize_field(row_field)},
            {"key": "count", "label": "Count"},
        ]
        series = []
        for r in shown_rows:
            row_data = data.get(r) or {}
            series.append(
                {row_key: r, "count": _jsonify(row_data.get("count", 0))}
            )
        title = f"Assets by {_humanize_field(row_field)}"

    if with_drilldown:
        for entry in series:
            label = entry.get(row_key)
            if label is not None and label != "(other)":
                entry["drilldown"] = drilldown_for_aggregate_cell(
                    row_field, label, base_filters=base_filters,
                )

    grand_total = _num(result.get("grand_total") or 0)

    artifact_type: ArtifactType = "table"
    return {
        "type": artifact_type,
        "title": title,
        "columns": cols_def,
        "series": series,
        "meta": {
            "source_tool": "aggregate_assets",
            "layout": "list",
            "total": grand_total,
            "truncated": truncated,
            "row_field": row_field,
            "col_field": col_field,
        },
    }


# --- drill-down descriptor (plan §3.6) ---------------------------------------
# The ONE shape every aggregate/summary tool emits so "show me those N" is a
# single deterministic re-run: {"filters": [{field, op, value}], "col_value": …}.
# ``filters`` is the standard §3.1 grammar that query_assets mode=list accepts
# verbatim; ``col_value`` (optional) records the pivoted column bucket of a 2-D
# cell. Phase 0 defines the shape + builders; the aggregate tools emit it later.

def build_drilldown(
    filters: List[Dict[str, Any]], *, col_value: Any = None,
) -> Dict[str, Any]:
    """Construct a drill-down descriptor from a list of ``{field, op, value}``
    filters. The filters are DEEP-copied so a later mutation of the source —
    including a mutable ``value`` list (the any/none/all/between operators take
    one) — can't corrupt the persisted artifact. ``col_value`` is included only
    when set."""
    drilldown: Dict[str, Any] = {"filters": copy.deepcopy(list(filters))}
    if col_value is not None:
        drilldown["col_value"] = col_value
    return drilldown


def drilldown_for_aggregate_cell(
    row_field: str, row_value: Any,
    col_field: Optional[str] = None, col_value: Any = None,
    *, base_filters: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build the drill-down for one aggregate row (1-D) or cell (2-D): an ``eq``
    filter on the row bucket, plus an ``eq`` filter on the column bucket for a
    2-D cell. ``row_field``/``col_field`` pass through verbatim (bare name or
    encoded id), so the descriptor re-runs through the same resolution path
    query_assets uses.

    ``base_filters`` — the filters that scoped the aggregate — are PREPENDED so
    the drill-down reproduces the same population the cell counted. Without
    them, drilling into a filtered aggregate (e.g. "category=server, breakdown
    by Owner") would re-run with only ``{Owner eq X}`` and return assets across
    ALL categories — more rows than the cell, breaking "show me those N" (§3.6)."""
    filters: List[Dict[str, Any]] = list(base_filters or [])
    filters.append({"field": row_field, "op": "eq", "value": row_value})
    if col_field is not None and col_value is not None:
        filters.append({"field": col_field, "op": "eq", "value": col_value})
    return build_drilldown(filters, col_value=col_value)


# Display order for the single-asset key-value table. Aligned with
# get_asset_impl's text rendering so users see the same fields.
_SINGLE_ASSET_FIELDS: List[Tuple[str, str]] = [
    ("hostname", "Hostname"), ("fqdn", "FQDN"), ("ip", "IP Address"),
    ("mac", "MAC Address"), ("sn", "Serial Number"),
    ("category", "Category"), ("subcategory", "Subcategory"),
    ("os_normalized", "OS"), ("os_eol_status", "OS EOL Status"),
    ("manufacturer", "Manufacturer"), ("model", "Model"),
    ("owner", "Owner"), ("location", "Location"),
    ("confidence", "Confidence"),
    ("data_source", "Data Sources"),
    ("first_seen", "First Seen"), ("last_seen", "Last Seen"),
]

# Default column set for the multi-asset list/search table.
# Mirrors the explorer's default visible columns. Order matters.
_LIST_ASSET_COLUMNS: List[Tuple[str, str]] = [
    ("hostname", "Hostname"),
    ("ip", "IP"),
    ("category", "Category"),
    ("subcategory", "Subcategory"),
    ("os_normalized", "OS"),
    ("owner", "Owner"),
    ("data_source", "Data Source"),
    ("last_seen", "Last Seen"),
]


def build_table_from_asset(
    asset: Optional[Dict[str, Any]], *, asset_id: str,
    providers: Optional[List[Dict[str, Any]]] = None,
    tags: Optional[List[Dict[str, Any]]] = None,
    source: Optional[str] = None,
    field_search: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Single-asset key-value table. Skips fields the asset doesn't carry.

    When ``providers`` (the raw get_asset_providers rows) are supplied the
    artifact gains the UI-fidelity rows that the chat ``get_asset`` shows: the
    private/public IP split + geo, MAC vendor, tags, the posture matrix with
    contributing provider badges, and per-source extended-attribute VALUES
    (not a field count). ``source``/``field_search`` bound the per-source
    ext-attr rows (see ``compute_asset_sections``). The old 2-arg call (no
    providers / no AssetAtlas enrichment) is unchanged — base rows only."""
    if not asset:
        return None
    series: List[Dict[str, Any]] = []
    for key, label in _SINGLE_ASSET_FIELDS:
        val = asset.get(key)
        if val is None or val == "" or val == [] or val == {}:
            continue
        series.append({"field": label, "value": _jsonify(val)})

    sections = compute_asset_sections(
        asset, providers or [], source=source, field_search=field_search,
    )

    # IP split + geo derive from the asset (extended_attributes.AssetAtlas).
    # Surface the split ONLY when a real public/private split exists, so the
    # fallback (ip_private == merged ip) doesn't duplicate the base IP row.
    ext = asset.get("extended_attributes")
    aa = ext.get("AssetAtlas") if isinstance(ext, dict) else None
    aa = aa if isinstance(aa, dict) else {}
    if aa.get("ip_private") or aa.get("ip_public"):
        if sections["ip_private"]:
            series.append({"field": "Private IP",
                           "value": ", ".join(str(v) for v in sections["ip_private"])})
        if sections["ip_public"]:
            series.append({"field": "Public IP",
                           "value": ", ".join(str(v) for v in sections["ip_public"])})
    for glabel, gkey in (("Country", "country"), ("Region", "region"),
                         ("City", "city")):
        if sections["geo"].get(gkey):
            series.append({"field": glabel, "value": sections["geo"][gkey]})
    if sections["mac_vendor"]:
        series.append({"field": "MAC Vendor", "value": sections["mac_vendor"]})
    if tags:
        tag_names = ", ".join(
            t.get("tag_name") for t in tags if t.get("tag_name"))
        if tag_names:
            series.append({"field": "Tags", "value": tag_names})

    # Posture matrix + per-source ext-attr VALUES require the providers, so
    # they only appear in the rich (providers-supplied) mode.
    if providers is not None:
        for c in sections["posture"]["checks"]:
            mark = "PASS" if c["passed"] else "FAIL"
            badge = f" — {', '.join(c['providers'])}" if c["providers"] else ""
            series.append({"field": f"Posture: {c['label']}",
                           "value": f"{mark}{badge}"})
        for g in sections["providers"]:
            for ext_key, ext_val in g["extended"].items():
                series.append({"field": f"{g['source_name']} · {ext_key}",
                               "value": _jsonify(ext_val)})

    if not series:
        return None
    artifact_type: ArtifactType = "table"
    return {
        "type": artifact_type,
        "title": f"Asset {asset_id}",
        "columns": [
            {"key": "field", "label": "Field"},
            {"key": "value", "label": "Value"},
        ],
        "series": series,
        "meta": {
            "source_tool": "get_asset",
            "layout": "keyvalue",
            "asset_id": asset_id,
        },
    }


def build_table_from_assets(
    rows: List[Dict[str, Any]], *, total: int, source_tool: str,
    max_rows: int = DEFAULT_MAX_TABLE_ROWS,
    query: Optional[Dict[str, Any]] = None,
    columns: Optional[List[Tuple[str, str]]] = None,
) -> Optional[Dict[str, Any]]:
    """Multi-asset list/search table. ``rows`` is the structured repo output
    with snake-cased keys. ``total`` is the true match count, which may exceed
    ``len(rows)``. ``query`` (when provided) is the originating list query in
    the chat/MCP filter shape; it is echoed into ``meta.query`` so the frontend
    can re-run it via GET /api/assets/export.csv for a full-set download.

    ``columns`` (when provided) is a resolved ``[(row_key, display_label)]`` list
    — the caller's selected/default fields (see
    ``_query.resolve_list_columns``) — that drives BOTH which keys render and
    their header labels, so ext:/group:/tag:/posture/virtual columns show with
    their catalog display name rather than an opaque encoded key. Defaults to the
    Explorer default column set (``_LIST_ASSET_COLUMNS``) when not supplied."""
    if not rows:
        return None
    col_defs = columns or _LIST_ASSET_COLUMNS
    truncated = len(rows) > max_rows
    shown = rows[:max_rows]
    series: List[Dict[str, Any]] = []
    for r in shown:
        out_row: Dict[str, Any] = {}
        for key, _ in col_defs:
            v = r.get(key)
            if v is not None:
                out_row[key] = _jsonify(v)
        series.append(out_row)
    title = (
        f"Assets ({total} total, showing {len(shown)})"
        if truncated or total != len(shown)
        else f"Assets ({total})"
    )
    meta: Dict[str, Any] = {
        "source_tool": source_tool,
        "layout": "list",
        "total": total,
        "truncated": truncated,
    }
    if query is not None:
        meta["query"] = query
    artifact_type: ArtifactType = "table"
    return {
        "type": artifact_type,
        "title": title,
        "columns": [{"key": k, "label": label} for k, label in col_defs],
        "series": series,
        "meta": meta,
    }


# Columns for the alerts list table (get_alerts). Order matters.
_ALERT_TABLE_COLUMNS: List[Tuple[str, str]] = [
    ("severity", "Severity"),
    ("type", "Type"),
    ("status", "Status"),
    ("title", "Title"),
    ("asset_count", "Assets"),
    ("source", "Source"),
    ("created_at", "Created"),
]

# A time axis naturally has many buckets (e.g. 90 daily, 24 hourly), so the
# alert timeline uses a far higher x cap than the categorical stacked bar.
DEFAULT_MAX_TIMELINE_BUCKETS = 90

# Severity stack order — critical first so the chart reads worst-at-the-bottom.
_ALERT_SEVERITY_ORDER = ("critical", "high", "medium", "low")


def build_table_from_alerts(
    items: List[Dict[str, Any]], *, total: int,
    page: int = 1, per_page: Optional[int] = None,
    max_rows: int = DEFAULT_MAX_TABLE_ROWS,
) -> Optional[Dict[str, Any]]:
    """Alerts list → table envelope. ``total`` is the true window count (may
    exceed the page). Each row carries ``alert_id`` so the model can drill via
    get_alert_detail — alerts link to assets through an id ARRAY, not a catalog
    field, so a §3.6 query_assets filter can't express the drill; the alert_id →
    get_alert_detail chain is the genuine path to the affected assets."""
    if not items:
        return None
    truncated = len(items) > max_rows
    shown = items[:max_rows]
    series: List[Dict[str, Any]] = []
    for a in shown:
        row: Dict[str, Any] = {}
        for key, _ in _ALERT_TABLE_COLUMNS:
            v = a.get(key)
            if v is not None:
                row[key] = _jsonify(v)
        aid = a.get("id")
        row["alert_id"] = str(aid) if aid is not None else None
        series.append(row)
    title = (
        f"Alerts ({total})" if total == len(shown)
        else f"Alerts ({total} total, showing {len(shown)})"
    )
    artifact_type: ArtifactType = "table"
    return {
        "type": artifact_type,
        "title": title,
        "columns": [{"key": k, "label": label}
                    for k, label in _ALERT_TABLE_COLUMNS],
        "series": series,
        "meta": {
            "source_tool": "get_alerts",
            "layout": "list",
            "total": total,
            "truncated": truncated,
            "page": page,
            "per_page": per_page,
        },
    }


def build_alerts_timeline_chart(
    timeline: List[Dict[str, Any]], *, bucket: str = "day",
    max_buckets: int = DEFAULT_MAX_TIMELINE_BUCKETS,
) -> Optional[Dict[str, Any]]:
    """Alerts created-at timeline → stacked-bar envelope (x = time bucket,
    stacks = severity). Returns ``None`` when there is nothing to chart. A
    ``line`` type would be dropped by ``validate_envelope`` (only donut/
    stacked_bar/table render), so a per-bucket severity stack is the timeline's
    chartable shape."""
    if not timeline:
        return None
    truncated = len(timeline) > max_buckets
    shown = timeline[:max_buckets]
    series: List[Dict[str, Any]] = []
    for b in shown:
        by_sev = b.get("by_severity") or {}
        stacks: List[Dict[str, Any]] = []
        for s in _ALERT_SEVERITY_ORDER:
            v = _safe_int(by_sev.get(s, 0))
            if v > 0:
                stacks.append({"label": s, "value": v})
        if stacks:
            series.append({"x_label": str(b.get("date")), "stacks": stacks})
    if not series:
        return None
    artifact_type: ArtifactType = "stacked_bar"
    return {
        "type": artifact_type,
        "title": f"Alerts over time ({bucket})",
        "series": series,
        "meta": {
            "source_tool": "get_alerts",
            "bucket": bucket,
            "truncated": truncated,
            "buckets": len(timeline),
        },
    }


# Columns for one alert's affected-asset list (get_alert_detail). Order matters.
_ALERT_ASSET_COLUMNS: List[Tuple[str, str]] = [
    ("hostname", "Hostname"),
    ("category", "Category"),
    ("ip", "IP"),
    ("model", "Model"),
]


def build_table_from_alert_assets(
    assets: List[Dict[str, Any]], *, total: int, title: str = "Alert",
    max_rows: int = DEFAULT_MAX_TABLE_ROWS,
) -> Optional[Dict[str, Any]]:
    """One alert's affected assets → table envelope. ``total`` is the alert's
    full affected-asset count (may exceed the returned page). Each row carries
    ``asset_id`` so a follow-up can pull that asset (e.g. get_asset)."""
    if not assets:
        return None
    truncated = len(assets) > max_rows
    shown = assets[:max_rows]
    series: List[Dict[str, Any]] = []
    for a in shown:
        row: Dict[str, Any] = {}
        for key, _ in _ALERT_ASSET_COLUMNS:
            v = a.get(key)
            if v is not None:
                row[key] = _jsonify(v)
        aid = a.get("id")
        row["asset_id"] = str(aid) if aid is not None else None
        series.append(row)
    art_title = (
        f"Affected assets ({total})" if total == len(shown)
        else f"Affected assets ({total} total, showing {len(shown)})"
    )
    artifact_type: ArtifactType = "table"
    return {
        "type": artifact_type,
        "title": art_title,
        "columns": [{"key": k, "label": label}
                    for k, label in _ALERT_ASSET_COLUMNS],
        "series": series,
        "meta": {
            "source_tool": "get_alert_detail",
            "layout": "list",
            "total": total,
            "truncated": truncated,
            "alert_title": title,
        },
    }
