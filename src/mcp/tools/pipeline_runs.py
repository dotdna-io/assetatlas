# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pipeline run history tools for the AssetAtlas MCP server.

Tools: list_pipeline_runs
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from src.mcp.context import AppContext
from src.mcp.tools._common import (
    CHAT_TEXT_ROW_CAP,
    overflow_note,
    truncate,
    unknown_enum,
    validate_uuid,
)
from src.modules.pipeline.pipeline_service import _split_created_readded

logger = logging.getLogger(__name__)

_MAX_PAGE_SIZE = 100
_ERROR_TRUNC_CHARS = 60

# Loud-validated so a typo'd status filter errors instead of silently
# returning every run. A dropped filter *widens* the result, which is both a
# correctness bug and an over-disclosure risk (plan §4).
_VALID_STATUSES = ("pending", "running", "completed", "failed")

# Sort keys advertised to the model -> column the service orders by. A bad
# sort must be a loud error, not a silent fallback to the default order
# (which would answer a *different* question than the one asked).
_VALID_SORTS = ("created", "started", "completed", "duration", "status")


def register_tools(mcp):
    """Register pipeline-run tools on the FastMCP server."""

    @mcp.tool()
    def list_pipeline_runs(
        run_id: str | None = None,
        page: int = 1,
        per_page: int = 20,
        ctx: Context[ServerSession, AppContext] = None,
    ) -> str:
        """List pipeline runs, or return full detail + stats for one run."""
        pipeline_service = ctx.request_context.lifespan_context.pipeline_service
        if pipeline_service is None:
            return "Pipeline service not available."
        return list_pipeline_runs_impl(
            pipeline_service,
            run_id=run_id,
            page=page,
            per_page=per_page,
        )


