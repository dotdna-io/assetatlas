# SPDX-License-Identifier: AGPL-3.0-or-later
"""Chronicle-driven tools for the AssetAtlas MCP server.

Tools: list_recent_changes, get_asset_trend.

Both operate over `asset_chronicle` (TimescaleDB hypertable of per-asset
change events). `list_recent_changes` answers "what changed across the
fleet in the last N days"; `get_asset_trend` answers "how many assets
were created / removed per time bucket over the last N days". Neither is
expressible via the existing per-asset `get_asset_history` tool.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from src.mcp.context import AppContext, get_db
from src.mcp.tools._common import (
    format_hostname,
    overflow_note,
    resolve_time_range_or_error,
    unknown_enum,
)


_EVENT_TYPES = (
    "attribute_updated",
    "asset_created",
    "asset_removed",
    "asset_readded",
    "asset_reactivated",
    "asset_retired",
    "asset_went_inactive",
)


_BUCKET_TO_TRUNC = {
    "day": "day",
    "week": "week",
    "month": "month",
}

# Hard cap on rows rendered per section. Was an effective 10 (the sample
# sub-cap below clamped to min(limit, 10), hiding the rest of the feed even
# when the caller asked for more — §4 "lift the silent caps"). The full set
# still drives the COUNT totals; only the rendered tail is bounded.
_MAX_LIMIT = 100

# Chronicle events join `assets` so we can apply the merge filter. LEFT JOIN
# (not INNER) keeps lifecycle events whose asset has left `assets`.
_CHRONICLE_FROM = "FROM asset_chronicle c LEFT JOIN assets a ON a.id = c.asset_id"
# Drop events for assets absorbed by a merge (merged_into set) while keeping
# events for assets no longer present at all (removed/retired → a.id IS NULL).
_MERGED_OK = "(a.merged_into IS NULL OR a.id IS NULL)"


def _parse_change_dt(value: Any) -> Optional[datetime]:
    """Parse an ISO date/datetime to an aware UTC datetime, or None.

    Accepts '2026-01-01' and '2026-01-01T13:00' (optionally 'Z'-suffixed).
    Naive values are assumed UTC so they compare against the timestamptz
    ``time`` column. None on anything unparseable so the caller fails loudly
    (§4) instead of guessing a window. Mirrors monitoring._parse_history_dt."""
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _resolve_change_window(
    time_range: str, start: Optional[str], end: Optional[str],
):
    """Resolve the chronicle time window.

    A custom ``start`` and/or ``end`` (either bound) defines an arbitrary range
    and OVERRIDES the ``time_range`` preset; unparseable input fails loudly
    rather than silently answering a default window (a wrong window is worse
    than an error — §4). Returns ``(where_sql, params, label, None)`` on success
    or ``(None, None, None, error_string)``. Window predicates are bound params
    (``%(win_*)s``); the preset path interpolates only the whitelisted interval
    literal from the shared enum map."""
    if start is not None or end is not None:
        params: Dict[str, Any] = {}
        clauses: List[str] = []
        if start is not None:
            sdt = _parse_change_dt(start)
            if sdt is None:
                return None, None, None, (
                    f"Invalid start date {start!r}; use ISO format like "
                    "'2026-01-01' or '2026-01-01T13:00'."
                )
            clauses.append("c.time >= %(win_start)s")
            params["win_start"] = sdt
        if end is not None:
            edt = _parse_change_dt(end)
            if edt is None:
                return None, None, None, (
                    f"Invalid end date {end!r}; use ISO format like "
                    "'2026-02-01' or '2026-02-01T13:00'."
                )
            # A date-only end means "through that whole day"; extend to
            # end-of-day so `time <= midnight` doesn't drop same-day events.
            if "T" not in end and ":" not in end:
                edt = edt.replace(hour=23, minute=59, second=59,
                                  microsecond=999999)
            clauses.append("c.time <= %(win_end)s")
            params["win_end"] = edt
        label = " → ".join(
            str(params[k].date())
            for k in ("win_start", "win_end") if k in params
        ) or "custom window"
        return " AND ".join(clauses), params, label, None

    interval, err = resolve_time_range_or_error(time_range)
    if err:
        return None, None, None, err
    return f"c.time > NOW() - INTERVAL '{interval}'", {}, time_range, None


def _attribute_typo_warning(
    db, window_where: str, window_params: Dict[str, Any], attribute: str,
) -> Optional[str]:
    """When an attribute filter matched nothing, return a warning iff the
    attribute isn't in the window's real change vocabulary (a typo), else None.

    The vocabulary is the DISTINCT changed_attributes of attribute_updated
    events in the window — exactly the set whose members would have produced a
    non-zero count. So this is only worth running on the zero-count path: a
    matched attribute is in the vocabulary by construction (§4)."""
    vocab_rows = db.execute_query(
        f"SELECT DISTINCT unnest(c.changed_attributes) AS attr "
        f"{_CHRONICLE_FROM} "
        f"WHERE {window_where} AND {_MERGED_OK} "
        f"  AND c.event_type = 'attribute_updated'",
        window_params,
    ) or []
    known = sorted({r["attr"] for r in vocab_rows if r.get("attr")})
    if attribute in known:
        return None
    if known:
        return (
            f"⚠ attribute {attribute!r} did not change for any asset in this "
            f"window (likely a typo). Tracked here: {', '.join(known[:20])}"
            + (", …" if len(known) > 20 else "") + "."
        )
    return (
        f"⚠ attribute {attribute!r} not found: no attribute_updated events in "
        "this window at all."
    )


def register_tools(mcp):
    """Register chronicle-driven tools on the FastMCP server."""

    @mcp.tool()
    def list_recent_changes(
        time_range: str = "7d",
        event_type: Optional[str] = None,
        attribute: Optional[str] = None,
        limit: int = 20,
        start: Optional[str] = None,
        end: Optional[str] = None,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Summarise asset changes across the whole fleet over a time window.

        Unlike get_asset_history (one asset at a time), this rolls up the
        chronicle so the user can ask "what's been changing lately?" without
        iterating assets.

        Args:
            time_range: Preset window: '24h', '7d', '30d', or '90d'. Ignored
                when start/end are given.
            event_type: Optional filter: one of 'attribute_updated',
                'asset_created', 'asset_removed', 'asset_readded',
                'asset_reactivated', 'asset_retired', 'asset_went_inactive'.
            attribute: Optional filter — only count events where this
                attribute name appears in the changed_attributes[] array
                (e.g. 'owner', 'os_normalized'). An attribute that never
                changed in the window is flagged (likely a typo) rather than
                silently returning zero.
            limit: Max rows to show per section (1-100, default 20).
            start: Optional ISO date/datetime for a custom window start
                (e.g. '2026-01-01'); overrides time_range.
            end: Optional ISO date/datetime for a custom window end.
        """
        return list_recent_changes_impl(
            get_db(ctx),
            time_range=time_range,
            event_type=event_type,
            attribute=attribute,
            limit=limit,
            start=start,
            end=end,
        )

    @mcp.tool()
    def get_asset_trend(
        time_range: str = "30d",
        bucket: str = "day",
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Time-bucketed asset additions and removals over a window.

        Answers "how many new assets per week?" / "when did we onboard the
        bulk of these?" — aggregate_assets can't bucket by time interval.

        Args:
            time_range: Window size: '24h', '7d', '30d', or '90d'.
            bucket: Bucket granularity: 'day', 'week', or 'month'.
                Defaults to 'day' for short windows, caller should pick
                'week' or 'month' for longer ones to keep output readable.
        """
        return get_asset_trend_impl(
            get_db(ctx),
            time_range=time_range,
            bucket=bucket,
        )


def list_recent_changes_impl(
    db,
    *,
    time_range: str = "7d",
    event_type: Optional[str] = None,
    attribute: Optional[str] = None,
    limit: int = 20,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> str:
    limit = max(1, min(int(limit or 20), _MAX_LIMIT))

    window_where, window_params, window_label, err = _resolve_change_window(
        time_range, start, end,
    )
    if err:
        return err

    params: Dict[str, Any] = dict(window_params)
    wheres = [window_where, _MERGED_OK]

    if event_type:
        if event_type not in _EVENT_TYPES:
            return unknown_enum("event_type", event_type, _EVENT_TYPES)
        wheres.append("c.event_type = %(event_type)s")
        params["event_type"] = event_type

    if attribute:
        wheres.append("%(attribute)s = ANY(c.changed_attributes)")
        params["attribute"] = attribute

    where_sql = " AND ".join(wheres)

    totals = db.execute_query(
        f"""
        SELECT c.event_type, COUNT(*) AS n,
               COUNT(DISTINCT c.asset_id) AS assets
        {_CHRONICLE_FROM}
        WHERE {where_sql}
        GROUP BY c.event_type
        ORDER BY n DESC
        """,
        params,
    ) or []

    grand_total = sum(int(r["n"]) for r in totals)
    distinct_rows = db.execute_query(
        f"SELECT COUNT(DISTINCT c.asset_id) AS n {_CHRONICLE_FROM} "
        f"WHERE {where_sql}",
        params,
    )
    distinct_assets = int(distinct_rows[0]["n"]) if distinct_rows else 0

    if grand_total == 0:
        zero = (
            f"COUNT: 0 events in window {window_label}"
            + (f" (event_type={event_type})" if event_type else "")
            + (f" (attribute={attribute})" if attribute else "")
            + "."
        )
        # An attribute filter that matched nothing is either a quiet attribute
        # or a typo — surface the window's real change vocabulary so the model
        # can tell which, instead of reading the zero as "nothing changed" (§4).
        if attribute:
            warn = _attribute_typo_warning(
                db, window_where, window_params, attribute)
            if warn:
                return f"{warn}\n{zero}"
        return zero

    lines: List[str] = [
        f"COUNT: {grand_total} change events in window {window_label} "
        f"across {distinct_assets} distinct assets."
    ]
    filters_bits = []
    if event_type:
        filters_bits.append(f"event_type={event_type}")
    if attribute:
        filters_bits.append(f"attribute={attribute}")
    if filters_bits:
        lines.append(f"(filters: {', '.join(filters_bits)})")
    lines.append("")

    lines.append("Events by type:")
    for row in totals:
        lines.append(
            f"  {row['event_type']:22s} {int(row['n']):>6d} events "
            f"across {int(row['assets']):>5d} assets"
        )

    if not attribute:
        attr_rows = db.execute_query(
            f"""
            SELECT attr, COUNT(*) AS n
            FROM (
                SELECT unnest(c.changed_attributes) AS attr
                {_CHRONICLE_FROM}
                WHERE {where_sql} AND c.event_type = 'attribute_updated'
            ) q
            GROUP BY attr
            ORDER BY n DESC
            LIMIT %(limit)s
            """,
            {**params, "limit": limit},
        ) or []
        if attr_rows:
            lines.append("")
            lines.append(f"Top {len(attr_rows)} most-changed attributes:")
            for row in attr_rows:
                lines.append(f"  {row['attr']:30s} {int(row['n']):>6d} changes")

    sample_rows = db.execute_query(
        f"""
        SELECT c.time, c.event_type, c.changed_attributes,
               a.hostname, a.category
        {_CHRONICLE_FROM}
        WHERE {where_sql}
        ORDER BY c.time DESC
        LIMIT %(limit)s
        """,
        {**params, "limit": limit},
    ) or []
    if sample_rows:
        lines.append("")
        lines.append(f"Most recent {len(sample_rows)} events:")
        for row in sample_rows:
            host_str = format_hostname(row, fallback_key="asset_id")
            attrs = row.get("changed_attributes") or []
            attrs_str = ", ".join(attrs) if attrs else "-"
            lines.append(
                f"  {row['time']} — {row['event_type']:18s} "
                f"{host_str:25s} [{attrs_str}]"
            )
        # The sample is the most-recent slice of `grand_total` events; signal the
        # tail so the feed isn't mistaken for the whole window (§4).
        note = overflow_note(
            grand_total, len(sample_rows), noun="events",
            hint="raise `limit` (≤100) or narrow the time window")
        if note:
            lines.append(note)
    return "\n".join(lines)


def get_asset_trend_impl(
    db,
    *,
    time_range: str = "30d",
    bucket: str = "day",
) -> str:
    interval, err = resolve_time_range_or_error(time_range)
    if err:
        return err
    trunc = _BUCKET_TO_TRUNC.get(bucket)
    if trunc is None:
        return unknown_enum("bucket", bucket, _BUCKET_TO_TRUNC.keys())

    # DATE_TRUNC is server-side; safe to interpolate the whitelisted token.
    rows = db.execute_query(
        f"""
        SELECT DATE_TRUNC('{trunc}', time)::date AS bucket,
               event_type,
               COUNT(DISTINCT asset_id) AS n
        FROM asset_chronicle
        WHERE time > NOW() - INTERVAL '{interval}'
          AND event_type IN ('asset_created', 'asset_removed',
                             'asset_retired', 'asset_went_inactive',
                             'asset_reactivated', 'asset_readded')
        GROUP BY bucket, event_type
        ORDER BY bucket ASC, event_type
        """
    ) or []

    if not rows:
        return (
            f"COUNT: 0 lifecycle events in the last {time_range} "
            f"(bucket={bucket})."
        )

    by_bucket: Dict[Any, Dict[str, int]] = {}
    totals: Dict[str, int] = {}
    for row in rows:
        b = row["bucket"]
        et = row["event_type"]
        n = int(row["n"])
        by_bucket.setdefault(b, {})[et] = n
        totals[et] = totals.get(et, 0) + n

    event_cols = sorted(totals, key=lambda e: totals[e], reverse=True)
    grand_total = sum(totals.values())

    lines = [
        f"COUNT: {grand_total} lifecycle events in the last {time_range} "
        f"({len(by_bucket)} {bucket} buckets).",
        "",
    ]
    header = f"{'bucket':12s}" + "".join(f"{e:>22s}" for e in event_cols) + f"{'Total':>8s}"
    lines.append(header)
    lines.append("-" * len(header))
    for b in sorted(by_bucket):
        cells = by_bucket[b]
        cell_strs = "".join(f"{cells.get(e, 0):>22d}" for e in event_cols)
        row_total = sum(cells.values())
        lines.append(f"{str(b):12s}{cell_strs}{row_total:>8d}")
    lines.append("-" * len(header))
    totals_str = "".join(f"{totals[e]:>22d}" for e in event_cols)
    lines.append(f"{'Total':12s}{totals_str}{grand_total:>8d}")

    return "\n".join(lines)
