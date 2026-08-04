# SPDX-License-Identifier: AGPL-3.0-or-later
"""PipelineWatchdogService — orchestrator + per-check logic.

The three check functions are pure-ish (take a repo, return a CheckResult).
They are module-level (not methods) so tests can call them directly with a
fake repo, and so the orchestrator can iterate over them uniformly.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from src.modules.alerts.service import AlertsService
from src.modules.pipeline_watchdog.domain import (
    CheckResult,
    ScheduleSettings,
    last_expected_fire_at,
)
from src.modules.pipeline_watchdog.repository import PipelineWatchdogRepository

logger = logging.getLogger(__name__)

# Window after a row is INSERTed by trigger_run() before its worker thread
# transitions it to 'running' and sets started_at — typically <1s. Anything
# beyond this is unambiguously orphaned (worker crashed before the UPDATE).
ORPHAN_RECOVERY_THRESHOLD_MINUTES = 5

# How many max-duration windows a 'running' row must outlive before recovery
# claims it. Must stay > 1: recovery REWRITES the rows check_stuck reads, so at
# an equal threshold the repair always wins the race and 'pipeline-stuck'
# becomes permanently unfireable. Two windows also buy liveness margin — a run
# that is merely slow keeps its row (and trigger_run()'s concurrency guard) for
# a second full window before the watchdog declares its worker dead.
ORPHAN_RECOVERY_MULTIPLIER = 2


def _not_firing(check_type, severity, threshold=None) -> CheckResult:
    return CheckResult(
        check_type=check_type, should_fire=False, severity=severity,
        title="", description="", threshold=threshold or {},
    )


# ---------------------------------------------------------------------------
# check_stale
# ---------------------------------------------------------------------------

# Reads into "a success has been expected since ...".
_ANCHOR_PHRASE = {
    "first_run_created_at": "the first run was attempted",
    "schedule_enabled_transition": "the schedule was switched on",
    "schedule_enabled_since": "the schedule setting was last written",
}


def _schedule_enabled_at(
    repo: PipelineWatchdogRepository,
) -> Tuple[Optional[datetime], str]:
    """When the schedule was switched on, preferring a real transition.

    The audit answer is the false->true change; schedule_enabled_since is the
    last time the row was written by anything, which a no-op re-save moves to
    now. Layered rather than swapped, because a settings row written directly by
    an installer — or changed before this fix shipped — logs no audit row at all.
    """
    transition = repo.schedule_enabled_transition_at()
    if transition is not None:
        return transition, "schedule_enabled_transition"
    return repo.schedule_enabled_since(), "schedule_enabled_since"


def _first_expectation(
    repo: PipelineWatchdogRepository,
) -> Tuple[Optional[datetime], Optional[str], Optional[datetime]]:
    """Since when a success has been expected, and since when one could be owed.

    Returns (anchor, anchor_source, owed_from).

    ``anchor`` is the EARLIER of the first run attempt and the switch-on — the
    honest answer to "since when have we been expecting a success", which a run
    attempted before the current switch-on still counts towards. It is what the
    alert card reports.

    ``owed_from`` is the switch-on alone, and it is a different question: a
    scheduled fire is only OWED once the schedule is on. Conflating the two is
    what let the eligibility gate be defeated by history — on any install with a
    `pipeline_runs` row older than the switch-on, the anchor is that row, so a
    cron fire from earlier the same day cleared a gate meant to reject it and
    enabling the schedule raised `critical` (with outbound deliveries) on the
    spot. It also sets interval mode's floor: after a switch-on the first run is
    due one interval later, not one interval after a success that predates it.

    ``owed_from`` is None only when the switch-on is unknown (an installer that
    wrote `system_settings` via psql, logging no audit row, on a row whose
    `updated_at` never survived); the caller falls back to ``anchor``.
    """
    enabled_at, enabled_source = _schedule_enabled_at(repo)
    candidates = [
        (repo.first_run_created_at(), "first_run_created_at"),
        (enabled_at, enabled_source),
    ]
    known = [(ts, source) for ts, source in candidates if ts is not None]
    if not known:
        return None, None, enabled_at
    anchor, anchor_source = min(known, key=lambda pair: pair[0])
    return anchor, anchor_source, enabled_at


def check_stale(
    repo: PipelineWatchdogRepository,
    settings: ScheduleSettings,
) -> CheckResult:
    """Fires when a scheduled run came due and nothing has succeeded since.

    That is the question the alert is actually asking. Sizing staleness by the
    schedule's own period instead (no success in 2x the longest quiet stretch)
    was too blunt for a non-uniform schedule: `0 6 * * 1-5` has a 72 h
    Friday-to-Monday gap, so the threshold was 144 h and a pipeline that died
    after Monday's run went unreported until Sunday. Detection now costs one
    missed run plus `pipeline_stale_grace_minutes`, whatever the schedule shape,
    and the 2x fudge is gone.

    Every negative branch returns should_fire=False rather than None, so a
    standing alert is auto-resolved on the next tick.
    """
    if not settings.enabled:
        return _not_firing("stale", "critical", {"reason": "schedule_disabled"})

    # A run that started on time and is still going is not stale, whatever its
    # duration — yet it presents exactly as the comparison below fires on: the
    # scheduled fire has passed and nothing has succeeded since. 'pipeline-stuck'
    # owns duration, at pipeline_max_expected_duration_minutes and its own
    # severity.
    # A genuinely wedged run therefore suppresses 'stale' until running-orphan
    # recovery fails it at ORPHAN_RECOVERY_MULTIPLIER x that ceiling — that is
    # the intended division of labour between the two checks, not an oversight.
    if repo.has_run_in_flight():
        return _not_firing("stale", "critical", {"reason": "run_in_flight"})

    last_success = repo.last_successful_completed_at()

    # Read before the schedule, because interval mode needs it as a fallback
    # reference: with no success on record there is nothing else to count from.
    anchor, anchor_source, owed_from = _first_expectation(repo)
    if anchor is None:
        # No success, no run, no record of the schedule being switched on.
        # Nothing to measure against proves nothing — firing here is the day-one
        # false critical by another route.
        return _not_firing("stale", "critical", {"reason": "no_anchor"})
    if owed_from is None:
        owed_from = anchor

    # Interval mode counts from the LATER of the two: after a switch-on the
    # first run is due one interval later, so a success that predates it (or an
    # install that has been idle for a month with the schedule off) must not
    # make the very next tick overdue. In steady state last_success wins and
    # this is a no-op.
    reference = max(last_success, owed_from) if last_success else owed_from
    last_expected_fire = last_expected_fire_at(settings, reference)
    if last_expected_fire is None:
        # The schedule is enabled but unreadable (a cron expression or timezone
        # that does not parse — neither setting has a validator). A branch, not
        # an escaping exception and not the `assert` #1168 removed: it is
        # testable, it keeps exceptions out of control flow, and it survives
        # `python -O`. But SILENT it hid the fault completely — a not-firing
        # result's threshold is discarded (only a firing result's reaches the
        # alert row), so `reason` gets to no human, and _apply_result resolves
        # whatever stale alert was standing. A typo in either setting therefore
        # switched this `critical` check off, cleared what it had been
        # reporting, and left nothing behind. Warned every tick rather than
        # once, because anyone who starts reading logs after the incident began
        # missed a first-time-only line. The sibling branches stay silent: they
        # are normal operating states, and `no_anchor` heals itself as soon as a
        # run row exists — this one persists until a human retypes the setting.
        logger.warning(
            "pipeline_watchdog: pipeline schedule is unreadable "
            "(pipeline_schedule_cron=%r, pipeline_schedule_timezone=%r); the "
            "stale check cannot run and any standing stale alert is resolved. "
            "This repeats every tick until the setting parses.",
            settings.cron, settings.timezone,
        )
        return _not_firing("stale", "critical", {"reason": "schedule_unreadable"})

    if last_expected_fire < owed_from:
        # A fire that came due before the schedule was switched on was never
        # owed. Without this an admin enabling the schedule at 09:00 with a
        # `0 6 * * *` cron alerts instantly: the previous fire is 06:00, three
        # hours before there was a schedule to miss. Gated on the switch-on and
        # NOT on `anchor` — anchor is the earlier of the switch-on and the first
        # run ever attempted, so on any install with run history it is years old
        # and the gate would pass every fire it exists to reject.
        return _not_firing("stale", "critical", {"reason": "not_due_yet"})

    if last_success is not None and last_success >= last_expected_fire:
        # The most recent run that was due has been met. On a weekday cron this
        # is what keeps the weekend quiet with no 2x fudge: Saturday's most
        # recent fire is Friday 06:00, and Friday succeeded.
        return _not_firing(
            "stale", "critical", {"reason": "success_since_last_fire"},
        )

    grace_minutes = repo.get_stale_grace_minutes()
    now = datetime.now(timezone.utc)
    overdue_seconds = (
        now - (last_expected_fire + timedelta(minutes=grace_minutes))
    ).total_seconds()
    if overdue_seconds <= 0:
        # The run may simply be starting. The grace is what makes this sharper
        # comparison safe: without it, a trigger a few minutes late alerts every
        # cycle.
        return _not_firing("stale", "critical", {"reason": "within_grace"})

    threshold = {
        # Everything the card needs to say why it fired now and not yesterday.
        "last_expected_fire": last_expected_fire.isoformat(),
        "grace_minutes": grace_minutes,
        "last_successful_at": last_success.isoformat() if last_success else None,
        "anchor_at": anchor.isoformat(),
        "anchor_source": anchor_source,
        "overdue_seconds": int(overdue_seconds),
    }
    due_at = last_expected_fire.strftime("%Y-%m-%d %H:%M %Z").strip()

    if last_success is None:
        return CheckResult(
            check_type="stale",
            should_fire=True,
            severity="critical",
            title="Asset generation pipeline has never successfully run",
            description=(
                f"A run was due at {due_at} and no pipeline run has ever "
                "completed successfully; a success has been expected since "
                f"{_ANCHOR_PHRASE.get(anchor_source, 'the schedule began')}. "
                "Check that the pipeline is scheduled correctly and the "
                "scheduler container is healthy."
            ),
            threshold=threshold,
        )

    return CheckResult(
        check_type="stale",
        should_fire=True,
        severity="critical",
        title="Asset generation pipeline is stale",
        description=(
            f"A run was due at {due_at} and nothing has succeeded since; the "
            "last successful run completed "
            f"{int((now - last_success).total_seconds() / 60)} minutes ago "
            f"(grace {grace_minutes} minutes)."
        ),
        threshold=threshold,
    )


# ---------------------------------------------------------------------------
# check_last_run_failed
# ---------------------------------------------------------------------------

def check_last_run_failed(repo: PipelineWatchdogRepository) -> CheckResult:
    """Fires when the most recent finished run has status='failed'."""
    run = repo.last_finished_run()
    if run is None or run["status"] != "failed":
        return _not_firing("last-run-failed", "high")

    err = (run.get("error_message") or "")[:500]
    return CheckResult(
        check_type="last-run-failed",
        should_fire=True,
        severity="high",
        title="Asset generation pipeline last run failed",
        description=(
            "The most recent finished pipeline run ended in failure. "
            "Review the run details and re-trigger once the underlying issue is fixed."
        ),
        threshold={
            "run_id": str(run["id"]),
            "completed_at": run["completed_at"].isoformat() if run.get("completed_at") else None,
            "error_message": err,
        },
        run_id=run["id"],
    )


# ---------------------------------------------------------------------------
# check_stuck
# ---------------------------------------------------------------------------

def check_stuck(
    repo: PipelineWatchdogRepository,
    max_duration_minutes: int,
) -> CheckResult:
    """Fires when any pending/running run has been alive beyond max_duration_minutes.

    If multiple runs are stuck, the oldest is named in the alert (repo returns
    rows ordered by age ascending).
    """
    rows = repo.stuck_runs(max_duration_minutes=max_duration_minutes)
    if not rows:
        return _not_firing("stuck", "high")

    oldest = rows[0]
    started_at = oldest.get("started_at")
    return CheckResult(
        check_type="stuck",
        should_fire=True,
        severity="high",
        title="Asset generation pipeline run is stuck",
        description=(
            f"A pipeline run (status={oldest['status']}) has been alive for "
            f"{int(oldest['running_seconds'] / 60)} minutes; max expected is "
            f"{max_duration_minutes} minutes."
        ),
        threshold={
            "run_id": str(oldest["id"]),
            "status": oldest["status"],
            "started_at": started_at.isoformat() if started_at else None,
            "running_seconds": int(oldest["running_seconds"]),
            "max_expected_duration_minutes": max_duration_minutes,
        },
        run_id=oldest["id"],
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class PipelineWatchdogService:
    """Orchestrates the three checks and translates results into alert writes
    and, for a newly fired alert, outbound deliveries.

    Stateless. One instance per scheduler container is fine; tick() is safe to
    call from an APScheduler job.
    """

    def __init__(
        self,
        watchdog_repo: PipelineWatchdogRepository,
        alert_repo,  # AlertsRepository (duck-typed to avoid an import cycle)
        delivery_service=None,  # AlertDeliveryService; None = create-only, no channels
    ):
        self.watchdog_repo = watchdog_repo
        self.alert_repo = alert_repo
        self.delivery = delivery_service

    def tick(self) -> None:
        """One watchdog tick. Must never raise — it's called by APScheduler.

        Step order matters, because recovery WRITES the same pipeline_runs rows
        the checks read: pending recovery, then check_stuck, then running
        recovery, then the two remaining checks.
        """
        # (a) A row left 'pending' past the spawn window never had a worker at
        # all, so nothing downstream can be waiting on it — clear it first.
        try:
            recovered = self.watchdog_repo.recover_orphaned_pending_runs(
                threshold_minutes=ORPHAN_RECOVERY_THRESHOLD_MINUTES,
            )
            if recovered:
                logger.warning(
                    "pipeline_watchdog: recovered %d orphaned pending run(s)",
                    recovered,
                )
        except Exception:
            logger.exception("pipeline_watchdog: orphan recovery failed")

        try:
            # (b)
            settings = self.watchdog_repo.get_pipeline_schedule_settings()
            max_duration = self.watchdog_repo.get_max_expected_duration_minutes()

            def run_check(check_type: str, check_fn) -> None:
                """Contain one check and its apply — neither can abort the tick."""
                try:
                    self._apply_result(check_type, check_fn())
                except Exception:
                    logger.exception(
                        "pipeline_watchdog: %s handling failed", check_type,
                    )

            # (c) Alert BEFORE repairing. The alert fires at the ceiling the
            # operator declared; recovery only starts a second full window later
            # (d), so the two can never shadow each other. Recovering first at
            # the same threshold is what made 'pipeline-stuck' unfireable — the
            # row was already 'failed' by the time this check queried for
            # pending/running, so it found nothing on every tick.
            run_check(
                "stuck", lambda: check_stuck(self.watchdog_repo, max_duration),
            )

            # (d) Only rows that have outlived the alert window twice over. Past
            # that, _execute_run() — which owns every completed/failed
            # transition — would have written one itself, so the host process is
            # gone (Flask reload, OOM, kill -9).
            try:
                recovered = self.watchdog_repo.recover_orphaned_running_runs(
                    threshold_minutes=max_duration * ORPHAN_RECOVERY_MULTIPLIER,
                )
                if recovered:
                    logger.warning(
                        "pipeline_watchdog: recovered %d orphaned running run(s)",
                        recovered,
                    )
            except Exception:
                logger.exception(
                    "pipeline_watchdog: running-orphan recovery failed",
                )

            # (e) After recovery, so a row just failed above still reaches
            # check_last_run_failed within this same tick.
            run_check("stale", lambda: check_stale(self.watchdog_repo, settings))
            run_check(
                "last-run-failed",
                lambda: check_last_run_failed(self.watchdog_repo),
            )
        except Exception:
            logger.exception("pipeline_watchdog: tick failed")

    def _apply_result(self, check_type: str, result: CheckResult) -> None:
        slug = f"pipeline-{check_type}"
        rule = self.alert_repo.get_rule_by_slug(slug)
        if rule is None:
            logger.warning(
                "pipeline_watchdog: rule %s not found; skipping", slug,
            )
            return
        if not rule["enabled"]:
            # Admin disabled the check — resolve any active alert and move on.
            self.alert_repo.resolve_alerts_for_rule(rule["id"])
            logger.info(
                "pipeline_watchdog: %s rule disabled; resolved active alerts",
                check_type,
            )
            return

        existing = self.alert_repo.get_active_alert_for_rule(rule["id"])

        if result.should_fire:
            if existing:
                self.alert_repo.update_alert_assets(
                    alert_id=existing["id"], asset_count=0,
                    assets_by_category={}, data_sources=[], asset_ids=[],
                    threshold=result.threshold, subject_type="system",
                )
                logger.info(
                    "pipeline_watchdog: %s still firing (alert=%s)",
                    check_type, existing["id"],
                )
            else:
                created = self.alert_repo.create_alert(
                    rule_id=rule["id"], alert_type="operational",
                    severity=result.severity, title=result.title,
                    description=result.description, threshold=result.threshold,
                    asset_count=0, assets_by_category={}, data_sources=[],
                    asset_ids=[], source="system", subject_type="system",
                )
                alert_id = created["id"]
                logger.info(
                    "pipeline_watchdog: fired %s (alert=%s) run=%s",
                    check_type, alert_id, result.run_id,
                )
                self._dispatch_delivery(
                    alert_id, rule, result, created_at=created["created_at"],
                )
        else:
            if existing:
                self.alert_repo.resolve_alert(existing["id"])
                logger.info(
                    "pipeline_watchdog: resolved %s (alert=%s)",
                    check_type, existing["id"],
                )

    def _dispatch_delivery(self, alert_id, rule, result, created_at) -> None:
        """Enqueue outbound deliveries for a newly fired watchdog alert (#985).

        Created branch only: a still-firing alert keeps its existing row, so this is
        one delivery per firing episode rather than one per 5-minute tick. Never
        breaks the check — the alert row is already written, and an enqueue failure
        is swallowed.
        """
        if self.delivery is None:
            return
        try:
            # Mirrors the create_alert call above field-for-field: the channel must
            # describe the same alert the card does.
            alert = {
                "id": alert_id, "type": "operational", "severity": result.severity,
                "title": result.title, "description": result.description,
                "threshold": result.threshold, "asset_count": 0,
                "assets_by_category": {}, "data_sources": [], "asset_ids": [],
                "assets": [], "subject_type": "system", "created_at": created_at,
            }
            # The same plain-language sentence the card shows, via the same static
            # method GET /api/alerts calls — so the two cannot disagree. A watchdog
            # threshold carries no operator, so subject_type="system" lands on
            # _generic_summary_reason("system").
            alert["reason"] = AlertsService._compute_reason(alert)
            self.delivery.enqueue_for_alert(alert, rule)
        except Exception as e:
            logger.error(
                "pipeline_watchdog: delivery enqueue failed (non-fatal): %s",
                e, exc_info=True,
            )
