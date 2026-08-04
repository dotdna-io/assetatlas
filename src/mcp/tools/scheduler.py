# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scheduler status tools for the AssetAtlas MCP server.

Tools: get_scheduler_status
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from src.mcp.context import AppContext, get_db
from src.mcp.tools._common import truncate, unknown_enum

logger = logging.getLogger(__name__)

SchedulerScope = Literal[
    "all", "health", "statistics", "alerts", "history", "job_health"
]
_VALID_SCOPES: tuple[SchedulerScope, ...] = (
    "all", "health", "statistics", "alerts", "history", "job_health",
)

# Row caps used to be a hardcoded 50; lifted to a per-call `limit` (clamped to
# _MAX_LIMIT) with an overflow signal so the model is never silently truncated
# (plan §4). The default keeps output compact for the common case.
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 1000
_ERROR_TRUNC_CHARS = 80

# Alert windows were hardcoded ("stuck > 1h", "failures < 24h"); now caller-
# tunable so "anything stuck longer than 4h" is answerable.
_DEFAULT_STUCK_HOURS = 1.0
_DEFAULT_FAILURE_HOURS = 24.0


def register_tools(mcp):
    """Register scheduler-status tools on the FastMCP server."""

    @mcp.tool()
    def get_scheduler_status(
        scope: str = "all",
        job_id: str | None = None,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """Scheduler health, statistics, history, per-job health, and alerts.

        ``scope`` is one of all|health|statistics|alerts|history|job_health.
        """
        scheduler_service = ctx.request_context.lifespan_context.scheduler_service
        if scheduler_service is None:
            return "Scheduler service not available."
        return get_scheduler_status_impl(
            scheduler_service,
            get_db(ctx),
            scope=scope,
            job_id=job_id,
        )


def get_scheduler_status_impl(
    scheduler_service,
    db,
    *,
    scope: SchedulerScope = "all",
    job_id: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    stuck_hours: float = _DEFAULT_STUCK_HOURS,
    failure_hours: float = _DEFAULT_FAILURE_HOURS,
) -> str:
    if scope not in _VALID_SCOPES:
        return unknown_enum("scope", scope, _VALID_SCOPES)

    err = _validate_positive("stuck_hours", stuck_hours)
    if err:
        return err
    err = _validate_positive("failure_hours", failure_hours)
    if err:
        return err
    # Bind numeric (not the raw arg): a JSON-string "4" would otherwise reach
    # `%(stuck_hours)s * INTERVAL '1 hour'` as text and raise "operator does
    # not exist: text * interval". _validate_positive already proved float()
    # succeeds.
    stuck_hours = float(stuck_hours)
    failure_hours = float(failure_hours)
    limit = _clamp_limit(limit)

    if scope == "health":
        return _render_health(scheduler_service)
    if scope == "statistics":
        return _render_statistics(db, job_id=job_id, limit=limit)
    if scope == "alerts":
        return _render_alerts(db, stuck_hours=stuck_hours,
                              failure_hours=failure_hours, limit=limit)
    if scope == "history":
        return _render_history(db, job_id=job_id, limit=limit)
    if scope == "job_health":
        return _render_job_health(db, job_id=job_id, limit=limit)

    # scope == "all": render each section; on failure of any one, show a
    # generic error inline (never the raw exception text — §4) and continue.
    sections: List[str] = []

    sections.append("# Scheduler health")
    sections.append(_render_health(scheduler_service))

    sections.append("")
    sections.append("# Statistics")
    try:
        sections.append(_render_statistics(db, job_id=job_id, limit=limit))
    except Exception:
        logger.exception("Scheduler statistics render failed")
        sections.append("Statistics unavailable (see server logs).")

    sections.append("")
    sections.append("# Alerts")
    try:
        sections.append(_render_alerts(db, stuck_hours=stuck_hours,
                                       failure_hours=failure_hours,
                                       limit=limit))
    except Exception:
        logger.exception("Scheduler alerts render failed")
        sections.append("Alerts unavailable (see server logs).")

    return "\n".join(sections)


def _validate_positive(name: str, value) -> str | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return f"Invalid {name} {value!r}: expected a positive number of hours."
    if v <= 0:
        return f"Invalid {name} {value!r}: must be greater than 0."
    return None


def _clamp_limit(limit) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT
    return max(1, min(n, _MAX_LIMIT))


def _total_of(rows: List[Dict[str, Any]]) -> int:
    """Real total from the windowed ``COUNT(*) OVER()`` column, else len(rows).

    The query carries the unbounded count in every row's ``total_count`` so we
    get a real total without a second COUNT query; the fallback keeps stubbed
    tests (which don't set total_count) rendering sanely.
    """
    if rows and rows[0].get("total_count") is not None:
        return int(rows[0]["total_count"])
    return len(rows)


def _showing_line(noun: str, shown: int, total: int, limit: int) -> str:
    if total > shown:
        return (
            f"Showing {shown} of {total} {noun}(s) — {total - shown} more not "
            f"shown; raise `limit` (max {_MAX_LIMIT}) or filter by job_id."
        )
    return f"Showing {shown} {noun}(s) (total {total})."


def _fmt_hours(h: float) -> str:
    h = float(h)
    return f"{int(h)}" if h == int(h) else f"{h}"


def _render_health(scheduler_service) -> str:
    try:
        health = scheduler_service.get_health_status() or {}
    except Exception:
        # Never leak raw exception text into the transcript (§4).
        logger.exception("Scheduler health unavailable")
        return "Scheduler health unavailable (see server logs)."

    status = health.get("status", "unknown")
    running = health.get("running", False)
    metrics = health.get("metrics") or {}
    job_count = metrics.get("jobs_scheduled", health.get("job_count", 0))
    uptime = health.get("uptime_seconds")
    next_run = health.get("next_run")
    last_success = health.get("last_success")
    last_error = health.get("last_error")

    running_str = "running" if running else "stopped"

    lines = [
        f"- status: {status}",
        f"- state: {running_str}",
        f"- jobs scheduled: {job_count}",
    ]
    if uptime is not None:
        lines.append(f"- uptime: {uptime}s")
    if next_run:
        lines.append(f"- next run: {next_run}")
    if last_success:
        lines.append(f"- last success: {last_success}")
    if last_error:
        lines.append(f"- last error: {last_error}")
    return "\n".join(lines)


def _render_statistics(db, *, job_id: str | None = None,
                       limit: int = _DEFAULT_LIMIT) -> str:
    conditions = ""
    params: Dict[str, Any] = {"lim": limit}
    if job_id:
        conditions = "WHERE job_id = %(jid)s"
        params["jid"] = job_id

    sql = f"""
        SELECT *, COUNT(*) OVER() AS total_count
        FROM job_execution_stats
        {conditions}
        ORDER BY last_execution_at DESC NULLS LAST
        LIMIT %(lim)s
    """
    rows: List[Dict[str, Any]] = db.execute_query(sql, params) or []
    if not rows:
        return "No job statistics available."

    total = _total_of(rows)
    lines = [
        _showing_line("job", len(rows), total, limit),
        "",
        "| job_id | job_name | total | success | failed | success % | avg (s) | last run | last success | last failure |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            "| "
            + " | ".join([
                str(r.get("job_id") or "—"),
                str(r.get("job_name") or "—"),
                str(r.get("total_executions", 0)),
                str(r.get("successful_executions", 0)),
                str(r.get("failed_executions", 0)),
                str(r.get("success_rate") if r.get("success_rate") is not None else "—"),
                str(r.get("avg_duration_seconds") if r.get("avg_duration_seconds") is not None else "—"),
                str(r.get("last_execution_at") or "—"),
                str(r.get("last_success_at") or "—"),
                str(r.get("last_failure_at") or "—"),
            ])
            + " |"
        )
    return "\n".join(lines)


def _render_alerts(db, *, stuck_hours: float = _DEFAULT_STUCK_HOURS,
                   failure_hours: float = _DEFAULT_FAILURE_HOURS,
                   limit: int = _DEFAULT_LIMIT) -> str:
    # NOW() - (N * INTERVAL '1 hour') keeps the window parameterized (a bare
    # INTERVAL '%s hours' can't be bound safely).
    stuck_sql = """
        SELECT job_id, job_name, execution_id, started_at, status,
               EXTRACT(EPOCH FROM (NOW() - started_at)) AS seconds_running,
               COUNT(*) OVER() AS total_count
        FROM job_executions
        WHERE status = 'running'
          AND started_at < NOW() - (%(stuck_hours)s * INTERVAL '1 hour')
        ORDER BY started_at
        LIMIT %(lim)s
    """
    failed_sql = """
        SELECT job_id, job_name, started_at, error_message,
               COUNT(*) OVER() AS total_count
        FROM job_executions
        WHERE status = 'failed'
          AND started_at > NOW() - (%(fail_hours)s * INTERVAL '1 hour')
        ORDER BY started_at DESC
        LIMIT %(lim)s
    """
    stuck: List[Dict[str, Any]] = db.execute_query(
        stuck_sql, {"stuck_hours": stuck_hours, "lim": limit}
    ) or []
    failed: List[Dict[str, Any]] = db.execute_query(
        failed_sql, {"fail_hours": failure_hours, "lim": limit}
    ) or []

    if not stuck and not failed:
        return (f"No scheduler alerts (stuck > {_fmt_hours(stuck_hours)}h, "
                f"failures < {_fmt_hours(failure_hours)}h).")

    lines: List[str] = []

    stuck_total = _total_of(stuck)
    lines.append(f"## Stuck jobs ({stuck_total}) — running > "
                 f"{_fmt_hours(stuck_hours)}h")
    if stuck:
        lines.append("| job_id | job_name | execution_id | started | seconds |")
        lines.append("| --- | --- | --- | --- | --- |")
        for r in stuck:
            secs = r.get("seconds_running")
            secs_s = f"{float(secs):.0f}" if secs is not None else "—"
            lines.append(
                f"| {r.get('job_id') or '—'} | {r.get('job_name') or '—'} "
                f"| {r.get('execution_id') or '—'} | {r.get('started_at') or '—'} "
                f"| {secs_s} |"
            )
        if stuck_total > len(stuck):
            lines.append(_showing_line("stuck job", len(stuck), stuck_total,
                                       limit))
    else:
        lines.append("_none_")

    lines.append("")
    failed_total = _total_of(failed)
    lines.append(f"## Recent failures ({failed_total}) — last "
                 f"{_fmt_hours(failure_hours)}h")
    if failed:
        lines.append("| job_id | job_name | started | error |")
        lines.append("| --- | --- | --- | --- |")
        for r in failed:
            err = truncate(r.get("error_message"), _ERROR_TRUNC_CHARS) or "—"
            lines.append(
                f"| {r.get('job_id') or '—'} | {r.get('job_name') or '—'} "
                f"| {r.get('started_at') or '—'} | {err} |"
            )
        if failed_total > len(failed):
            lines.append(_showing_line("failure", len(failed), failed_total,
                                       limit))
    else:
        lines.append("_none_")

    return "\n".join(lines)


def _render_history(db, *, job_id: str | None = None,
                    limit: int = _DEFAULT_LIMIT) -> str:
    conditions = ""
    params: Dict[str, Any] = {"lim": limit}
    if job_id:
        conditions = "WHERE job_id = %(jid)s"
        params["jid"] = job_id

    sql = f"""
        SELECT job_id, job_name, status, started_at, completed_at,
               duration_seconds, records_processed, error_message,
               attempt_number, COUNT(*) OVER() AS total_count
        FROM job_executions
        {conditions}
        ORDER BY started_at DESC
        LIMIT %(lim)s
    """
    rows: List[Dict[str, Any]] = db.execute_query(sql, params) or []
    if not rows:
        return "No job executions recorded."

    total = _total_of(rows)
    lines = [
        _showing_line("execution", len(rows), total, limit),
        "",
        "| job_id | job_name | status | started | duration (s) | records | attempt | error |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        dur = r.get("duration_seconds")
        err = truncate(r.get("error_message"), _ERROR_TRUNC_CHARS) or "—"
        lines.append(
            f"| {r.get('job_id') or '—'} | {r.get('job_name') or '—'} "
            f"| {r.get('status') or '—'} | {r.get('started_at') or '—'} "
            f"| {dur if dur is not None else '—'} "
            f"| {r.get('records_processed') if r.get('records_processed') is not None else '—'} "
            f"| {r.get('attempt_number') if r.get('attempt_number') is not None else '—'} "
            f"| {err} |"
        )
    return "\n".join(lines)


def _render_job_health(db, *, job_id: str | None = None,
                       limit: int = _DEFAULT_LIMIT) -> str:
    # Per-job health comes from the canonical get_job_health_status() SQL
    # function (same source the UI's /jobs/<id>/health route uses). It is
    # per-job, so a job_id is required; help the model pick one when missing.
    if not job_id:
        rows = db.execute_query(
            """SELECT job_id, job_name FROM job_execution_stats
               ORDER BY last_execution_at DESC NULLS LAST LIMIT %(lim)s""",
            {"lim": min(limit, 50)},
        ) or []
        if not rows:
            return ("scope='job_health' needs a `job_id`, and no jobs were "
                    "found in job_execution_stats yet.")
        listing = "\n".join(
            f"- {r.get('job_id')} ({r.get('job_name') or '—'})" for r in rows
        )
        return ("scope='job_health' needs a `job_id`. Known jobs:\n" + listing)

    rows = db.execute_query(
        "SELECT * FROM get_job_health_status(%(jid)s)", {"jid": job_id}
    ) or []
    if not rows:
        return (f"No health data for job {job_id!r} (no executions in the "
                f"last 7 days).")

    h = rows[0]
    avg = h.get("avg_duration")
    lines = [
        f"# Job health: {job_id}",
        f"- status: {h.get('health_status')}",
        f"- recent failures (24h): {h.get('recent_failures')}",
        f"- success rate: {h.get('success_rate')}",
        f"- avg duration: {avg if avg is not None else '—'}s",
        f"- last success: {h.get('last_success') or '—'}",
        f"- last failure: {h.get('last_failure') or '—'}",
        f"- stuck: {'yes' if h.get('is_stuck') else 'no'}",
    ]

    recent = db.execute_query(
        """SELECT status, started_at, duration_seconds, error_message
           FROM job_executions WHERE job_id = %(jid)s
           ORDER BY started_at DESC LIMIT %(lim)s""",
        {"jid": job_id, "lim": min(limit, 10)},
    ) or []
    if recent:
        lines.append("")
        lines.append(f"## Recent executions ({len(recent)})")
        lines.append("| status | started | duration (s) | error |")
        lines.append("| --- | --- | --- | --- |")
        for r in recent:
            dur = r.get("duration_seconds")
            err = truncate(r.get("error_message"), _ERROR_TRUNC_CHARS) or "—"
            lines.append(
                f"| {r.get('status') or '—'} | {r.get('started_at') or '—'} "
                f"| {dur if dur is not None else '—'} | {err} |"
            )
    return "\n".join(lines)
