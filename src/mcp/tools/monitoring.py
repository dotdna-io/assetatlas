# SPDX-License-Identifier: AGPL-3.0-or-later
"""Monitoring tools for the AssetAtlas MCP server.

Tools: get_dashboard, get_alerts, get_pipeline_status, get_integration_status,
       list_tag_rules, get_asset_history, get_data_quality
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from src.api.modules.settings.settings_schema import get_confidence_thresholds
from src.mcp.context import AppContext, get_db
from src.mcp.tools._common import (
    TIME_RANGE_TO_INTERVAL,
    unknown_enum,
    validate_uuid,
)
from src.modules.alerts.repository import AlertsRepository
from src.modules.pipeline.pipeline_service import _split_created_readded

logger = logging.getLogger(__name__)


# Joins a per-integration `last_sync.last_sync_at` column for queries that
# iterate integrations i — started_at of the latest ingestion batch the
# asset-generation pipeline successfully processed, not integrations.last_sync_at.
_LAST_SYNC_LATERAL = """
            LEFT JOIN LATERAL (
                SELECT MAX(ib.started_at) AS last_sync_at
                FROM ingestion_batches ib
                WHERE ib.integration_id = i.id
                  AND ib.asset_generation_status = 'completed'
            ) last_sync ON TRUE
