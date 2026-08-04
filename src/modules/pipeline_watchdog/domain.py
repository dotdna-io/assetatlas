# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pipeline Watchdog domain types and pure helpers.

Contains:
- CheckResult  : Pydantic model returned by each of the three checks.
- ScheduleSettings : frozen dataclass capturing the pipeline schedule knobs.
- last_expected_fire_at : pure function answering "when was a run last due".

No I/O. Safe to import anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from croniter import croniter
from pydantic import BaseModel


CheckType = Literal["stale", "last-run-failed", "stuck"]
Severity = Literal["critical", "high", "medium", "low"]


class CheckResult(BaseModel):
    """Result of one watchdog check on a single tick."""

    check_type: CheckType
    should_fire: bool
    severity: Severity
    title: str
    description: str
    threshold: dict
    run_id: Optional[UUID] = None


@dataclass(frozen=True)
class ScheduleSettings:
    """Snapshot of the pipeline-schedule settings the watchdog needs."""

    enabled: bool
    mode: Literal["interval", "cron"]
    interval_hours: int
    cron: str
    timezone: str


def _now(tz: str) -> datetime:
    """Indirection so tests can patch the clock."""
    return datetime.now(ZoneInfo(tz))


def last_expected_fire_at(
    settings: ScheduleSettings, reference: datetime,
) -> Optional[datetime]:
    """The most recent moment a pipeline run should have STARTED.

    This is the question 'pipeline-stale' actually wants answered — a run was
    scheduled, it came due, and nothing has succeeded since. Sizing staleness by
    the schedule's period instead (max gap x 2) was too blunt for a non-uniform
    schedule: ``0 6 * * 1-5`` has a 72 h Friday-to-Monday quiet stretch, so the
    threshold was 144 h and a pipeline that died after Monday's run went
    unreported until Sunday. One missed fire is one missed fire whatever shape
    the week has.

    Cron mode consults the expression itself; ``reference`` is ignored, because
    a cron schedule is absolute. Interval mode has no expression to consult, so
    it counts from ``reference`` — which mirrors the real trigger:
    ``PipelineService.sync_schedule`` pins ``IntervalTrigger(start_date=...)``
    to the last completed run, so fire times sit on the grid
    ``last_success + k x interval``. The caller passes the later of the last
    success and the schedule switch-on; only when neither is on record does
    APScheduler fall back to anchoring at process start.

    Returns None when there is no answer to give: the schedule is off, or its
    cron expression / timezone cannot be read. That has to be a value the caller
    branches on rather than an exception, because the watchdog contains every
    check — a raise here would be logged once per tick and leave the stale alert
    silently dead for as long as the bad setting lives.
    """
    if not settings.enabled:
        return None
    if settings.mode == "interval":
        return reference + timedelta(hours=settings.interval_hours)
    # cron. `pipeline_schedule_cron` and `pipeline_schedule_timezone` are
    # free-text settings with no validator, so both can be unreadable: croniter
    # raises CroniterBadCronError (a ValueError) and ZoneInfo raises
    # ZoneInfoNotFoundError (a KeyError).
    try:
        return croniter(settings.cron, _now(settings.timezone)).get_prev(datetime)
    except (ValueError, KeyError):
        return None
