# SPDX-License-Identifier: AGPL-3.0-or-later
"""Alert detail tools for the AssetAtlas MCP server.

Tools:
- get_alert_detail: full detail for one alert, including affected assets.
- get_alert_history: per-run firing history for one alert (the Alerts
  drilldown timechart) — asset_count / severity across recent pipeline runs.
- list_alerts_for_asset: reverse pivot. Given an asset_id, list the alerts
  that reference it. Without asset_id, list the top-N noisiest assets by
  active-alert count. Answers "what alerts are on this host?" / "which
  assets have the most alerts?" — get_alerts is alert-first, not
  asset-first, so these questions are awkward today.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from src.mcp.context import AppContext
from src.mcp.tools._common import (
    format_hostname,
    overflow_note,
    unknown_enum,
    validate_uuid,
)
from src.modules.alerts.service import AlertsService


def _get_db(ctx: Context[ServerSession, AppContext]):
    return ctx.request_context.lifespan_context.db


def register_tools(mcp):
    """Register alert-detail tools on the FastMCP server."""

    @mcp.tool()
    def get_alert_detail(
        alert_id: str,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Return full detail for a single alert by ID, including affected assets.

        Args:
            alert_id: Alert UUID string.
        """
        return get_alert_detail_impl(_get_db(ctx), alert_id=alert_id)

    @mcp.tool()
    def list_alerts_for_asset(
        asset_id: Optional[str] = None,
        status: str = "active",
        limit: int = 10,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Asset-first view of alerts (reverse of get_alerts).

        With asset_id: list the alerts that currently reference this asset.

        Without asset_id: return the top-N assets ranked by number of
        matching alerts, so the user can ask "which hosts have the most
        alerts?" without paging through every alert.

        Args:
            asset_id: Optional asset UUID. Omit for the top-N roll-up.
            status: Alert status to filter by: 'active', 'acknowledged',
                'resolved', or 'any'. Defaults to 'active'.
            limit: Max rows in the top-N roll-up (1-50, default 10).
                Ignored when asset_id is provided.
        """
        return list_alerts_for_asset_impl(
            _get_db(ctx),
            asset_id=asset_id,
            status=status,
            limit=limit,
        )

    @mcp.tool()
    def get_alert_history(
        alert_id: str,
        days: int = 90,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Per-run firing history for one alert (the Alerts drilldown
        timechart): how its matched asset_count / severity moved across recent
        pipeline runs.

        Args:
            alert_id: Alert UUID string.
            days: Look-back window in days (1-730, default 90).
        """
        alerts_service = ctx.request_context.lifespan_context.alerts_service
        return get_alert_history_impl(
            alerts_service, alert_id=alert_id, days=days)


def get_alert_detail_impl(
    db, *, alert_id: str, offset: int = 0, limit: int = 50,
    return_data: bool = False,
):
    """Full detail for one alert: reason, source, threshold + current value,
    assets-by-category, contributing data sources, timestamps, and the affected
    assets (hostname/ip/model + id) paged via offset/limit.

    Returns the text; with return_data=True returns (text, data) for the chat
    binding's affected-assets table. MCP back-compat: the (db, alert_id) call
    still works — offset/limit/return_data are optional with defaults.
    """
    err = validate_uuid(alert_id, "alert")
    if err:
        return err

    rows = db.execute_query(
        """
        SELECT id, title, severity, type, status, description,
               source, threshold, assets_by_category, data_sources,
               created_at, updated_at, resolved_at,
               -- psycopg2 returns bare uuid[] as an unparsed string literal
               -- ("{uuid1,uuid2}"). Casting to text[] here makes psycopg2
               -- hand back a real Python list. Without this, the Python code
               -- iterates the string character-by-character below and the
               -- second query asks PG to cast "{", "2", "5", … to uuid —
               -- which fails with InvalidTextRepresentation.
               asset_ids::text[] AS asset_ids,
               asset_count
        FROM alerts
        WHERE id = %(id)s::uuid
        """,
        {"id": alert_id},
    )
    alert: Optional[Dict[str, Any]] = rows[0] if rows else None
    if not alert:
        return f"Alert {alert_id} not found."

    # The UI labels DB 'user' rules as 'custom'; mirror that here.
    if alert.get("source") == "user":
        alert["source"] = "custom"
    # reason is computed (no column) — reuse the canonical AlertsService logic
    # so chat and the UI explain a firing identically.
    reason = AlertsService._compute_reason(alert)
    threshold = alert.get("threshold")
    threshold = threshold if isinstance(threshold, dict) else {}
    current_value = threshold.get("current_value")

    try:
        limit = max(1, min(int(limit or 50), 500))
    except (TypeError, ValueError):
        limit = 50
    try:
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        offset = 0

    asset_ids = alert.get("asset_ids") or []
    total_assets = len(asset_ids)
    assets: List[Dict[str, Any]] = []
    if asset_ids:
        assets = db.execute_query(
            """
            SELECT id, hostname[1] AS hostname, category,
                   ip[1]::text AS ip, model[1] AS model
            FROM assets
            WHERE id = ANY(%(ids)s::uuid[])
            ORDER BY hostname NULLS LAST
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {"ids": [str(x) for x in asset_ids],
             "limit": limit, "offset": offset},
        ) or []
    # Defensive cap: a mock/driver that ignores LIMIT still pages cleanly.
    shown = assets[:limit]

    lines = [
        f"# {alert['title']}",
        f"- severity: {alert['severity']}",
        f"- type: {alert['type']}",
        f"- status: {alert['status']}",
        f"- source: {alert.get('source') or 'system'}",
        f"- reason: {reason}",
    ]
    if threshold.get("value") is not None or current_value is not None:
        unit_sfx = "%" if threshold.get("unit") == "percent" else ""
        parts = []
        if current_value is not None:
            parts.append(f"current {current_value}{unit_sfx}")
        if threshold.get("value") is not None:
            cond = threshold.get("condition")
            tail = f" ({cond})" if cond else ""
            parts.append(f"threshold {threshold['value']}{unit_sfx}{tail}")
        if parts:
            lines.append(f"- threshold: {', '.join(parts)}")
    lines.append(f"- created: {alert['created_at']}")
    if alert.get("updated_at"):
        lines.append(f"- updated: {alert['updated_at']}")
    if alert.get("resolved_at"):
        lines.append(f"- resolved: {alert['resolved_at']}")

    data_sources = alert.get("data_sources") or []
    if data_sources:
        lines.append(
            f"- data sources: {', '.join(str(s) for s in data_sources)}")
    abc = alert.get("assets_by_category")
    if isinstance(abc, dict) and abc:
        lines.append(
            "- assets by category: "
            + ", ".join(f"{k}: {v}" for k, v in abc.items()))

    if alert.get("description"):
        lines.append(f"\n**Description:** {alert['description']}")

    if total_assets and not shown:
        # The alert has assets but the requested page is past the last one —
        # say so, else the header reads "N total" with no rows and no hint.
        lines.append(f"\n## Affected assets ({total_assets} total)")
        lines.append(
            f"(offset {offset} is past the last asset — lower offset.)")
    elif total_assets:
        end = offset + len(shown)
        lines.append(
            f"\n## Affected assets ({total_assets} total, "
            f"showing {offset + 1}-{end})")
        for a in shown:
            host = a.get("hostname") or str(a["id"])
            cat = a.get("category") or "unknown"
            extra = [str(a[k]) for k in ("ip", "model") if a.get(k)]
            suffix = f" — {', '.join(extra)}" if extra else ""
            lines.append(f"- {host} ({cat}){suffix}  [id={a['id']}]")
        remaining = total_assets - end
        if remaining > 0:
            lines.append(
                f"... and {remaining} more affected asset(s) "
                f"(page with offset/limit).")
    else:
        rec = alert.get("asset_count") or 0
        if rec:
            lines.append(
                f"\n{rec} affected assets recorded but none returned by lookup.")
        else:
            lines.append("\nNo affected assets recorded.")

    text = "\n".join(lines)
    if return_data:
        data = {
            "alert": {k: v for k, v in alert.items() if k != "asset_ids"},
            "assets": shown,
            "asset_count": total_assets,
            "title": alert.get("title"),
            "offset": offset,
            "limit": limit,
        }
        return text, data
    return text


_ALLOWED_ALERT_STATUSES = ("active", "acknowledged", "resolved", "any")


def list_alerts_for_asset_impl(
    db,
    *,
    asset_id: Optional[str] = None,
    status: str = "active",
    limit: int = 10,
) -> str:
    if status not in _ALLOWED_ALERT_STATUSES:
        return unknown_enum("status", status, _ALLOWED_ALERT_STATUSES)
    try:
        limit = max(1, min(int(limit or 10), 50))
    except (TypeError, ValueError):
        limit = 10

    params: Dict[str, Any] = {}
    status_where = "" if status == "any" else "AND status = %(status)s"
    if status != "any":
        params["status"] = status

    if asset_id:
        err = validate_uuid(asset_id, "asset")
        if err:
            return err
        # Confirm the asset exists BEFORE querying alerts. Without this, a
        # typo UUID silently returns "0 alerts reference asset <UUID>" as
        # if the asset were clean, which is actively misleading.
        asset = db.execute_query(
            """
            SELECT id, hostname, category,
                   ip[1]::text AS ip, model[1] AS model
            FROM assets WHERE id = %(id)s::uuid
            """,
            {"id": asset_id},
        )
        if not asset:
            return f"Asset {asset_id} not found."
        host_str = format_hostname(asset[0])
        meta = [str(asset[0][k]) for k in ("ip", "model") if asset[0].get(k)]
        meta_str = f" ({', '.join(meta)})" if meta else ""

        # Real total FIRST so a capped listing reports the true count, not the
        # number of rows we happened to render (an asset can carry far more
        # alerts than `limit`; reporting len(rows) would under-count).
        total_row = db.execute_query(
            f"""
            SELECT COUNT(*) AS n
            FROM alerts
            WHERE %(id)s::uuid = ANY(asset_ids) {status_where}
            """,
            {**params, "id": asset_id},
        )
        total = int(total_row[0]["n"]) if total_row else 0

        rows = db.execute_query(
            f"""
            SELECT id, title, severity, type, status, asset_count,
                   created_at, resolved_at
            FROM alerts
            WHERE %(id)s::uuid = ANY(asset_ids)
              {status_where}
            ORDER BY
              CASE severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                            WHEN 'medium' THEN 3 ELSE 4 END,
              created_at DESC
            LIMIT %(limit)s
            """,
            {**params, "id": asset_id, "limit": limit},
        ) or []

        if not rows:
            scope = "" if status == "any" else f" with status='{status}'"
            return (
                f"COUNT: 0 alerts reference asset {host_str}{meta_str}{scope}."
            )
        lines = [
            f"COUNT: {total} alerts reference asset {host_str}{meta_str} "
            f"(status={status}).",
            "",
            f"{'severity':<9s} {'type':<13s} {'status':<13s} "
            f"{'assets':>6s}  title (created / resolved)",
        ]
        lines.append("-" * 80)
        for r in rows:
            created = str(r.get("created_at") or "")[:19]
            resolved = (f"  resolved={str(r['resolved_at'])[:19]}"
                        if r.get("resolved_at") else "")
            lines.append(
                f"{(r['severity'] or ''):<9s} {(r['type'] or ''):<13s} "
                f"{(r['status'] or ''):<13s} "
                f"{int(r.get('asset_count') or 0):>6d}  "
                f"{r['title']}  (id={r['id']})  created={created}{resolved}"
            )
        note = overflow_note(
            total, len(rows), noun="alerts",
            hint="highest-severity shown first; raise `limit` (≤50)")
        if note:
            lines.append(note)
        return "\n".join(lines)

    # No asset_id → top-N roll-up.
    rows = db.execute_query(
        f"""
        WITH x AS (
            SELECT unnest(asset_ids) AS asset_id
            FROM alerts
            WHERE 1=1 {status_where}
        )
        SELECT a.id, a.hostname, a.category,
               a.ip[1]::text AS ip, a.model[1] AS model, COUNT(*) AS n
        FROM x LEFT JOIN assets a ON a.id = x.asset_id
        GROUP BY a.id, a.hostname, a.category
        ORDER BY n DESC, a.hostname NULLS LAST
        LIMIT %(limit)s
        """,
        {**params, "limit": limit},
    ) or []
    if not rows:
        return (
            f"COUNT: 0 alert-asset links found "
            f"(status={status}). Either there are no {status} alerts or "
            f"they reference no known assets."
        )

    total_links = db.execute_query(
        f"""
        SELECT COUNT(*) AS n
        FROM (SELECT unnest(asset_ids) FROM alerts WHERE 1=1 {status_where}) q
        """,
        params,
    )
    total_links = int(total_links[0]["n"]) if total_links else 0

    lines = [
        f"COUNT: {total_links} alert-asset links (status={status}); "
        f"showing the {len(rows)} noisiest assets.",
        "",
        f"{'alerts':>6s}  {'category':<15s} hostname / uuid (ip, model)",
        "-" * 70,
    ]
    for r in rows:
        host_str = format_hostname(r) if (r.get("id") or r.get("hostname")) else "(orphaned)"
        meta = [str(r[k]) for k in ("ip", "model") if r.get(k)]
        meta_str = f"  ({', '.join(meta)})" if meta else ""
        lines.append(
            f"{int(r['n']):>6d}  {(r.get('category') or '-'):<15s} "
            f"{host_str}{meta_str}"
        )
    # Hitting the cap means more alert-bearing assets exist below the top-N; say
    # so rather than letting the model treat the noisiest slice as the whole set.
    if len(rows) >= limit:
        lines.append(
            f"[More assets carry {status} alerts below the top {len(rows)} "
            f"shown — raise `limit` (≤50) to see more.]")
    return "\n".join(lines)


# Firing-history text cap: the series is oldest→newest and the repo returns up
# to 500 events; a chat turn shows at most this many (the most recent = the
# right edge of the timechart) so a dense alert can't blow the context window.
_MAX_HISTORY_ROWS = 50


def get_alert_history_impl(
    alerts_service, *, alert_id: str, days: int = 90,
) -> str:
    """Per-run firing history for one alert (#905): how its matched asset_count
    / severity moved across recent pipeline runs. Wraps
    ``AlertsService.get_alert_history``; the series is oldest→newest, matching
    the Alerts-page drilldown timechart."""
    if alerts_service is None:
        return "Alerts service not available."
    err = validate_uuid(alert_id, "alert")
    if err:
        return err
    try:
        days = max(1, min(int(days or 90), 730))
    except (TypeError, ValueError):
        days = 90

    try:
        result = alerts_service.get_alert_history(alert_id, days=days)
    except Exception as e:
        return f"Alert history unavailable: {e}"

    history = (result or {}).get("history") or []
    if not history:
        return (
            f"COUNT: 0 firing-history events for alert {alert_id} in the last "
            f"{days} days (no pipeline run recorded a firing in the window)."
        )

    # Series is oldest→newest; when it exceeds the text cap keep the most recent
    # rows (the right edge of the timechart) and say how many precede them.
    shown = history[-_MAX_HISTORY_ROWS:]
    lines = [
        f"COUNT: {len(history)} firing-history event(s) for alert {alert_id} "
        f"(last {days} days, oldest→newest).",
        "",
        f"{'when':<20s} {'status':<11s} {'severity':<9s} {'assets':>6s}  "
        f"pipeline run",
        "-" * 78,
    ]
    for point in shown:
        when = str(point.get("started_at") or point.get("created_at") or "")[:19]
        status = str(point.get("status") or "-")
        severity = str(point.get("severity") or "-")
        try:
            assets = int(point.get("asset_count") or 0)
        except (TypeError, ValueError):
            assets = 0
        run = point.get("pipeline_run_id") or "-"
        lines.append(
            f"{when:<20s} {status:<11s} {severity:<9s} {assets:>6d}  {run}")
    note = overflow_note(
        len(history), len(shown), noun="earlier events",
        hint="narrow `days` to focus")
    if note:
        lines.append(note)
    return "\n".join(lines)
