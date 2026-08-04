# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for PipelineWatchdogService orchestration (tick, _apply_result)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.modules.pipeline_watchdog.domain import CheckResult, ScheduleSettings
from src.modules.pipeline_watchdog.service import PipelineWatchdogService


@pytest.fixture
def alert_repo():
    return MagicMock()


@pytest.fixture
def watchdog_repo():
    repo = MagicMock()
    # Sensible defaults used by tick(); individual tests can override.
    repo.get_pipeline_schedule_settings.return_value = ScheduleSettings(
        enabled=False, mode="interval", interval_hours=24,
        cron="0 0 * * *", timezone="UTC",
    )
    repo.get_max_expected_duration_minutes.return_value = 240
    repo.last_successful_completed_at.return_value = None
    repo.last_finished_run.return_value = None
    repo.stuck_runs.return_value = []
    repo.recover_orphaned_pending_runs.return_value = 0
    repo.recover_orphaned_running_runs.return_value = 0
    return repo


@pytest.fixture
def service(watchdog_repo, alert_repo):
    return PipelineWatchdogService(
        watchdog_repo=watchdog_repo, alert_repo=alert_repo,
    )


def _rule(slug, rule_id=None, enabled=True):
    return {
        "id": rule_id or str(uuid4()),
        "slug": slug,
        "enabled": enabled,
    }


def _firing(check_type, severity="high"):
    return CheckResult(
        check_type=check_type, should_fire=True, severity=severity,
        title="t", description="d", threshold={"k": "v"},
    )


def _not_firing(check_type, severity="high"):
    return CheckResult(
        check_type=check_type, should_fire=False, severity=severity,
        title="", description="", threshold={},
    )


# ---------------------------------------------------------------------------
# _apply_result
# ---------------------------------------------------------------------------

class TestApplyResult:
    def test_creates_alert_when_firing_and_none_exists(self, service, alert_repo):
        rule = _rule("pipeline-stale")
        alert_repo.get_rule_by_slug.return_value = rule
        alert_repo.get_active_alert_for_rule.return_value = None
        alert_repo.create_alert.return_value = {
            "id": "alert-abc", "created_at": datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc)}

        service._apply_result("stale", _firing("stale", severity="critical"))

        alert_repo.create_alert.assert_called_once()
        kwargs = alert_repo.create_alert.call_args.kwargs
        assert kwargs["rule_id"] == rule["id"]
        assert kwargs["severity"] == "critical"
        assert kwargs["source"] == "system"
        assert kwargs["alert_type"] == "operational"
        alert_repo.update_alert_assets.assert_not_called()
        alert_repo.resolve_alert.assert_not_called()

    def test_refreshes_threshold_when_firing_and_alert_already_active(
        self, service, alert_repo,
    ):
        rule = _rule("pipeline-stale")
        alert_repo.get_rule_by_slug.return_value = rule
        alert_repo.get_active_alert_for_rule.return_value = {
            "id": "existing", "status": "active",
        }

        service._apply_result("stale", _firing("stale", severity="critical"))

        alert_repo.create_alert.assert_not_called()
        alert_repo.update_alert_assets.assert_called_once()
        kwargs = alert_repo.update_alert_assets.call_args.kwargs
        assert kwargs["alert_id"] == "existing"
        assert kwargs["threshold"] == {"k": "v"}

    def test_resolves_alert_when_not_firing_and_alert_active(
        self, service, alert_repo,
    ):
        rule = _rule("pipeline-stale")
        alert_repo.get_rule_by_slug.return_value = rule
        alert_repo.get_active_alert_for_rule.return_value = {"id": "existing"}

        service._apply_result("stale", _not_firing("stale"))

        alert_repo.resolve_alert.assert_called_once_with("existing")
        alert_repo.create_alert.assert_not_called()

    def test_no_op_when_not_firing_and_no_active_alert(self, service, alert_repo):
        rule = _rule("pipeline-stale")
        alert_repo.get_rule_by_slug.return_value = rule
        alert_repo.get_active_alert_for_rule.return_value = None

        service._apply_result("stale", _not_firing("stale"))

        alert_repo.create_alert.assert_not_called()
        alert_repo.resolve_alert.assert_not_called()
        alert_repo.update_alert_assets.assert_not_called()

    def test_disabled_rule_resolves_active_alert_and_skips(self, service, alert_repo):
        rule = _rule("pipeline-stale", enabled=False)
        alert_repo.get_rule_by_slug.return_value = rule

        service._apply_result("stale", _firing("stale"))

        alert_repo.resolve_alerts_for_rule.assert_called_once_with(rule["id"])
        alert_repo.create_alert.assert_not_called()
        alert_repo.get_active_alert_for_rule.assert_not_called()

    def test_missing_rule_logs_warning_and_skips(self, service, alert_repo, caplog):
        alert_repo.get_rule_by_slug.return_value = None

        with caplog.at_level("WARNING"):
            service._apply_result("stale", _firing("stale"))

        assert any("not found" in r.message for r in caplog.records)
        alert_repo.create_alert.assert_not_called()


