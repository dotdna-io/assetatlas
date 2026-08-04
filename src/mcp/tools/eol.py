# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-of-Life (EOL) inventory tool for the AssetAtlas MCP server.

Tool: list_eol_assets.

Answers "how many Windows 10 machines go EOL in the next 30 days?",
"which OS versions are past support?", "EOL exposure by category?" —
questions that aggregate_assets can't express because they need date
bucketing on `os_eol_date` rather than enum grouping on `os_eol_status`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from src.mcp.context import AppContext, get_db
from src.mcp.tools._common import unknown_enum


_VALID_BREAKDOWNS = ("category", "os_normalized", "owner", "location")
_VALID_SCOPES = ("past", "upcoming", "all")

# Breakdown-row paging: default page size + the raised hard cap (was a silent
# LIMIT 15 — §4 "lift the silent caps"). The total-distinct line tells the
# caller how many breakdown values exist beyond the page.
_BREAKDOWN_DEFAULT_LIMIT = 15
_BREAKDOWN_MAX_LIMIT = 100

# Map the breakdown_by key to the catalog display name a drilldown filter must
# use so "show me those N" re-runs through query_assets' resolver verbatim.
_BREAKDOWN_FIELD_LABEL = {
    "os_normalized": "OS Normalized",
    "category": "Category",
    "owner": "Owner",
    "location": "Location",
}

# A chat tool may return plain text (MCP path) or (text, artifact) (chat path).
ToolReturn = Tuple[str, Optional[Dict[str, Any]]]


def _parse_eol_date(value: Any) -> Optional[date]:
    """Parse an ISO date (or datetime) to a ``date``, or None when unparseable.

    ``os_eol_date`` is a DATE column, so a custom window bound collapses to a
    day. None lets the caller fail loudly (§4) rather than guess a window."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        pass
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        return datetime.fromisoformat(candidate).date()
    except ValueError:
        return None


def register_tools(mcp):
    """Register the EOL inventory tool on the FastMCP server."""

    @mcp.tool()
    def list_eol_assets(
        scope: str = "all",
        within_days: Optional[int] = None,
        breakdown_by: str = "os_normalized",
        start: Optional[str] = None,
        end: Optional[str] = None,
        offset: int = 0,
        limit: int = _BREAKDOWN_DEFAULT_LIMIT,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Summarise EOL / approaching-EOL exposure with date bucketing.

        Args:
            scope: Which slice to report on.
                'past'     — already end_of_life as of today.
                'upcoming' — still active/approaching_eol but EOL date is in
                             the future (use with within_days OR start/end to
                             bound).
                'all'      — both past and upcoming (default).
            within_days: Optional horizon (1-3650). When set with scope in
                ('upcoming', 'all'), only assets whose os_eol_date is within
                this many days from now are counted in the "upcoming" bucket.
                Ignored for scope='past'.
            breakdown_by: One of 'os_normalized' (default), 'category',
                'owner', 'location'. Picks the row dimension for the
                top-N table.
            start: Optional ISO date — lower bound on os_eol_date for the
                'upcoming' window (overrides within_days).
            end: Optional ISO date — upper bound on os_eol_date for the
                'upcoming' window (overrides within_days).
            offset: Breakdown-table row offset (paging, default 0).
            limit: Breakdown-table rows per page (1-100, default 15).
        """
        return list_eol_assets_impl(
            get_db(ctx),
            scope=scope,
            within_days=within_days,
            breakdown_by=breakdown_by,
            start=start,
            end=end,
            offset=offset,
            limit=limit,
        )