"""


def register_tools(mcp):
    """Register all monitoring tools on the FastMCP server."""

    @mcp.tool()
    def get_dashboard(
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Get a summary of the AssetAtlas dashboard.

        Returns total assets, category breakdown, confidence distribution,
        and data source statistics. No parameters needed.
        """
        sm = ctx.request_context.lifespan_context.settings_manager if ctx else None
        medium, high = get_confidence_thresholds(sm)
        return get_dashboard_impl(get_db(ctx), medium=medium, high=high)

    @mcp.tool()
    def get_alerts(
        severity: Optional[str] = None,
        alert_type: Optional[str] = None,
        status: Optional[str] = None,
        time_range: str = "7d",
        limit: int = 20,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Get alerts with optional filtering.

        Args:
            severity: Filter by severity (critical, high, medium, low)
            alert_type: Filter by type (security, compliance, operational,
                data_quality)
            status: Filter by status (active, acknowledged, resolved). The
                status filter does NOT change the time window.
            time_range: Time window (24h, 7d, 30d, 90d, all). Default: 7d.
                Use 'all' to skip the window and see every alert regardless
                of age.
            limit: Maximum alerts to return per page (default 20)
        """
        return get_alerts_impl(
            get_db(ctx),
            severity=severity, alert_type=alert_type, status=status,
            time_range=time_range, limit=limit,
        )

    @mcp.tool()
    def get_pipeline_status(
        include_history: bool = True,
        limit: int = 5,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Get current pipeline status and recent run history.

        Args:
            include_history: Include recent run history (default true)
            limit: Number of recent runs to show (1-20, default 5)
        """
        return get_pipeline_status_impl(
            get_db(ctx), include_history=include_history, limit=limit,
        )

    @mcp.tool()
    def get_integration_status(
        integration_name: Optional[str] = None,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Get data source health and sync status.

        Shows each integration's connection status, last sync time, asset count,
        and error information.

        Args:
            integration_name: Optional integration name to get details for one source.
                              If omitted, returns all integrations.
        """
        return get_integration_status_impl(
            get_db(ctx), integration_name=integration_name,
        )

    @mcp.tool()
    def list_tag_rules(
        enabled_only: bool = False,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """List tag rules with execution statistics.

        Shows each rule's type, status, match counts, and last execution time.

        Args:
            enabled_only: If true, only show enabled rules (default false)
        """
        return list_tag_rules_impl(get_db(ctx), enabled_only=enabled_only)

    @mcp.tool()
    def get_asset_history(
        asset_id: str,
        time_range: str = "30d",
        limit: int = 50,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Get chronicle change history for an asset.

        Shows a timeline of field changes with old and new values.

        Args:
            asset_id: Asset UUID
            time_range: Time window (24h, 7d, 30d, 90d). Default: 30d
            limit: Maximum events to return (1-200, default 50)
        """
        return get_asset_history_impl(
            get_db(ctx), asset_id=asset_id, time_range=time_range, limit=limit,
        )

    @mcp.tool()
    def get_data_quality(
        category: Optional[str] = None,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Get data quality metrics: confidence distribution, field coverage, stale records.

        Helps identify data gaps, low-confidence assets, and stale records
        that may need attention.

        Args:
            category: Optional category filter (e.g. 'Server', 'Desktop')
        """
        sm = ctx.request_context.lifespan_context.settings_manager if ctx else None
        medium, high = get_confidence_thresholds(sm)
        return get_data_quality_impl(get_db(ctx), category=category, medium=medium, high=high)


def get_dashboard_impl(db, *, medium: float = 0.45, high: float = 0.70) -> str:
    # Total count
    total_result = db.execute_query("SELECT COUNT(*) as total FROM assets")
    total = total_result[0]["total"] if total_result else 0

    # Added today
    today_result = db.execute_query(
        "SELECT COUNT(*) as cnt FROM assets WHERE first_seen::date = CURRENT_DATE"
    )
    added_today = today_result[0]["cnt"] if today_result else 0

    # Category breakdown
    categories = db.execute_query("""
        SELECT
            COALESCE(category, 'Uncategorized') as category,
            COUNT(*) as count,
            ROUND(COUNT(*) * 100.0 / NULLIF(SUM(COUNT(*)) OVER (), 0), 1) as percentage
        FROM assets
        GROUP BY category
        ORDER BY count DESC
    """)

    # Confidence distribution
    confidence = db.execute_query("""
        SELECT level, COUNT(*) as count,
               ROUND(COUNT(*) * 100.0 / NULLIF(SUM(COUNT(*)) OVER (), 0), 1) as percentage
        FROM (
            SELECT CASE
                WHEN confidence >= %(cl_high)s THEN 'high'
                WHEN confidence >= %(cl_med)s THEN 'medium'
                ELSE 'low'
            END as level
            FROM assets
        ) levels
        GROUP BY level
        ORDER BY CASE level WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END
    """, {"cl_high": high, "cl_med": medium})

    sources = db.execute_query(f"""
        SELECT
            i.name,
            i.integration_type,
            i.status,
            COUNT(DISTINCT ap.asset_id) as asset_count,
            ROUND(AVG(a.confidence) * 100) as avg_confidence,
            EXTRACT(EPOCH FROM (NOW() - last_sync.last_sync_at)) / 3600 as hours_since_sync
        FROM integrations i
        {_LAST_SYNC_LATERAL}
        LEFT JOIN asset_providers ap ON ap.data_source = i.name
        LEFT JOIN assets a ON ap.asset_id = a.id
        WHERE i.status = 'enabled'
        GROUP BY i.id, i.name, i.integration_type, i.status, last_sync.last_sync_at
        ORDER BY asset_count DESC
    """)

    # Format output
    lines = [f"Dashboard Summary\n"]
    lines.append(f"  Total Assets: {total:,}")
    lines.append(f"  Added Today: {added_today:,}")

    if categories:
        lines.append(f"\nCategories:")
        for c in categories:
            lines.append(f"  {c['category']:25s} {c['count']:>6,}  ({c['percentage']}%)")

    if confidence:
        lines.append(f"\nConfidence Distribution:")
        label = {
            "high": f"High (>={high:.0%})",
            "medium": f"Medium ({medium:.0%}-{high:.0%})",
            "low": f"Low (<{medium:.0%})",
        }
        for c in confidence:
            lines.append(f"  {label.get(c['level'], c['level']):25s} {c['count']:>6,}  ({c['percentage']}%)")

    if sources:
        lines.append(f"\nData Sources ({len(sources)}):")
        for s in sources:
            sync_str = f"{s['hours_since_sync']:.0f}h ago" if s.get("hours_since_sync") else "never"
            lines.append(
                f"  {s['name']:25s} {s['asset_count']:>6,} assets  "
                f"conf={s['avg_confidence'] or 0:.0f}%  sync={sync_str}"
            )

    return "\n".join(lines)


# Alerts read-path validation sets. Severity/type/status mirror the DB CHECK
# constraints; an unknown value is rejected loudly (never a silent empty set).
_ALERT_SEVERITIES = ("critical", "high", "medium", "low")
_ALERT_TYPES = ("security", "compliance", "operational", "data_quality")
_ALERT_STATUSES = ("active", "acknowledged", "resolved")
_ALERT_TIME_RANGES = ("24h", "7d", "30d", "90d", "all")
_ALERT_BUCKETS = ("hour", "day")
# Raised from the old hard 100 cap; whole-set pulls go via paging.
_ALERTS_PER_PAGE_MAX = 200
# Timeline buckets rendered into the text preview before an overflow note.
_ALERT_TIMELINE_PREVIEW = 30


def _display_alert_source(source: Optional[str]) -> str:
    """Map the raw ``alerts.source`` to the UI label (DB 'user' rules surface as
    'custom', as in AlertsService); other values pass through, None → 'system'.
    Applied to the alert list AND the timeline source split so one response
    never labels the same source two ways."""
    if source == "user":
        return "custom"
    return source or "system"


def get_alerts_impl(
    db,
    *,
    severity: Optional[str] = None,
    alert_type: Optional[str] = None,
    status: Optional[str] = None,
    time_range: str = "7d",
    limit: int = 20,
    search: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    page: int = 1,
    per_page: Optional[int] = None,
    include_summary: bool = False,
    bucket: Optional[str] = None,
    return_data: bool = False,
    repo=None,
):
    """List alerts with filtering, search, paging, summary and a timeline.

    Wraps ``AlertsRepository`` — the SAME read path the /api/alerts UI uses — so
    counts match the product exactly. Enum args (severity/alert_type/status/
    time_range/bucket) are validated LOUDLY: an unknown value returns an error
    string, never a silent empty result. ``status`` filters WITHOUT nullifying
    the time window — only ``time_range='all'`` or a custom ``start``/``end``
    changes the window (this fixes the historical status-override bug).

    Window: presets 24h/7d/30d/90d, ``'all'`` for no window, OR a custom ISO
    ``start``/``end`` (which overrides the preset). ``page``/``per_page`` page
    with a real ``total_items``/``total_pages``. ``include_summary`` adds the
    by_type breakdown; ``bucket`` (hour|day) returns a created-at timeline split
    by severity (and source).

    Returns the text summary; with ``return_data=True`` returns ``(text, data)``
    where ``data`` is a plain dict the chat binding renders into table/timeline
    artifacts (kept out of this module so the MCP server stays Flask-free).
    MCP back-compat: the ``(db, severity, alert_type, status, time_range,
    limit)`` call still works — every new param is optional with a default.
    """
    # --- validate enums loudly (never silently widen the result) ---
    if status:
        # DB statuses are lowercase; tolerate LLM casing ("Active").
        status = status.lower()
        if status not in _ALERT_STATUSES:
            return unknown_enum("status", status, _ALERT_STATUSES)
    if severity:
        severity = severity.lower()
        if severity not in _ALERT_SEVERITIES:
            return unknown_enum("severity", severity, _ALERT_SEVERITIES)
    if alert_type and alert_type not in _ALERT_TYPES:
        return unknown_enum("alert_type", alert_type, _ALERT_TYPES)
    if bucket is not None and bucket not in _ALERT_BUCKETS:
        return unknown_enum("bucket", bucket, _ALERT_BUCKETS)

    # --- resolve the window: a custom start/end overrides the preset ---
    start_dt = end_dt = None
    use_custom = start is not None or end is not None
    if use_custom:
        if start is not None:
            start_dt = _parse_history_dt(start)
            if start_dt is None:
                return (f"Invalid start date {start!r}; use ISO format like "
                        f"'2026-01-01' or '2026-01-01T13:00'.")
        if end is not None:
            end_dt = _parse_history_dt(end)
            if end_dt is None:
                return (f"Invalid end date {end!r}; use ISO format like "
                        f"'2026-02-01' or '2026-02-01T13:00'.")
            # A date-only end means "through that whole day"; extend to end of
            # day else created_at <= midnight drops everything that day (§4).
            if "T" not in end and ":" not in end:
                end_dt = end_dt.replace(hour=23, minute=59, second=59,
                                        microsecond=999999)
        tr = "7d"  # ignored once start/end are set; a valid sentinel for the repo
        window_label = (
            f"{start_dt.date() if start_dt else 'start'} → "
            f"{end_dt.date() if end_dt else 'now'}"
        )
    else:
        if time_range not in _ALERT_TIME_RANGES:
            return unknown_enum("time_range", time_range, _ALERT_TIME_RANGES)
        tr = time_range
        window_label = "all-time" if time_range == "all" else time_range

    # --- paging (the old 100 cap is raised to _ALERTS_PER_PAGE_MAX) ---
    try:
        page = max(1, int(page or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(per_page if per_page is not None else limit)
    except (TypeError, ValueError):
        per_page = 20
    per_page = max(1, min(per_page, _ALERTS_PER_PAGE_MAX))

    repo = repo or AlertsRepository(db)
    filters = dict(time_range=tr, severity=severity, alert_type=alert_type,
                   status=status, search=search, start=start_dt, end=end_dt)

    # The summary is one cheap GROUP BY and its total is the full-window count,
    # so it is fetched unconditionally: the severity breakdown stays in the
    # header without a second round-trip and total_items needs no extra COUNT.
    summary = repo.get_alerts_summary(**filters)
    total_items = int(summary.get("total", 0))
    total_pages = (total_items + per_page - 1) // per_page if per_page else 0

    items = repo.get_alerts(**filters, page=page, per_page=per_page)
    # Mirror the UI's source labelling (DB 'user' rules surface as 'custom').
    for a in items:
        a["source"] = _display_alert_source(a.get("source"))

    timeline = None
    if bucket is not None:
        timeline = repo.get_alerts_timeline(**filters, bucket=bucket,
                                            split_by_source=True)

    data: Dict[str, Any] = {
        "items": items,
        "total_items": total_items,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "window_label": window_label,
        "by_severity": summary.get("by_severity", {}),
        "summary": summary if include_summary else None,
        "timeline": timeline,
        "bucket": bucket,
    }

    text = _format_alerts_text(data)
    if return_data:
        return text, data
    return text


def _format_alerts_text(data: Dict[str, Any]) -> str:
    """Render the alerts list/summary/timeline into the LLM-facing text."""
    items = data["items"]
    total = data["total_items"]
    by_sev = data.get("by_severity") or {}
    window = data["window_label"]
    page, per_page, pages = data["page"], data["per_page"], data["total_pages"]

    header = (
        f"Alerts ({window}): {total} total — "
        f"critical: {by_sev.get('critical', 0)}, high: {by_sev.get('high', 0)}, "
        f"medium: {by_sev.get('medium', 0)}, low: {by_sev.get('low', 0)}"
    )
    if total == 0 and window != "all-time":
        header += ". (Try time_range='all' or a wider window.)"
    lines = [header]
    if total and items:
        shown_from = (page - 1) * per_page + 1
        shown_to = (page - 1) * per_page + len(items)
        lines.append(
            f"Page {page}/{max(pages, 1)} (showing {shown_from}-{shown_to} "
            f"of {total})."
        )
    lines.append("")

    summary = data.get("summary")
    if summary:
        by_type = summary.get("by_type") or {}
        lines.append(
            "By type — " + ", ".join(f"{k}: {v}" for k, v in by_type.items())
        )
        lines.append("")

    if not items:
        if total:
            # The window has matches, just none on the requested page.
            lines.append(
                f"No alerts on page {page} of {max(pages, 1)} "
                f"({total} total — lower `page`).")
        else:
            lines.append("No alerts match the given criteria.")
        return "\n".join(lines)

    for a in items:
        sev = (a.get("severity") or "").upper()
        # Surface the UUID — get_alert_detail needs it. Without it the model
        # passes the type slug and hits "Invalid alert ID".
        lines.append(f"[{sev}] {a.get('title')} (id={a.get('id')})")
        lines.append(
            f"  Status: {a.get('status')}  |  Type: {a.get('type')}  |  "
            f"Assets: {a.get('asset_count', 0)}  |  "
            f"Source: {a.get('source') or 'system'}"
        )
        lines.append(f"  Created: {a.get('created_at')}")
        lines.append("")

    timeline = data.get("timeline")
    if timeline:
        lines.append(f"Timeline ({data.get('bucket')} buckets):")
        for b in timeline[:_ALERT_TIMELINE_PREVIEW]:
            bs = b.get("by_severity", {})
            line = (
                f"  {b.get('date')}: {b.get('total', 0)} "
                f"(crit {bs.get('critical', 0)}, high {bs.get('high', 0)}, "
                f"med {bs.get('medium', 0)}, low {bs.get('low', 0)})"
            )
            # Surface the system/custom/prometheus split when requested,
            # mapping 'user'→'custom' to match the list/detail labelling.
            src = b.get("by_source")
            if src:
                line += "  src[" + ", ".join(
                    f"{_display_alert_source(k)}:{v}"
                    for k, v in src.items()) + "]"
            lines.append(line)
        if len(timeline) > _ALERT_TIMELINE_PREVIEW:
            hidden = len(timeline) - _ALERT_TIMELINE_PREVIEW
            lines.append(f"  ... and {hidden} more buckets.")
        lines.append("")

    lines.append(
        "Drill in: call get_alert_detail(alert_id) for an alert's affected "
        "assets (with their ids), threshold and reason."
    )
    return "\n".join(lines)


def get_pipeline_status_impl(
    db,
    *,
    include_history: bool = True,
    limit: int = 5,
) -> str:
    limit = max(1, min(limit, 20))

    # Check for active run
    active = db.execute_query("""
        SELECT id, status, current_stage, started_at, triggered_by
        FROM pipeline_runs
        WHERE status IN ('pending', 'running')
        ORDER BY created_at DESC LIMIT 1
    """)

    lines = ["Pipeline Status\n"]

    if active:
        run = active[0]
        elapsed = ""
        if run.get("started_at"):
            from datetime import datetime, timezone
            delta = (datetime.now(timezone.utc) - run["started_at"]).total_seconds()
            elapsed = f" ({delta:.0f}s elapsed)"
        lines.append(f"  Status: {run['status'].upper()}{elapsed}")
        lines.append(f"  Stage: {run.get('current_stage') or '(initializing)'}")
        lines.append(f"  Run ID: {run['id']}")
        lines.append(f"  Triggered by: {run.get('triggered_by', '?')}")
    else:
        lines.append("  Status: IDLE")
        # Show last completed run
        last = db.execute_query("""
            SELECT id, status, completed_at, duration_seconds, triggered_by,
                   stats->'total_assets' as total_assets,
                   stats->'total_assets_created' as assets_created,
                   stats->'chronicle_events' as chronicle_events
            FROM pipeline_runs
            ORDER BY created_at DESC LIMIT 1
        """)
        if last:
            run = last[0]
            dur = f"{float(run['duration_seconds']):.1f}s" if run.get("duration_seconds") else "?"
            lines.append(f"  Last run: {run['status']} ({dur})")
            lines.append(f"  Completed: {run.get('completed_at')}")
            if run.get("total_assets"):
                # New/Returned split, consistent with the Run History table and
                # the list_pipeline_runs detail tool (issue #796).
                created, readded = _split_created_readded({
                    "chronicle_events": run.get("chronicle_events"),
                    "total_assets_created": run.get("assets_created"),
                })
                lines.append(
                    f"  Assets: {run['total_assets']} total, "
                    f"{created} created, {readded} returned"
                )

    if include_history:
        runs = db.execute_query("""
            SELECT id, status, triggered_by, started_at, completed_at,
                   duration_seconds, error_message,
                   stats->'total_assets' as total_assets,
                   stats->'batches_processed' as batches
            FROM pipeline_runs
            ORDER BY created_at DESC
            LIMIT %s
        """, (limit,))

        if runs:
            lines.append(f"\nRecent Runs ({len(runs)}):")
            for r in runs:
                dur = f"{float(r['duration_seconds']):.0f}s" if r.get("duration_seconds") else "-"
                assets = r.get("total_assets", "-")
                status_icon = {"completed": "ok", "failed": "FAIL", "running": "..."}.get(r["status"], r["status"])
                lines.append(f"  [{status_icon:4s}] {r.get('started_at') or r.get('completed_at')}  {dur:>6s}  assets={assets}  by={r.get('triggered_by', '?')}")
                if r.get("error_message"):
                    lines.append(f"         Error: {r['error_message'][:100]}")

    return "\n".join(lines)


def get_integration_status_impl(
    db,
    *,
    integration_name: Optional[str] = None,
) -> str:
    conditions = ["1=1"]
    params: Dict[str, Any] = {}
    if integration_name:
        conditions.append("i.name = %(name)s")
        params["name"] = integration_name

    where = " AND ".join(conditions)

    integrations = db.execute_query(f"""
        SELECT
            i.id, i.name, i.integration_type, i.status,
            i.last_sync_status, i.last_error,
            last_sync.last_sync_at,
            i.last_connection_test_status,
            i.last_connection_test_message,
            ig.name as group_name,
            COUNT(DISTINCT ap.asset_id) as asset_count,
            EXTRACT(EPOCH FROM (NOW() - last_sync.last_sync_at)) / 3600 as hours_since_sync
        FROM integrations i
        {_LAST_SYNC_LATERAL}
        LEFT JOIN integration_groups ig ON i.group_id = ig.id
        LEFT JOIN asset_providers ap ON ap.data_source = i.name
        WHERE {where}
        GROUP BY i.id, i.name, i.integration_type, i.status, i.last_sync_status,
                 i.last_error, last_sync.last_sync_at,
                 i.last_connection_test_status, i.last_connection_test_message,
                 ig.name
        ORDER BY i.status, i.name
    """, params)

    if not integrations:
        if integration_name:
            return f"Integration not found: {integration_name}"
        return "No integrations configured."

    # Summarize the failing/disabled set up front so the LLM doesn't have to
    # scan the full list to answer "what's broken". Without this, the model
    # tends to quote `Connection: success` (a stale manual test-connection
    # flag) and miss that the actual last sync failed.
    failing = [i for i in integrations
               if (i.get("status") or "").lower() != "enabled"
               or (i.get("last_sync_status") or "").lower() == "failed"]
    # A truncated collection (#1334) is not a failure — records were stored —
    # but it must still be called out, or "are my integrations healthy?" gets
    # a misleading all-clear.
    partial = [i for i in integrations
               if i not in failing
               and (i.get("last_sync_status") or "").lower() == "partial"]
    healthy = [i for i in integrations if i not in failing and i not in partial]

    header = f"Integrations ({len(integrations)} total: "
    header += f"{len(healthy)} healthy, {len(failing)} failing/disabled"
    if partial:
        header += f", {len(partial)} partial"
    header += ")"
    lines = [header, ""]

    def _bucket(i: Dict[str, Any]) -> str:
        st = (i.get("status") or "").lower()
        sync_st = (i.get("last_sync_status") or "").lower()
        if st == "disabled":
            return "DISABLED"
        if st == "error" or sync_st == "failed":
            return "FAILING"
        if sync_st == "partial":
            return "PARTIAL"
        if st == "enabled" and sync_st in ("", "success"):
            return "HEALTHY"
        return st.upper() or "UNKNOWN"

    for i in integrations:
        bucket = _bucket(i)
        group = f" ({i['group_name']})" if i.get("group_name") else ""
        lines.append(f"[{bucket}] {i['name']}{group}  ({i['integration_type']})")
        lines.append(f"     id: {i['id']}")
        lines.append(f"     Assets: {i['asset_count']:,}")

        if i.get("hours_since_sync") is not None:
            h = i["hours_since_sync"]
            if h < 1:
                sync_str = f"{h * 60:.0f} minutes ago"
            elif h < 24:
                sync_str = f"{h:.1f} hours ago"
            else:
                sync_str = f"{h / 24:.1f} days ago"
            lines.append(f"     Last Sync: {sync_str}")
        else:
            lines.append(f"     Last Sync: never")

        if i.get("last_sync_status"):
            lines.append(f"     Last Sync Status: {i['last_sync_status']}")
            if (i.get("last_sync_status") or "").lower() in ("failed", "partial") and i.get("last_error"):
                lines.append(f"     Last Error: {str(i['last_error'])[:200]}")

        if i.get("last_connection_test_status"):
            lines.append(f"     Connection Test: {i['last_connection_test_status']}")
            if i.get("last_connection_test_message") and i["last_connection_test_status"] != "success":
                lines.append(f"     Connection Error: {i['last_connection_test_message'][:100]}")

        lines.append("")

    return "\n".join(lines)


def list_tag_rules_impl(db, *, enabled_only: bool = False) -> str:
    conditions = []
    if enabled_only:
        conditions.append("enabled = true")
    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    rules = db.execute_query(f"""
        SELECT id, name, type, enabled, description,
               execution_count,
               last_executed_at,
               last_execution_duration_ms,
               last_execution_assets_matched,
               last_execution_assets_passed,
               last_execution_assets_failed
        FROM tag_rules
        {where}
        ORDER BY type, name
    """)

    if not rules:
        return "No tag rules found."

    enabled_count = sum(1 for r in rules if r.get("enabled"))
    lines = [f"Tag Rules: {len(rules)} total, {enabled_count} enabled\n"]

    for r in rules:
        status = "ON" if r.get("enabled") else "OFF"
        rtype = (r.get("type") or "").capitalize()
        lines.append(f"[{status}] {r['name']}  ({rtype})")

        if r.get("description"):
            lines.append(f"     {r['description'][:80]}")

        stats_parts = []
        if r.get("execution_count"):
            stats_parts.append(f"runs: {r['execution_count']}")
        if r.get("last_execution_assets_matched") is not None:
            stats_parts.append(f"matched: {r['last_execution_assets_matched']}")
        if r.get("last_execution_assets_passed") is not None:
            stats_parts.append(f"passed: {r['last_execution_assets_passed']}")
        if r.get("last_execution_assets_failed") is not None:
            stats_parts.append(f"failed: {r['last_execution_assets_failed']}")
        if r.get("last_execution_duration_ms"):
            stats_parts.append(f"duration: {r['last_execution_duration_ms']}ms")

        if stats_parts:
            lines.append(f"     Stats: {', '.join(stats_parts)}")
        if r.get("last_executed_at"):
            lines.append(f"     Last run: {r['last_executed_at']}")

        lines.append("")

    return "\n".join(lines)


# Sort keys the asset-history timeline accepts, mirroring HistoryTable.tsx's
# clickable columns (Date / Event / Attribute / Source).
_HISTORY_SORTS = ("date", "type", "attribute", "source")

# Cap on rendered timeline rows (the full set still drives the counts/overflow
# note). Keeps the LLM context lean on chatty assets without hiding the total.
_HISTORY_MAX_SHOWN = 100


def _parse_history_dt(value: Any) -> Optional[datetime]:
    """Parse a user date/datetime string to an aware UTC datetime, or None.

    Accepts ISO dates ('2026-01-01') and datetimes ('2026-01-01T13:00',
    optionally with a trailing 'Z'). Naive values are assumed UTC so they
    compare against the timestamptz columns. Returns None on anything
    unparseable so the caller can fail loudly (§4) instead of guessing a window.
    """
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


def _flatten_history_value(val: Any) -> Optional[str]:
    """Flatten an array/scalar chronicle value to a display string (or None).

    Chronicle old/new values arrive as text[] (e.g. ``['alice']``); the UI
    flattens them to ``'alice'`` / ``'a, b'`` so the timeline shows the value,
    not its Python list repr. Mirrors AssetHistoryService._flatten_value."""
    if val is None:
        return None
    if isinstance(val, list):
        parts = [str(v) for v in val if v is not None]
        return ", ".join(parts) if parts else None
    return str(val)


def _history_ts_key(ts: Any) -> str:
    """A lexicographically-sortable key for a timestamp (ISO for datetimes).

    ISO strings sort chronologically, and chronicle ``time`` / alert
    ``created_at`` are both timestamptz → aware datetimes with the same offset,
    so a single string key orders the merged stream correctly."""
    if isinstance(ts, datetime):
        return ts.isoformat()
    return str(ts) if ts is not None else ""


def _attr_history_event(
    ts: Any, etype: str, attr: Optional[str],
    old: Optional[str], new: Optional[str], source: Optional[str],
) -> Dict[str, Any]:
    subject = attr or etype or "(event)"
    return {
        "kind": "attribute",
        "ts": ts,
        "ts_key": _history_ts_key(ts),
        "etype": etype,
        "attr": attr,
        "old": old,
        "new": new,
        "source": source or "",
        "subject": subject,
        "alert_type": "",
        "_haystack": " ".join(
            str(x).lower() for x in
            (attr or "", old or "", new or "", source or "", etype or "")
        ),
    }


def _build_history_events(
    chronicle: List[Dict[str, Any]], alert_rows: List[Dict[str, Any]],
    window_start: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Merge chronicle change rows + alert rows into one event list.

    Chronicle rows explode into one event per changed attribute (matching the
    UI's per-attribute timeline); alert rows become ``kind='alert'`` events.
    Both carry a normalized ``ts_key`` for sorting and a ``_haystack`` for
    search. This is the interleave the old tool never did.

    An alert whose ``created_at`` precedes ``window_start`` is only present
    because it is still open (the query OR-includes unresolved alerts); it is
    flagged ``out_of_window`` so the rendered line can explain why an alert
    older than the asked-for range appears."""
    events: List[Dict[str, Any]] = []

    for evt in chronicle:
        etype = evt.get("event_type", "") or ""
        ts = evt.get("time")
        attr_changes = evt.get("attribute_changes")
        changed_attrs = evt.get("changed_attributes")

        if isinstance(attr_changes, dict) and attr_changes:
            for attr, change in attr_changes.items():
                if isinstance(change, dict):
                    old = _flatten_history_value(change.get("old"))
                    new = _flatten_history_value(change.get("new"))
                    source = change.get("source")
                else:
                    old = new = source = None
                events.append(
                    _attr_history_event(ts, etype, attr, old, new, source)
                )
        elif isinstance(changed_attrs, list) and changed_attrs:
            for attr in changed_attrs:
                events.append(
                    _attr_history_event(ts, etype, attr, None, None, None)
                )
        else:
            events.append(
                _attr_history_event(ts, etype, None, None, None, None)
            )

    for ar in alert_rows:
        ts = ar.get("created_at")
        alert_type = ar.get("type") or "alert"
        severity = ar.get("severity") or ""
        title = ar.get("title") or ""
        status = ar.get("status") or ""
        out_of_window = bool(
            window_start is not None
            and isinstance(ts, datetime)
            and ts < window_start
        )
        events.append({
            "kind": "alert",
            "ts": ts,
            "ts_key": _history_ts_key(ts),
            "alert_type": alert_type,
            "severity": severity,
            "title": title,
            "status": status,
            "out_of_window": out_of_window,
            "alert_id": str(ar.get("id") or ""),
            "subject": title,
            "source": "",
            "_haystack": " ".join(
                str(x).lower() for x in
                (alert_type, severity, title, ar.get("description") or "",
                 status)
            ),
        })

    return events


def _sort_history_events(events: List[Dict[str, Any]], sort: str) -> None:
    """Sort the merged stream in place. Always newest-first first, so a stable
    secondary sort on the chosen key keeps recent events leading within ties."""
    events.sort(key=lambda e: e["ts_key"], reverse=True)
    if sort == "type":
        events.sort(
            key=lambda e: (e["kind"], e.get("alert_type", "") or e.get("etype", ""))
        )
    elif sort == "attribute":
        events.sort(key=lambda e: (e.get("subject") or "").lower())
    elif sort == "source":
        events.sort(key=lambda e: (e.get("source") or "").lower())
    # sort == "date": the newest-first order above is the result.


def _format_history_line(e: Dict[str, Any]) -> str:
    ts_str = _history_ts_key(e["ts"])[:19].replace("T", " ") if e["ts"] else "?"
    if e["kind"] == "alert":
        sev = f" {e['severity']}" if e["severity"] else ""
        status = f" ({e['status']})" if e["status"] else ""
        # An open alert that predates the window is still surfaced; say so, so
        # the user sees why its timestamp is outside the asked-for range.
        flag = " [still open · created before window]" if e.get(
            "out_of_window") else ""
        return (f"  {ts_str}  [ALERT{sev}] {e['alert_type']}: "
                f"{e['title']}{status}{flag}")
    attr = e["attr"] or e["etype"] or "(event)"
    src = f"  [{e['source']}]" if e["source"] else ""
    if e["old"] is not None and e["new"] is not None:
        return f"  {ts_str}  {attr}: {e['old'][:60]} → {e['new'][:60]}{src}"
    if e["new"] is not None:
        return f"  {ts_str}  {attr}: (added) {e['new'][:60]}{src}"
    if e["old"] is not None:
        return f"  {ts_str}  {attr}: (removed) {e['old'][:60]}{src}"
    return f"  {ts_str}  [{e['etype']}] {attr}{src}"


def get_asset_history_impl(
    db,
    *,
    asset_id: str,
    time_range: str = "30d",
    limit: int = 50,
    start: Optional[str] = None,
    end: Optional[str] = None,
    sort: str = "date",
    search: Optional[str] = None,
) -> str:
    """Asset history timeline — chronicle field changes INTERLEAVED with alert
    events, matching the UI (HistoryTable.tsx / EventTimeline.tsx).

    ``time_range`` is a preset (24h/7d/30d/90d); pass ``start`` and/or ``end``
    (ISO) for an arbitrary window, which overrides the preset. ``sort`` is one
    of date/type/attribute/source; ``search`` is a case-insensitive substring
    over the merged stream (attribute names/values/source AND alert titles).

    MCP back-compat: the older ``(asset_id, time_range, limit)`` call still
    works — the new params are optional with defaults.
    """
    err = validate_uuid(asset_id, "asset")
    if err:
        return err

    limit = max(1, min(limit, 200))

    if sort not in _HISTORY_SORTS:
        return unknown_enum("sort", sort, _HISTORY_SORTS)

    # Resolve the window. A custom start/end (either bound) defines an arbitrary
    # range and OVERRIDES the time_range preset; unparseable input fails loudly
    # rather than silently answering a default window (a wrong window is worse
    # than an error — §4).
    end_time = datetime.now(timezone.utc)
    if start is not None or end is not None:
        if start is not None:
            start_time = _parse_history_dt(start)
            if start_time is None:
                return (
                    f"Invalid start date {start!r}; use ISO format like "
                    f"'2026-01-01' or '2026-01-01T13:00'."
                )
        else:
            start_time = datetime(1970, 1, 1, tzinfo=timezone.utc)
        if end is not None:
            parsed_end = _parse_history_dt(end)
            if parsed_end is None:
                return (
                    f"Invalid end date {end!r}; use ISO format like "
                    f"'2026-02-01' or '2026-02-01T13:00'."
                )
            # A date-only end ('2026-02-01') means "through that whole day";
            # extend it to end-of-day, else `created_at <= midnight` silently
            # drops everything that happened ON the end date (off-by-one).
            if "T" not in end and ":" not in end:
                parsed_end = parsed_end.replace(
                    hour=23, minute=59, second=59, microsecond=999999)
            end_time = parsed_end
        window_label = f"{start_time.date()} → {end_time.date()}"
    else:
        # Share the enum set with the other tools; derive a timedelta from the
        # same key so a new value in one place propagates.
        unit_map = {"h": "hours", "d": "days"}
        if time_range not in TIME_RANGE_TO_INTERVAL:
            return (
                f"Unknown time_range {time_range!r}. "
                f"Valid values: {sorted(TIME_RANGE_TO_INTERVAL)!r}."
            )
        n = int(time_range[:-1])
        start_time = end_time - timedelta(**{unit_map[time_range[-1]]: n})
        window_label = time_range

    # Verify asset exists and get metadata.
    asset = db.execute_query(
        "SELECT id, hostname, category, ip FROM assets WHERE id = %(aid)s",
        {"aid": asset_id},
    )
    if not asset:
        return f"Asset not found: {asset_id}"
    hostname = asset[0].get("hostname")
    if isinstance(hostname, list):
        hostname = hostname[0] if hostname else "(no hostname)"

    # Chronicle field-change events in the window.
    chronicle = db.execute_query("""
        SELECT id, time, event_type, changed_attributes, attribute_changes
        FROM asset_chronicle
        WHERE asset_id = %(aid)s::uuid
          AND time >= %(start)s AND time <= %(end)s
        ORDER BY time DESC
        LIMIT %(limit)s
    """, {"aid": asset_id, "start": start_time, "end": end_time,
          "limit": limit}) or []

    # Alert events on this asset in the window. The UI interleaves these;
    # querying only the chronicle silently under-reports what happened. Linkage
    # is the alerts.asset_ids UUID[] array (no join table) — same predicate
    # list_alerts_for_asset_impl uses.
    #
    # A still-OPEN alert (status <> 'resolved' — the product's "active or
    # acknowledged" set; see alerts.repository.get_active_alert_for_rule) is
    # present-state, not history: the UI shows it regardless of when it fired,
    # so windowing it like a chronicle change would silently drop an unresolved
    # alert created before the window (e.g. a long-standing "stale assets"
    # alert over a 30d view). OR it in regardless of created_at. RESOLVED
    # alerts stay windowed — once resolved an alert is a historical event like
    # a chronicle change.
    alert_rows = db.execute_query("""
        SELECT id, type, severity, title, description, status,
               created_at, resolved_at
        FROM alerts
        WHERE %(aid)s::uuid = ANY(asset_ids)
          AND ((created_at >= %(start)s AND created_at <= %(end)s)
               OR status <> 'resolved')
        ORDER BY created_at DESC
        LIMIT %(limit)s
    """, {"aid": asset_id, "start": start_time, "end": end_time,
          "limit": limit}) or []

    events = _build_history_events(chronicle, alert_rows,
                                   window_start=start_time)

    if search:
        needle = search.lower()
        events = [e for e in events if needle in e["_haystack"]]

    n_attr = sum(1 for e in events if e["kind"] == "attribute")
    n_alert = sum(1 for e in events if e["kind"] == "alert")

    _sort_history_events(events, sort)

    lines = [f"History for {hostname} ({asset_id[:8]}...) — {window_label}\n"]

    if not events:
        suffix = f" matching {search!r}" if search else ""
        lines.append(f"No changes or alerts recorded in this window{suffix}.")
        return "\n".join(lines)

    lines.append(
        f"Events: {n_attr} attribute change(s) + {n_alert} alert(s) "
        f"({len(events)} total)\n"
    )

    for e in events[:_HISTORY_MAX_SHOWN]:
        lines.append(_format_history_line(e))

    if len(events) > _HISTORY_MAX_SHOWN:
        hidden = len(events) - _HISTORY_MAX_SHOWN
        lines.append(
            f"\n  [{hidden} more not shown — narrow with a tighter "
            f"time_range/start-end, a search term, or a lower limit]"
        )

    return "\n".join(lines)


def get_data_quality_impl(
    db, *, category: Optional[str] = None, medium: float = 0.45, high: float = 0.70
) -> str:
    cat_where = ""
    params: Dict[str, Any] = {}
    if category:
        cat_where = "WHERE category = %(category)s"
        params["category"] = category

    # Total assets in scope
    total_result = db.execute_query(
        f"SELECT COUNT(*) as total FROM assets {cat_where}", params
    )
    total = total_result[0]["total"] if total_result else 0

    if total == 0:
        return f"No assets found{' in category ' + category if category else ''}."

    # Confidence distribution
    conf_params = dict(params, cl_high=high, cl_med=medium)
    confidence = db.execute_query(f"""
        SELECT level, COUNT(*) as count,
               ROUND(AVG(avg_conf) * 100, 1) as avg_pct
        FROM (
            SELECT CASE
                WHEN confidence >= %(cl_high)s THEN 'high'
                WHEN confidence >= %(cl_med)s THEN 'medium'
                ELSE 'low'
            END as level, confidence as avg_conf
            FROM assets
            {cat_where}
        ) levels
        GROUP BY level
        ORDER BY CASE level
            WHEN 'high' THEN 1
            WHEN 'medium' THEN 2
            ELSE 3 END
    """, conf_params)

    # Field coverage (% of assets with non-null values)
    coverage = db.execute_query(f"""
        SELECT
            COUNT(*) FILTER (WHERE hostname IS NOT NULL AND hostname != '{{}}') as has_hostname,
            COUNT(*) FILTER (WHERE ip IS NOT NULL AND ip != '{{}}') as has_ip,
            COUNT(*) FILTER (WHERE mac IS NOT NULL AND mac != '{{}}') as has_mac,
            COUNT(*) FILTER (WHERE sn IS NOT NULL AND sn != '{{}}') as has_serial,
            COUNT(*) FILTER (WHERE os_normalized IS NOT NULL AND os_normalized != '') as has_os,
            COUNT(*) FILTER (WHERE manufacturer IS NOT NULL AND manufacturer != '{{}}') as has_manufacturer,
            COUNT(*) FILTER (WHERE owner IS NOT NULL AND owner != '{{}}') as has_owner,
            COUNT(*) FILTER (WHERE category IS NOT NULL) as has_category,
            COUNT(*) as total
        FROM assets
        {cat_where}
    """, params)

    # Stale records (not seen in 30+ days)
    stale = db.execute_query(f"""
        SELECT
            COUNT(*) FILTER (WHERE last_seen < NOW() - interval '30 days') as stale_30d,
            COUNT(*) FILTER (WHERE last_seen < NOW() - interval '90 days') as stale_90d,
            COUNT(*) FILTER (WHERE last_seen IS NULL) as never_seen
        FROM assets
        {cat_where}
    """, params)

    # Per-category confidence if no category filter
    cat_conf = None
    if not category:
        cat_conf = db.execute_query("""
            SELECT category, COUNT(*) as count,
                   ROUND(AVG(confidence) * 100, 1) as avg_confidence
            FROM assets
            WHERE category IS NOT NULL
            GROUP BY category
            ORDER BY avg_confidence ASC
        """)

    # Format
    scope = f" ({category})" if category else ""
    lines = [f"Data Quality Report{scope}: {total:,} assets\n"]

    # Confidence
    lines.append("Confidence Distribution:")
    label = {
        "high": f"High (>={high:.0%})",
        "medium": f"Medium ({medium:.0%}-{high:.0%})",
        "low": f"Low (<{medium:.0%})",
    }
    for c in (confidence or []):
        pct = c["count"] / total * 100
        lines.append(f"  {label.get(c['level'], c['level']):25s} {c['count']:>6,}  ({pct:5.1f}%)  avg={c['avg_pct']}%")

    # Field coverage
    if coverage:
        cov = coverage[0]
        t = cov["total"]
        lines.append(f"\nField Coverage:")
        for field, key in [
            ("Hostname", "has_hostname"), ("IP Address", "has_ip"),
            ("MAC Address", "has_mac"), ("Serial Number", "has_serial"),
            ("OS", "has_os"), ("Manufacturer", "has_manufacturer"),
            ("Owner", "has_owner"), ("Category", "has_category"),
        ]:
            val = cov.get(key, 0)
            pct = val / t * 100 if t > 0 else 0
            bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
            lines.append(f"  {field:20s} {bar} {pct:5.1f}%  ({val:,}/{t:,})")

    # Stale records
    if stale:
        s = stale[0]
        lines.append(f"\nStale Records:")
        lines.append(f"  Not seen in 30+ days: {s.get('stale_30d', 0):,}")
        lines.append(f"  Not seen in 90+ days: {s.get('stale_90d', 0):,}")
        lines.append(f"  Never seen:           {s.get('never_seen', 0):,}")

    # Category confidence ranking
    if cat_conf:
        lines.append(f"\nConfidence by Category (lowest first):")
        for c in cat_conf[:10]:
            lines.append(f"  {c['category']:25s} avg={c['avg_confidence']}%  ({c['count']:,} assets)")

    return "\n".join(lines)