# ---------------------------------------------------------------------------
# tick
# ---------------------------------------------------------------------------

class TestTick:
    def test_tick_runs_all_three_checks(self, service, watchdog_repo, alert_repo):
        # No rules match -> every _apply_result call warns + exits
        alert_repo.get_rule_by_slug.return_value = None

        service.tick()

        slugs = {c.args[0] for c in alert_repo.get_rule_by_slug.call_args_list}
        assert slugs == {
            "pipeline-stale", "pipeline-last-run-failed", "pipeline-stuck",
        }

    def test_tick_isolates_per_check_crashes(self, service, watchdog_repo, alert_repo, caplog):
        """If _apply_result for one check raises, the other two still run."""
        rule = _rule("pipeline-last-run-failed")

        def lookup(slug):
            if slug == "pipeline-stale":
                raise RuntimeError("boom")
            return rule

        alert_repo.get_rule_by_slug.side_effect = lookup
        alert_repo.get_active_alert_for_rule.return_value = None

        with caplog.at_level("ERROR"):
            service.tick()  # must not raise

        assert any("stale handling failed" in r.message for r in caplog.records)
        # Other two checks still reached get_rule_by_slug
        assert alert_repo.get_rule_by_slug.call_count == 3

    def test_tick_swallows_top_level_failure(self, service, watchdog_repo, caplog):
        """A crash before the check loop must not kill the APScheduler job."""
        watchdog_repo.get_pipeline_schedule_settings.side_effect = RuntimeError("db down")

        with caplog.at_level("ERROR"):
            service.tick()  # must not raise

        assert any("tick failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Orphan recovery
# ---------------------------------------------------------------------------


class TestOrphanRecovery:
    def test_tick_invokes_recovery_with_module_threshold(
        self, service, watchdog_repo, alert_repo,
    ):
        from src.modules.pipeline_watchdog.service import (
            ORPHAN_RECOVERY_THRESHOLD_MINUTES,
        )
        alert_repo.get_rule_by_slug.return_value = None
        service.tick()
        watchdog_repo.recover_orphaned_pending_runs.assert_called_once_with(
            threshold_minutes=ORPHAN_RECOVERY_THRESHOLD_MINUTES,
        )

    def test_tick_logs_when_orphans_recovered(
        self, service, watchdog_repo, alert_repo, caplog,
    ):
        watchdog_repo.recover_orphaned_pending_runs.return_value = 3
        alert_repo.get_rule_by_slug.return_value = None
        with caplog.at_level("WARNING"):
            service.tick()
        assert any(
            "recovered 3 orphaned pending run" in r.message for r in caplog.records
        )

    def test_tick_silent_when_no_orphans(
        self, service, watchdog_repo, alert_repo, caplog,
    ):
        watchdog_repo.recover_orphaned_pending_runs.return_value = 0
        alert_repo.get_rule_by_slug.return_value = None
        with caplog.at_level("WARNING"):
            service.tick()
        assert not any("orphaned pending run" in r.message for r in caplog.records)

    def test_recovery_failure_does_not_skip_other_checks(
        self, service, watchdog_repo, alert_repo, caplog,
    ):
        """Recovery exception must be isolated — alerting checks still run."""
        watchdog_repo.recover_orphaned_pending_runs.side_effect = RuntimeError("db hiccup")
        alert_repo.get_rule_by_slug.return_value = None

        with caplog.at_level("ERROR"):
            service.tick()  # must not raise

        assert any("orphan recovery failed" in r.message for r in caplog.records)
        # All three downstream checks still reached get_rule_by_slug.
        slugs = {c.args[0] for c in alert_repo.get_rule_by_slug.call_args_list}
        assert slugs == {
            "pipeline-stale", "pipeline-last-run-failed", "pipeline-stuck",
        }

    def test_running_recovery_waits_two_max_duration_windows(
        self, service, watchdog_repo, alert_repo,
    ):
        """Recovery must never share a threshold with the 'stuck' alert.

        Both read the same rows, and recovery writes them: at an equal threshold
        every run past the ceiling is already 'failed' before check_stuck looks,
        so the alert can never fire (and a merely-slow run gets killed the
        instant it crosses the ceiling). 90-minute ceiling -> repair at 180.
        """
        watchdog_repo.get_max_expected_duration_minutes.return_value = 90
        alert_repo.get_rule_by_slug.return_value = None

        service.tick()

        watchdog_repo.recover_orphaned_running_runs.assert_called_once_with(
            threshold_minutes=180,
        )
        # Pending recovery is a different failure mode (worker never started) and
        # keeps its own short, fixed window.
        watchdog_repo.recover_orphaned_pending_runs.assert_called_once_with(
            threshold_minutes=5,
        )

    def test_tick_logs_when_running_orphans_recovered(
        self, service, watchdog_repo, alert_repo, caplog,
    ):
        watchdog_repo.recover_orphaned_running_runs.return_value = 2
        alert_repo.get_rule_by_slug.return_value = None
        with caplog.at_level("WARNING"):
            service.tick()
        assert any(
            "recovered 2 orphaned running run" in r.message for r in caplog.records
        )

    def test_running_recovery_failure_does_not_skip_checks(
        self, service, watchdog_repo, alert_repo, caplog,
    ):
        watchdog_repo.recover_orphaned_running_runs.side_effect = RuntimeError("db hiccup")
        alert_repo.get_rule_by_slug.return_value = None

        with caplog.at_level("ERROR"):
            service.tick()  # must not raise

        assert any(
            "running-orphan recovery failed" in r.message for r in caplog.records
        )
        slugs = {c.args[0] for c in alert_repo.get_rule_by_slug.call_args_list}
        assert slugs == {
            "pipeline-stale", "pipeline-last-run-failed", "pipeline-stuck",
        }

    def test_stuck_check_failure_does_not_skip_recovery_or_other_checks(
        self, service, watchdog_repo, alert_repo, caplog,
    ):
        """check_stuck now runs first — a crash there must not eat the rest."""
        watchdog_repo.stuck_runs.side_effect = RuntimeError("db hiccup")
        alert_repo.get_rule_by_slug.return_value = None

        with caplog.at_level("ERROR"):
            service.tick()  # must not raise

        assert any("stuck handling failed" in r.message for r in caplog.records)
        watchdog_repo.recover_orphaned_running_runs.assert_called_once()
        slugs = {c.args[0] for c in alert_repo.get_rule_by_slug.call_args_list}
        assert slugs == {"pipeline-stale", "pipeline-last-run-failed"}


# ---------------------------------------------------------------------------
# check_stuck vs orphan recovery — the two touch the same rows
# ---------------------------------------------------------------------------


def _now():
    return datetime.now(timezone.utc)


def _completed_run(hours_ago, run_id=None):
    finished = _now() - timedelta(hours=hours_ago)
    return {
        "id": run_id or uuid4(), "status": "completed",
        "started_at": finished - timedelta(minutes=10),
        "created_at": finished - timedelta(minutes=10),
        "completed_at": finished, "error_message": None,
    }


def _running_run(age_minutes, run_id=None):
    # CheckResult.run_id is a UUID field — a placeholder string would blow up
    # inside the check rather than at the assertion.
    started = _now() - timedelta(minutes=age_minutes)
    return {
        "id": run_id or uuid4(), "status": "running", "started_at": started,
        "created_at": started, "completed_at": None, "error_message": None,
    }


class FakeWatchdogRepo:
    """In-memory PipelineWatchdogRepository with the one property a MagicMock
    cannot have: recovery MUTATES the rows check_stuck reads.

    That mutation is the whole defect. A canned ``stuck_runs.return_value``
    answers the same regardless of what ran before it, so every existing test
    passed while 'pipeline-stuck' was unfireable in production. Each method here
    reproduces its SQL predicate against one shared list of runs, which makes
    tick()'s ordering observable.
    """

    def __init__(self, runs, max_duration_minutes=60, schedule_enabled=False,
                 schedule_enabled_at=None, schedule_enabled_transition=None,
                 stale_grace_minutes=60):
        self.runs = [dict(r) for r in runs]
        self._max_duration_minutes = max_duration_minutes
        self._schedule_enabled = schedule_enabled
        self._schedule_enabled_at = schedule_enabled_at
        self._schedule_enabled_transition = schedule_enabled_transition
        self._stale_grace_minutes = stale_grace_minutes

    # -- settings ---------------------------------------------------------

    def get_pipeline_schedule_settings(self):
        return ScheduleSettings(
            enabled=self._schedule_enabled, mode="interval", interval_hours=24,
            cron="0 0 * * *", timezone="UTC",
        )

    def get_max_expected_duration_minutes(self):
        return self._max_duration_minutes

    def get_stale_grace_minutes(self):
        return self._stale_grace_minutes

    # -- reads ------------------------------------------------------------

    def last_successful_completed_at(self):
        done = [r["completed_at"] for r in self.runs
                if r["status"] == "completed" and r.get("completed_at")]
        return max(done) if done else None

    def has_run_in_flight(self):
        """WHERE status IN ('pending','running') LIMIT 1.

        Reads the shared run list, so running-orphan recovery at step (d) of
        tick() is visible to check_stale at step (e) — a run recovered to
        'failed' is no longer in flight, and the stale check must see that
        within the same tick rather than one tick later.
        """
        return any(r["status"] in ("pending", "running") for r in self.runs)

    def first_run_created_at(self):
        """MIN(created_at) over pipeline_runs, unfiltered by status.

        Present because check_stale reaches for it whenever the schedule is
        enabled and nothing has succeeded. Omit it and the AttributeError lands
        inside tick()'s run_check, which logs and moves on — so a stale-check
        assertion would pass against a check that never ran.
        """
        created = [r["created_at"] for r in self.runs if r.get("created_at")]
        return min(created) if created else None

    def schedule_enabled_since(self):
        return self._schedule_enabled_at

    def schedule_enabled_transition_at(self):
        """MAX(created_at) over the false->true rows in settings_audit_log.

        Distinct from schedule_enabled_since, which a no-op re-save of the
        setting moves to now; check_stale prefers this one and falls back.
        """
        return self._schedule_enabled_transition

    def last_finished_run(self):
        """ORDER BY completed_at DESC NULLS LAST LIMIT 1, over completed/failed."""
        finished = [r for r in self.runs if r["status"] in ("completed", "failed")]
        if not finished:
            return None
        floor = datetime.min.replace(tzinfo=timezone.utc)
        return max(finished, key=lambda r: r.get("completed_at") or floor)

    def stuck_runs(self, max_duration_minutes):
        """WHERE status IN ('pending','running')
             AND COALESCE(started_at, created_at) < now() - interval."""
        cutoff = _now() - timedelta(minutes=max_duration_minutes)
        rows = [r for r in self.runs
                if r["status"] in ("pending", "running")
                and (r.get("started_at") or r["created_at"]) < cutoff]
        rows.sort(key=lambda r: r.get("started_at") or r["created_at"])
        return [
            dict(r, running_seconds=int(
                (_now() - (r.get("started_at") or r["created_at"])).total_seconds()
            ))
            for r in rows
        ]

    # -- writes -----------------------------------------------------------

    def recover_orphaned_pending_runs(self, threshold_minutes):
        cutoff = _now() - timedelta(minutes=threshold_minutes)
        hit = [r for r in self.runs
               if r["status"] == "pending" and r.get("started_at") is None
               and r["created_at"] < cutoff]
        for row in hit:
            self._fail(row, "Orphaned: pending without start beyond threshold")
        return len(hit)

    def recover_orphaned_running_runs(self, threshold_minutes=None,
                                      max_duration_minutes=None):
        """WHERE status='running' AND started_at < now() - interval.

        Deliberately tolerates the pre-fix ``max_duration_minutes`` kwarg so the
        ordering test below fails for the ordering bug rather than for a
        TypeError that tick()'s try/except would swallow into a no-op recovery —
        which would have made the broken code look correct.
        """
        threshold = (threshold_minutes if threshold_minutes is not None
                     else max_duration_minutes)
        cutoff = _now() - timedelta(minutes=threshold)
        hit = [r for r in self.runs
               if r["status"] == "running" and r.get("started_at")
               and r["started_at"] < cutoff]
        for row in hit:
            self._fail(row, "Orphaned: running beyond max expected duration")
        return len(hit)

    @staticmethod
    def _fail(run, message):
        run["status"] = "failed"
        run["completed_at"] = _now()
        run["error_message"] = message


@pytest.fixture
def rule_serving_alert_repo():
    """Alert repo that returns an enabled rule for whichever slug is asked for."""
    repo = MagicMock()
    repo.get_rule_by_slug.side_effect = lambda slug: {
        "id": f"rule-{slug}", "slug": slug, "enabled": True,
    }
    repo.get_active_alert_for_rule.return_value = None
    repo.create_alert.return_value = {
        "id": "alert-1",
        "created_at": datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
    }
    return repo


def _fired_rule_ids(alert_repo):
    return [c.kwargs["rule_id"] for c in alert_repo.create_alert.call_args_list]


class TestStaleAlertIsReachableThroughTick:
    """check_stale reads more of the repository than any other check.

    Every read has to exist on the fake or the AttributeError lands inside
    tick()'s run_check, which logs it and moves on — so an assertion about a
    quiet outcome would pass against a check that never ran. That is the
    silent-vacuous-pass shape this class was written to prevent (#1166/#1168),
    and it is why these two tests assert through tick() rather than by calling
    check_stale directly.
    """

    def test_a_missed_scheduled_run_fires_through_tick(
        self, rule_serving_alert_repo,
    ):
        repo = FakeWatchdogRepo(
            runs=[_completed_run(hours_ago=50)], schedule_enabled=True,
        )
        service = PipelineWatchdogService(
            watchdog_repo=repo, alert_repo=rule_serving_alert_repo,
        )

        service.tick()

        assert "rule-pipeline-stale" in _fired_rule_ids(rule_serving_alert_repo)

    def test_a_run_in_flight_keeps_the_stale_alert_quiet(
        self, rule_serving_alert_repo,
    ):
        """The division of labour between the two checks, through the real tick.

        The 30-minute run is young enough to survive running-orphan recovery at
        step (d), so it is still in flight when check_stale runs at step (e) —
        and without the in-flight test the 50-hour-old success would fire
        'stale' on a pipeline that is at that moment working.
        """
        repo = FakeWatchdogRepo(
            runs=[_completed_run(hours_ago=50), _running_run(age_minutes=30)],
            schedule_enabled=True,
        )
        service = PipelineWatchdogService(
            watchdog_repo=repo, alert_repo=rule_serving_alert_repo,
        )

        service.tick()

        assert "rule-pipeline-stale" not in _fired_rule_ids(rule_serving_alert_repo)


class TestStuckAlertIsReachable:
    """Regression: running-orphan recovery must not shadow check_stuck (#501/#563)."""

    def test_run_past_max_duration_fires_the_stuck_alert(
        self, rule_serving_alert_repo,
    ):
        """A 90-minute run against a 60-minute ceiling is the alert's whole point.

        Recovery running first, at that same 60-minute threshold, rewrites the
        row to 'failed' — so check_stuck queries pending/running and finds
        nothing, every tick, forever.
        """
        repo = FakeWatchdogRepo(
            runs=[_running_run(age_minutes=90)], max_duration_minutes=60,
        )
        service = PipelineWatchdogService(
            watchdog_repo=repo, alert_repo=rule_serving_alert_repo,
        )

        service.tick()

        assert "rule-pipeline-stuck" in _fired_rule_ids(rule_serving_alert_repo)

    def test_live_run_inside_the_recovery_window_is_left_running(
        self, rule_serving_alert_repo,
    ):
        """Recovery releases trigger_run()'s concurrency guard.

        Killing the row of a run that is merely slow lets the next tick start a
        SECOND pipeline over the first — and the pipeline TRUNCATEs assets.
        """
        repo = FakeWatchdogRepo(
            runs=[_running_run(age_minutes=90)], max_duration_minutes=60,
        )
        service = PipelineWatchdogService(
            watchdog_repo=repo, alert_repo=rule_serving_alert_repo,
        )

        service.tick()

        assert repo.runs[0]["status"] == "running"

    def test_run_past_two_windows_is_recovered_and_reported_failed_same_tick(
        self, rule_serving_alert_repo,
    ):
        """Recovery still lands before check_last_run_failed within one tick.

        That was the original reason recovery ran early; the reorder keeps it —
        a row failed at step (d) must be visible to the check at step (e).
        """
        repo = FakeWatchdogRepo(
            runs=[_running_run(age_minutes=130)], max_duration_minutes=60,
        )
        service = PipelineWatchdogService(
            watchdog_repo=repo, alert_repo=rule_serving_alert_repo,
        )

        service.tick()

        assert repo.runs[0]["status"] == "failed"
        assert "rule-pipeline-last-run-failed" in _fired_rule_ids(
            rule_serving_alert_repo,
        )