def list_eol_assets_impl(
    db,
    *,
    scope: str = "all",
    within_days: Optional[int] = None,
    breakdown_by: str = "os_normalized",
    start: Optional[str] = None,
    end: Optional[str] = None,
    offset: int = 0,
    limit: int = _BREAKDOWN_DEFAULT_LIMIT,
    with_artifact: bool = False,
) -> Union[str, ToolReturn]:
    def _ret(text: str, artifact: Optional[Dict[str, Any]] = None):
        # Single return shim so every early-out honours with_artifact: the chat
        # binding always gets a (text, artifact) tuple; MCP/tests get a string.
        return (text, artifact) if with_artifact else text

    if scope not in _VALID_SCOPES:
        return _ret(unknown_enum("scope", scope, _VALID_SCOPES))
    if breakdown_by not in _VALID_BREAKDOWNS:
        return _ret(unknown_enum("breakdown_by", breakdown_by, _VALID_BREAKDOWNS))
    if within_days is not None:
        try:
            within_days = int(within_days)
        except (TypeError, ValueError):
            return _ret("within_days must be an integer (days).")
        if not 1 <= within_days <= 3650:
            return _ret("within_days must be between 1 and 3650.")

    # A custom os_eol_date window (start/end) OVERRIDES within_days for the
    # 'upcoming' bound; unparseable input fails loudly (§4).
    eol_start = eol_end = None
    if start is not None:
        eol_start = _parse_eol_date(start)
        if eol_start is None:
            return _ret(f"Invalid start date {start!r}; use ISO format like "
                        "'2026-01-01'.")
    if end is not None:
        eol_end = _parse_eol_date(end)
        if eol_end is None:
            return _ret(f"Invalid end date {end!r}; use ISO format like "
                        "'2026-12-31'.")
    if eol_start and eol_end and eol_start > eol_end:
        return _ret(f"start {eol_start} is after end {eol_end}.")

    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or _BREAKDOWN_DEFAULT_LIMIT),
                       _BREAKDOWN_MAX_LIMIT))
    custom_window = eol_start is not None or eol_end is not None

    # Scope predicates. merged_into IS NULL excludes the "absorbed by merge"
    # shadows so we never double-count a surviving primary with its merged
    # twins — consistent with how the dashboard and other tools count.
    # Interpolated values are injection-safe: within_days is int-checked and the
    # date bounds are real date objects rendered via isoformat().
    past_pred = "(os_eol_status = 'end_of_life')"
    lo = f"DATE '{eol_start.isoformat()}'" if eol_start else "CURRENT_DATE"
    hi = None
    if custom_window:
        if eol_end:
            hi = f"DATE '{eol_end.isoformat()}'"
    elif within_days:
        hi = f"CURRENT_DATE + INTERVAL '{within_days} days'"
    upcoming_pred = (
        "(os_eol_status IN ('active', 'approaching_eol') "
        f"AND os_eol_date IS NOT NULL AND os_eol_date >= {lo}"
    )
    if hi:
        upcoming_pred += f" AND os_eol_date <= {hi}"
    upcoming_pred += ")"

    def _window_note() -> str:
        if custom_window:
            a = eol_start.isoformat() if eol_start else "today"
            b = eol_end.isoformat() if eol_end else "∞"
            return f" (os_eol_date {a} → {b})"
        if within_days:
            return f" (horizon {within_days} days)"
        return ""

    if scope == "past":
        where = f"{past_pred} AND merged_into IS NULL"
        scope_label = "end_of_life"
    elif scope == "upcoming":
        where = f"{upcoming_pred} AND merged_into IS NULL"
        scope_label = "approaching EOL" + _window_note()
    else:
        where = f"({past_pred} OR {upcoming_pred}) AND merged_into IS NULL"
        scope_label = "end_of_life + approaching" + _window_note()

    total = db.execute_query(
        f"SELECT COUNT(*) AS n FROM assets WHERE {where}"
    )
    total = int(total[0]["n"]) if total else 0

    if total == 0:
        return _ret(f"COUNT: 0 assets match EOL scope '{scope_label}'.")

    status_rows = db.execute_query(
        f"""
        SELECT os_eol_status, COUNT(*) AS n
        FROM assets
        WHERE {where}
        GROUP BY os_eol_status
        ORDER BY n DESC
        """
    ) or []

    # Breakdown row-dim is whitelisted — direct interpolation safe.
    # owner/location are TEXT[]: unnest so each value becomes its own bucket
    # (an asset with multiple owners contributes once per owner — Alice's EOL
    # exposure spans all her assets, even shared ones).
    label_source = (
        f"unnest({breakdown_by})" if breakdown_by in {"owner", "location"}
        else breakdown_by
    )
    # Real total of distinct breakdown values so the page footer can report how
    # many lie beyond the window (§4 "real totals + no silent truncation").
    distinct_rows = db.execute_query(
        f"SELECT COUNT(*) AS n FROM "
        f"(SELECT DISTINCT {label_source} AS label FROM assets WHERE {where}) d"
    )
    total_distinct = int(distinct_rows[0]["n"]) if distinct_rows else 0

    breakdown_rows = db.execute_query(
        f"""
        SELECT label, COUNT(*) AS n,
               MIN(os_eol_date) AS min_eol,
               MAX(os_eol_date) AS max_eol
        FROM (
            SELECT {label_source} AS label, os_eol_date
            FROM assets
            WHERE {where}
        ) sub
        GROUP BY label
        ORDER BY n DESC, label ASC NULLS LAST
        OFFSET {offset} LIMIT {limit}
        """
    ) or []

    # Urgency buckets for "upcoming" and "all": 0-30d / 31-90d / 91-365d /
    # beyond / overdue. Overdue lives in the 'past' predicate, so it is
    # calculated separately to avoid a double-branch SQL.
    horizon_rows = db.execute_query(
        f"""
        SELECT
            SUM(CASE WHEN os_eol_date < CURRENT_DATE THEN 1 ELSE 0 END) AS overdue,
            SUM(CASE WHEN os_eol_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '30 days' THEN 1 ELSE 0 END) AS next_30,
            SUM(CASE WHEN os_eol_date BETWEEN CURRENT_DATE + INTERVAL '31 days' AND CURRENT_DATE + INTERVAL '90 days' THEN 1 ELSE 0 END) AS next_90,
            SUM(CASE WHEN os_eol_date BETWEEN CURRENT_DATE + INTERVAL '91 days' AND CURRENT_DATE + INTERVAL '365 days' THEN 1 ELSE 0 END) AS next_365,
            SUM(CASE WHEN os_eol_date > CURRENT_DATE + INTERVAL '365 days' THEN 1 ELSE 0 END) AS beyond_365,
            SUM(CASE WHEN os_eol_date IS NULL THEN 1 ELSE 0 END) AS no_eol_date
        FROM assets
        WHERE {where}
        """
    )
    horizon = horizon_rows[0] if horizon_rows else {}

    lines = [
        f"COUNT: {total} assets match EOL scope '{scope_label}'.",
        "",
        "Status breakdown:",
    ]
    for row in status_rows:
        status = row.get("os_eol_status") or "(null)"
        lines.append(f"  {status:20s} {int(row['n']):>6d}")

    lines.append("")
    lines.append("Urgency buckets (by os_eol_date):")
    lines.append(f"  overdue (past CURRENT_DATE):  {int(horizon.get('overdue') or 0):>6d}")
    lines.append(f"  next 30 days:                 {int(horizon.get('next_30') or 0):>6d}")
    lines.append(f"  31-90 days:                   {int(horizon.get('next_90') or 0):>6d}")
    lines.append(f"  91-365 days:                  {int(horizon.get('next_365') or 0):>6d}")
    lines.append(f"  beyond 365 days:              {int(horizon.get('beyond_365') or 0):>6d}")
    lines.append(f"  no eol_date recorded:         {int(horizon.get('no_eol_date') or 0):>6d}")

    shown = len(breakdown_rows)
    range_note = (
        f" (rows {offset + 1}-{offset + shown} of {total_distinct} distinct)"
        if total_distinct else ""
    )
    lines.append("")
    lines.append(f"Top {shown} by {breakdown_by}{range_note}:")
    lines.append(
        f"  {'value':40s} {'count':>6s}  {'earliest_eol':>14s}  {'latest_eol':>14s}"
    )
    for row in breakdown_rows:
        label = row.get("label") or "(null)"
        if isinstance(label, list):
            label = ", ".join(str(v) for v in label) or "(empty)"
        min_eol = row.get("min_eol") or ""
        max_eol = row.get("max_eol") or ""
        lines.append(
            f"  {str(label)[:40]:40s} {int(row['n']):>6d}  "
            f"{str(min_eol):>14s}  {str(max_eol):>14s}"
        )
    omitted = max(0, total_distinct - (offset + shown))
    if omitted > 0:
        lines.append(
            f"[{omitted} more {breakdown_by} value(s) not shown — raise `limit` "
            "or page with `offset`.]"
        )

    text = "\n".join(lines)
    if not with_artifact:
        return text
    return text, _build_eol_artifact(
        breakdown_rows, horizon, scope=scope, scope_label=scope_label,
        breakdown_by=breakdown_by, within_days=within_days,
        eol_start=eol_start, eol_end=eol_end, custom_window=custom_window,
        total=total, total_distinct=total_distinct, offset=offset, limit=limit,
    )