def list_pipeline_runs_impl(
    pipeline_service,
    *,
    phase_service=None,
    run_id: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    sort: str = "created",
    sort_dir: str = "desc",
    page: int = 1,
    per_page: int = 20,
    phases: bool = False,
) -> str:
    if pipeline_service is None:
        return "Pipeline service not available."

    # Single-run views (detail / phase breakdown) resolve a UUID first so a
    # malformed id never reaches the service.
    if run_id is not None:
        err = validate_uuid(run_id, "pipeline run")
        if err:
            return err
        if phases:
            return _format_phases(phase_service, run_id)
        run = pipeline_service.get_run(UUID(run_id))
        if run is None:
            return f"Pipeline run {run_id} not found."
        return _format_detail(run)

    # phases is a per-run view — without a run_id there's nothing to break down.
    if phases:
        return ("phases=true needs a run_id (the stage breakdown is per-run). "
                "List runs first, then pass one run's id.")

    # Validate filters/sort loudly (never silently widen).
    if status is not None and status not in _VALID_STATUSES:
        return unknown_enum("status", status, _VALID_STATUSES)
    if sort not in _VALID_SORTS:
        return unknown_enum("sort", sort, _VALID_SORTS)
    direction = str(sort_dir).lower()
    if direction not in ("asc", "desc"):
        return unknown_enum("sort_dir", sort_dir, ("asc", "desc"))

    start_dt, err = _parse_date(start_date, "start_date")
    if err:
        return err
    # end_date is an INCLUSIVE upper bound: a date-only value means "through
    # the end of that day", so widen it to end-of-day (otherwise `<= midnight`
    # silently drops the whole end day).
    end_dt, err = _parse_date(end_date, "end_date", end_of_day=True)
    if err:
        return err

    # Coerce paging like the sibling tools (enrichment/scheduler) — the chat
    # registry does not type-coerce, so a JSON-string "2" would otherwise hit
    # max("2", 1) -> TypeError -> opaque "failed" instead of page 2.
    page = max(int(page or 1), 1)
    per_page = max(1, min(int(per_page or 20), _MAX_PAGE_SIZE))

    result = pipeline_service.get_runs(
        page=page, per_page=per_page,
        status=status, start_date=start_dt, end_date=end_dt,
        sort=sort, sort_dir=direction,
    )
    runs = result.get("runs") or []

    # Merge the live status (replaces the old get_pipeline_status tool) so one
    # call answers "what's running now" + "what ran before".
    header = _format_current_status(pipeline_service)

    if not runs:
        if status or start_dt or end_dt:
            body = ("No pipeline runs match the given filters. Widen the "
                    "status/date filters to see more.")
        elif page > 1:
            # Empty + unfiltered + past page 1 means we paged off the end, not
            # that no runs exist — don't imply an empty system.
            body = (f"No pipeline runs on page {page} — it is past the last "
                    f"page. Request page 1.")
        else:
            body = "No pipeline runs recorded."
        return "\n".join(header + ["", body]) if header else body

    total = result.get("total", len(runs))
    pages = result.get("pages", 1)
    cur_page = result.get("page", page)

    lines = list(header)
    if header:
        lines.append("")
    lines += [
        f"# Pipeline runs (page {cur_page} of {pages}, total {total})",
        "| Run ID | Status | Triggered | Started | Duration | Assets | Error |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in runs:
        err_msg = truncate(r.get("error_message"), _ERROR_TRUNC_CHARS) or "—"
        dur = r.get("duration_seconds")
        dur_s = f"{dur}s" if dur is not None else "—"
        lines.append(
            f"| {r.get('id')} | {r.get('status')} | {r.get('triggered_by')} "
            f"| {r.get('started_at') or '—'} | {dur_s} "
            f"| {r.get('total_assets', 0)} | {err_msg} |"
        )

    if cur_page < pages:
        remaining = total - cur_page * per_page
        lines.append("")
        lines.append(
            f"_{remaining} more run(s) not shown — request page "
            f"{cur_page + 1}, or narrow with status/date filters._"
        )
    lines.append("")
    lines.append("_Pass a Run ID as `run_id` for batch/stage/chronicle detail; "
                 "add `phases=true` for the per-stage timing breakdown._")
    return "\n".join(lines)


def _parse_date(value, param, *, end_of_day=False):
    """Parse an ISO-8601 date/datetime, or return a loud error string.

    Naive inputs are pinned to UTC so the filter compares apples-to-apples
    against the ``timestamptz`` ``created_at`` column (otherwise PostgreSQL
    interprets a naive literal in the session timezone). When ``end_of_day``
    is set and the caller gave a date with no time component, the bound is
    widened to 23:59:59.999999 so an inclusive ``<=`` upper bound covers the
    whole day.
    """
    if value is None or value == "":
        return None, None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None, (
            f"Invalid {param} {value!r}. Use ISO-8601, e.g. '2026-04-20' "
            f"or '2026-04-20T10:00:00'."
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if end_of_day and "T" not in value and ":" not in value:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt, None


def _format_current_status(pipeline_service) -> list:
    """Render the live pipeline status, or [] when unavailable.

    Guards on ``isinstance(dict)`` so a stub/mock service (which returns a
    MagicMock) renders nothing rather than garbage.
    """
    try:
        status = pipeline_service.get_current_status()
    except Exception:
        # The live-status header is best-effort; a failure here must not sink
        # the run list. Log so the failure is diagnosable (matches the
        # logger.exception pattern in the sibling scheduler/enrichment tools).
        logger.exception("Pipeline current-status header unavailable")
        return []
    if not isinstance(status, dict):
        return []

    state = status.get("status")
    lines = ["# Pipeline status"]
    if state in ("running", "pending"):
        lines.append(f"- state: {state}")
        if status.get("run_id"):
            lines.append(f"- active run: {status['run_id']}")
        if status.get("current_stage"):
            lines.append(f"- stage: {status['current_stage']}")
        if status.get("elapsed_seconds") is not None:
            lines.append(f"- elapsed: {status['elapsed_seconds']}s")
    else:
        lines.append("- state: idle")
        last = status.get("last_run")
        if isinstance(last, dict):
            lines.append(
                f"- last run: {last.get('run_id')} [{last.get('status')}] "
                f"completed {last.get('completed_at') or '—'}"
            )
    return lines


def _format_phases(phase_service, run_id: str) -> str:
    if phase_service is None:
        return ("Phase breakdown is not available in this context "
                "(pipeline phase service not configured).")
    result = phase_service.get_phases_for_run(UUID(run_id))
    if result is None:
        return f"Pipeline run {run_id} not found."

    lines = [
        f"# Pipeline run {result.get('run_id')} — phase breakdown",
        f"- run status: {result.get('status')}",
    ]
    for ph in result.get("phases") or []:
        slow = " ⚠ has slow stage(s)" if ph.get("has_slow_stages") else ""
        lines.append("")
        lines.append(
            f"## {ph.get('label')} — {ph.get('status')} "
            f"({ph.get('duration_seconds')}s){slow}"
        )
        subs = ph.get("sub_stages") or []
        if not subs:
            lines.append("_no recorded sub-stage timings_")
            continue
        lines.append("| Stage | Duration (s) | p95 (s) | samples | slow? |")
        lines.append("| --- | --- | --- | --- | --- |")
        for s in subs:
            p95 = s.get("p95_seconds")
            lines.append(
                f"| {s.get('stage_key')} | {s.get('duration_seconds')} "
                f"| {p95 if p95 is not None else '—'} | {s.get('samples')} "
                f"| {'yes' if s.get('is_slow') else 'no'} |"
            )
    return "\n".join(lines)


def _format_detail(run: dict) -> str:
    stats = run.get("stats") or {}
    # `get_run` returns raw `stats` JSON (not the flattened list-view shape),
    # so pull asset counters from there with the raw pipeline keys.
    total_assets = stats.get("total_assets", 0)
    # New/Returned split shared with the REST surfaces (get_runs etc.) so the
    # chatbot and UI report the same counts; legacy runs fall back to combined.
    assets_created, assets_readded = _split_created_readded(stats)
    assets_updated = stats.get("total_assets_updated", 0)
    # "removed" is genuine departures only; "merged" is identity consolidations
    # (pipeline_merge) — same split the REST/UI surfaces report.
    assets_removed = stats.get("removed_assets", 0)
    assets_merged = stats.get("merged_assets", 0)
    batches = stats.get("batches_processed", 0)

    dur = run.get("duration_seconds")
    dur_s = f"{dur}s" if dur is not None else "—s"
    completed = run.get("completed_at") or "running"
    error = run.get("error_message") or "none"
    stage = run.get("current_stage") or "—"

    lines = [
        f"# Pipeline run {run.get('id')}",
        f"- status: {run.get('status')}",
        f"- triggered by: {run.get('triggered_by')}",
        f"- stage: {stage}",
        f"- started: {run.get('started_at') or '—'}",
        f"- completed: {completed}",
        f"- duration: {dur_s}",
        f"- error: {error}",
        "",
        "## Asset stats",
        f"- total assets: {total_assets}",
        f"- created: {assets_created}",
        f"- returned: {assets_readded}",
        f"- updated: {assets_updated}",
        f"- removed: {assets_removed}",
        f"- merged: {assets_merged}",
        f"- batches: {batches}",
    ]

    # Chronicle event breakdown (event_type -> count).
    chronicle = stats.get("chronicle_events")
    if isinstance(chronicle, dict) and chronicle:
        lines.append("")
        lines.append("## Chronicle events")
        for event_type, count in chronicle.items():
            lines.append(f"- {event_type}: {count}")

    # Per-stage wall-clock timings.
    timings = stats.get("stage_timings")
    if isinstance(timings, list) and timings:
        lines.append("")
        lines.append("## Stage timings")
        lines.append("| Stage | Duration (s) |")
        lines.append("| --- | --- |")
        for t in timings:
            lines.append(f"| {t.get('name')} | {t.get('duration')} |")

    # Per-batch (per-source) results. A heavily-integrated run can carry dozens
    # of batches, so cap the rendered rows (header keeps the real total) and
    # signal the tail rather than dumping every batch into context.
    batch_results = stats.get("batch_results")
    if isinstance(batch_results, list) and batch_results:
        lines.append("")
        lines.append(f"## Batch results ({len(batch_results)})")
        lines.append("| Batch | Source | Success | Input | Unique | Error |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for b in batch_results[:CHAT_TEXT_ROW_CAP]:
            src = (b.get("integration_name") or b.get("integration_type")
                   or "—")
            b_err = truncate(b.get("error"), _ERROR_TRUNC_CHARS) or "—"
            unique = b.get("unique_assets")
            lines.append(
                f"| {b.get('batch_id') or '—'} | {src} "
                f"| {'yes' if b.get('success') else 'no'} "
                f"| {b.get('input_records', 0)} "
                f"| {unique if unique is not None else '—'} | {b_err} |"
            )
        note = overflow_note(
            len(batch_results), min(len(batch_results), CHAT_TEXT_ROW_CAP),
            noun="batches", hint="inspect a specific batch by its id")
        if note:
            lines.append(note)

    return "\n".join(lines)