def _build_eol_artifact(
    breakdown_rows: List[Dict[str, Any]], horizon: Dict[str, Any], *,
    scope: str, scope_label: str, breakdown_by: str,
    within_days: Optional[int], eol_start: Optional[date],
    eol_end: Optional[date], custom_window: bool,
    total: int, total_distinct: int, offset: int, limit: int,
) -> Optional[Dict[str, Any]]:
    """Build the per-bucket-drillable EOL table artifact (§3.6).

    Each breakdown row carries a ``drilldown`` = the scope's EOL predicate (as
    far as flat AND/OR can express it) + an equality on the breakdown value, so
    "show me those N" re-runs through query_assets verbatim. ``meta.urgency``
    carries a clean ``OS EOL Date`` date-range drilldown per urgency bucket. The
    builder lives here (not in chat.artifacts) and imports ``build_drilldown``
    lazily so the MCP server's import path never pulls the chat package."""
    from src.api.blueprints.chat.artifacts import build_drilldown

    today = date.today()
    arr = breakdown_by in {"owner", "location"}
    bd_field = _BREAKDOWN_FIELD_LABEL[breakdown_by]

    def _row_filter(value: Any) -> Dict[str, Any]:
        # Array dims (owner/location) match via `any`; scalar dims via `eq`.
        if arr:
            return {"field": bd_field, "op": "any", "value": [value]}
        return {"field": bd_field, "op": "eq", "value": value}

    # Scope EOL predicate in query_assets grammar. scope='all' is a past∪upcoming
    # OR that flat filters can't encode — the breakdown filter alone is the
    # honest best-effort (flagged in meta.drilldown_note).
    scope_filters: List[Dict[str, Any]] = []
    if scope == "past":
        scope_filters = [{"field": "OS EOL Status", "op": "eq",
                          "value": "end_of_life"}]
    elif scope == "upcoming":
        scope_filters = [{"field": "OS EOL Status", "op": "any",
                          "value": ["active", "approaching_eol"]}]
        lo_d = eol_start or today
        if custom_window and eol_end:
            scope_filters.append({"field": "OS EOL Date", "op": "between",
                                  "value": [lo_d.isoformat(),
                                            eol_end.isoformat()]})
        elif custom_window:
            scope_filters.append({"field": "OS EOL Date", "op": "gte",
                                  "value": lo_d.isoformat()})
        elif within_days:
            scope_filters.append(
                {"field": "OS EOL Date", "op": "between",
                 "value": [today.isoformat(),
                           (today + timedelta(days=within_days)).isoformat()]})
        else:
            scope_filters.append({"field": "OS EOL Date", "op": "gte",
                                  "value": today.isoformat()})

    series: List[Dict[str, Any]] = []
    for row in breakdown_rows:
        label = row.get("label")
        disp = label
        if isinstance(disp, list):
            disp = ", ".join(str(v) for v in disp)
        if disp in (None, ""):
            disp = "(none)"
        entry: Dict[str, Any] = {
            "label": disp,
            "count": int(row["n"]),
            "earliest_eol": str(row.get("min_eol") or ""),
            "latest_eol": str(row.get("max_eol") or ""),
        }
        if label not in (None, "", []):
            entry["drilldown"] = build_drilldown(
                scope_filters + [_row_filter(label)])
        series.append(entry)

    t = today
    urgency_specs = [
        ("overdue", horizon.get("overdue"),
         {"field": "OS EOL Date", "op": "lt", "value": t.isoformat()}),
        ("next 30 days", horizon.get("next_30"),
         {"field": "OS EOL Date", "op": "between",
          "value": [t.isoformat(), (t + timedelta(days=30)).isoformat()]}),
        ("31-90 days", horizon.get("next_90"),
         {"field": "OS EOL Date", "op": "between",
          "value": [(t + timedelta(days=31)).isoformat(),
                    (t + timedelta(days=90)).isoformat()]}),
        ("91-365 days", horizon.get("next_365"),
         {"field": "OS EOL Date", "op": "between",
          "value": [(t + timedelta(days=91)).isoformat(),
                    (t + timedelta(days=365)).isoformat()]}),
        ("beyond 365 days", horizon.get("beyond_365"),
         {"field": "OS EOL Date", "op": "gt",
          "value": (t + timedelta(days=365)).isoformat()}),
        ("no eol_date", horizon.get("no_eol_date"),
         {"field": "OS EOL Date", "op": "empty", "value": None}),
    ]
    urgency = [
        {"label": lbl, "count": int(cnt or 0),
         "drilldown": build_drilldown([flt])}
        for lbl, cnt, flt in urgency_specs
    ]

    meta: Dict[str, Any] = {
        "source_tool": "list_eol_assets",
        "layout": "list",
        "scope": scope,
        "total": total,
        "total_distinct": total_distinct,
        "offset": offset,
        "limit": limit,
        "breakdown_by": breakdown_by,
        "urgency": urgency,
    }
    if scope == "all":
        meta["drilldown_note"] = (
            "scope=all drill-downs filter by the breakdown value only; the "
            "EOL status/date predicate is an OR flat filters can't encode."
        )
    return {
        "type": "table",
        "title": f"EOL exposure by {breakdown_by} ({scope_label})",
        "columns": [
            {"key": "label", "label": bd_field},
            {"key": "count", "label": "Count"},
            {"key": "earliest_eol", "label": "Earliest EOL"},
            {"key": "latest_eol", "label": "Latest EOL"},
        ],
        "series": series,
        "meta": meta,
    }
