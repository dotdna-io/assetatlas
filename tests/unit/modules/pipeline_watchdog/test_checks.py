# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the three watchdog check functions."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from src.modules.pipeline_watchdog.domain import ScheduleSettings
from src.modules.pipeline_watchdog.repository import PipelineWatchdogRepository
from src.modules.pipeline_watchdog.service import (
    check_last_run_failed,
    check_stale,
    check_stuck,
)

UTC = timezone.utc


@pytest.fixture
def repo():
    return MagicMock()


def _settings(enabled=True, mode="interval", interval_hours=24,
              cron="0 0 * * *", timezone_="UTC"):
    return ScheduleSettings(
        enabled=enabled, mode=mode, interval_hours=interval_hours,
        cron=cron, timezone=timezone_,
    )


@contextmanager
def frozen(now):
    """Freeze both clocks check_stale consults.

    The service reads `datetime.now(timezone.utc)`; the cron branch of
    `last_expected_fire_at` reads `domain._now(tz)`. A test that pins only one
    of them measures a real elapsed interval against a fixed schedule.
    """
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)

    with patch("src.modules.pipeline_watchdog.service.datetime", _Frozen), \
            patch("src.modules.pipeline_watchdog.domain._now",
                  lambda tz: now.astimezone(ZoneInfo(tz))):
        yield


def _stale_repo(last_success=None, first_run=None, enabled_transition=None,
                enabled_since=None, in_flight=False, grace_minutes=60):
    repo = MagicMock()
    repo.has_run_in_flight.return_value = in_flight
    repo.last_successful_completed_at.return_value = last_success
    repo.first_run_created_at.return_value = first_run
    repo.schedule_enabled_transition_at.return_value = enabled_transition
    repo.schedule_enabled_since.return_value = enabled_since
    repo.get_stale_grace_minutes.return_value = grace_minutes
    return repo


# ---------------------------------------------------------------------------
# check_stale
# ---------------------------------------------------------------------------

class TestCheckStaleOnAMissedScheduledRun:
    """'pipeline-stale' asks whether a scheduled run came due and nothing has
    succeeded since — not whether the pipeline has been quiet for 2x its
    longest gap.

    The proxy was too blunt for a non-uniform schedule. ``0 6 * * 1-5`` — the
    common production shape — has a 72 h Friday-to-Monday quiet stretch, so the
    threshold was 144 h: a pipeline that died after Monday's run was not
    reported until Sunday, and one that died after Friday's not until the
    following Thursday. Detection now costs one missed run plus the grace
    window, whatever shape the week has.
    """

    CRON = "0 6 * * 1-5"          # weekday mornings, 06:00 UTC
    GRACE_MINUTES = 60
    MONDAY = datetime(2026, 5, 4, tzinfo=UTC)   # 2026-05-04 is a Monday

    @classmethod
    def _at(cls, day_offset, hour, minute=0):
        return cls.MONDAY + timedelta(days=day_offset, hours=hour, minutes=minute)

    def _verdict(self, now, last_success):
        # An install old enough that the anchor is never the binding constraint.
        repo = _stale_repo(
            last_success=last_success,
            first_run=self._at(-30, 6),
            enabled_transition=self._at(-30, 5),
            grace_minutes=self.GRACE_MINUTES,
        )
        with frozen(now):
            return check_stale(repo, _settings(mode="cron", cron=self.CRON))

    def test_the_morning_after_a_missed_weekday_run_fires(self):
        """Tuesday 06:00 came due and nothing has succeeded since Monday.

        Old behaviour: quiet — 25 h against a 144 h threshold. It stayed quiet
        until Sunday.
        """
        result = self._verdict(self._at(1, 7, 30), self._at(0, 6, 5))
        assert result.should_fire is True
        assert result.severity == "critical"
        assert result.title == "Asset generation pipeline is stale"

    def test_inside_the_grace_window_stays_quiet(self):
        """06:30 against a 06:00 fire: the run may still be starting.

        The grace is what makes the sharper comparison safe to ship — without
        it, a run whose trigger is a few minutes late alerts every cycle.
        """
        result = self._verdict(self._at(1, 6, 30), self._at(0, 6, 5))
        assert result.should_fire is False
        assert result.threshold["reason"] == "within_grace"

    def test_a_weekend_with_no_scheduled_run_stays_quiet(self):
        """Saturday has no fire of its own; Friday's is the most recent one, and
        it succeeded. This is the #1168 weekend false-critical, which must stay
        fixed now that the 2x cadence fudge is gone."""
        result = self._verdict(self._at(5, 10), self._at(4, 6, 5))
        assert result.should_fire is False
        assert result.threshold["reason"] == "success_since_last_fire"

    def test_monday_morning_after_a_dead_weekend_fires(self):
        """Monday 06:00 came due and the last success was Friday.

        Old behaviour: quiet — 73 h against 144 h — and it stayed quiet until
        Thursday, five more missed runs later.
        """
        result = self._verdict(self._at(7, 7, 30), self._at(4, 6, 5))
        assert result.should_fire is True
        assert result.title == "Asset generation pipeline is stale"


class TestCheckStaleWhileARunIsInFlight:
    """A run that started on time and is still going is not stale."""

    def test_a_run_in_flight_suppresses_the_alert(self):
        """Otherwise a long run trips 'stale' while it is actively working: the
        scheduled fire has passed, the run has not completed, and nothing has
        succeeded since. 'pipeline-stuck' owns duration, at
        pipeline_max_expected_duration_minutes and its own severity.
        """
        repo = _stale_repo(
            in_flight=True,
            last_success=datetime.now(UTC) - timedelta(days=30),
            first_run=datetime.now(UTC) - timedelta(days=60),
        )
        result = check_stale(repo, _settings(interval_hours=24))

        assert result.should_fire is False
        assert result.threshold["reason"] == "run_in_flight"

    def test_the_in_flight_test_precedes_every_read_it_can_skip(self):
        repo = _stale_repo(in_flight=True)

        check_stale(repo, _settings(interval_hours=24))

        repo.last_successful_completed_at.assert_not_called()
        repo.first_run_created_at.assert_not_called()

    def test_nothing_in_flight_leaves_the_check_to_do_its_job(self):
        """The suppression must be a fact about the pipeline, not a way out."""
        repo = _stale_repo(
            in_flight=False,
            last_success=datetime.now(UTC) - timedelta(days=30),
            first_run=datetime.now(UTC) - timedelta(days=60),
        )
        assert check_stale(repo, _settings(interval_hours=24)).should_fire is True


class TestCheckStaleOnAFreshInstall:
    """A fire that came due before the schedule existed was never owed."""

    CRON = "0 6 * * *"                              # daily, 06:00 UTC
    ENABLED_AT = datetime(2026, 5, 5, 9, 0, tzinfo=UTC)   # Tuesday morning

    def _verdict(self, now, **kwargs):
        repo = _stale_repo(enabled_transition=self.ENABLED_AT, **kwargs)
        with frozen(now):
            return check_stale(repo, _settings(mode="cron", cron=self.CRON))

    def test_a_fire_that_predates_the_schedule_does_not_count(self):
        """Enable at 09:00 with a 06:00 cron and get_prev answers 06:00 — three
        hours before the schedule existed. Without the eligibility gate a fresh
        install raises `critical`, and dispatches outbound deliveries, the
        instant an admin switches the schedule on."""
        result = self._verdict(self.ENABLED_AT + timedelta(minutes=30))
        assert result.should_fire is False
        assert result.threshold["reason"] == "not_due_yet"

    def test_the_first_fire_after_the_schedule_was_enabled_counts(self):
        """Next morning, 06:00 came due while the schedule was on and nothing
        has ever succeeded — that is a real setup fault."""
        result = self._verdict(
            self.ENABLED_AT.replace(day=6, hour=7, minute=30),
        )
        assert result.should_fire is True
        assert result.title == "Asset generation pipeline has never successfully run"

    def test_still_inside_the_grace_of_that_first_fire_stays_quiet(self):
        result = self._verdict(
            self.ENABLED_AT.replace(day=6, hour=6, minute=30),
        )
        assert result.should_fire is False
        assert result.threshold["reason"] == "within_grace"

    def test_no_anchor_at_all_does_not_fire(self):
        """Nothing to measure from is not evidence of a fault."""
        repo = _stale_repo()
        with frozen(self.ENABLED_AT):
            result = check_stale(repo, _settings(mode="cron", cron=self.CRON))
        assert result.should_fire is False
        assert result.threshold["reason"] == "no_anchor"


class TestCheckStaleWhenAnExistingInstallSwitchesTheScheduleOn:
    """Run history must not defeat the eligibility gate.

    The gate rejects a fire that came due before the schedule was switched on.
    Gating it on `anchor` — the EARLIER of the switch-on and the first run ever
    attempted — makes it a fresh-install-only guard: every deployment that has
    ever run the pipeline (manually, or under a schedule that was later turned
    off) has an `anchor` years old, so the gate passes the very fire it exists
    to reject and switching the schedule on raises `critical`, with outbound
    deliveries to every routed channel, within one tick.
    """

    CRON = "0 6 * * *"                                    # daily, 06:00 UTC
    ENABLED_AT = datetime(2026, 5, 5, 9, 0, tzinfo=UTC)   # switched on at 09:00
    OLD_HISTORY = datetime(2025, 1, 1, tzinfo=UTC)

    def _verdict(self, now, last_success, settings):
        repo = _stale_repo(
            last_success=last_success,
            first_run=self.OLD_HISTORY,
            enabled_transition=self.ENABLED_AT,
        )
        with frozen(now):
            return check_stale(repo, settings)

    def test_cron_this_mornings_fire_predates_the_switch_on(self):
        """09:05, five minutes after switching a 06:00 cron on: 06:00 came due
        three hours before there was a schedule to miss."""
        result = self._verdict(
            self.ENABLED_AT + timedelta(minutes=5),
            last_success=self.ENABLED_AT - timedelta(days=1),
            settings=_settings(mode="cron", cron=self.CRON),
        )
        assert result.should_fire is False
        assert result.threshold["reason"] == "not_due_yet"

    def test_cron_the_first_fire_after_the_switch_on_still_counts(self):
        """The gate must not become a permanent excuse: next morning's 06:00 is
        owed, and nothing has succeeded since."""
        result = self._verdict(
            self.ENABLED_AT + timedelta(days=1),          # next day 09:00
            last_success=self.ENABLED_AT - timedelta(days=1),
            settings=_settings(mode="cron", cron=self.CRON),
        )
        assert result.should_fire is True

    def test_interval_counts_from_the_switch_on_not_a_stale_success(self):
        """Same install in interval mode: the last success is a month old
        because the schedule was off. The first run is due one interval after
        the switch-on, not one interval after that success."""
        result = self._verdict(
            self.ENABLED_AT + timedelta(minutes=5),
            last_success=self.ENABLED_AT - timedelta(days=30),
            settings=_settings(interval_hours=24),
        )
        assert result.should_fire is False
        assert result.threshold["reason"] == "within_grace"

    def test_interval_fires_one_interval_after_the_switch_on(self):
        result = self._verdict(
            self.ENABLED_AT + timedelta(hours=25, minutes=30),
            last_success=self.ENABLED_AT - timedelta(days=30),
            settings=_settings(interval_hours=24),
        )
        assert result.should_fire is True
        assert result.threshold["last_expected_fire"] == (
            self.ENABLED_AT + timedelta(hours=24)
        ).isoformat()


class TestCheckStaleAnchorSurvivesASettingsResave:
    """The anchor must be a transition, not the last time anything was written.

    settings_manager stamps system_settings.updated_at on EVERY write, including
    a re-save that changes nothing. On an install with no pipeline_runs rows —
    nothing for first_run_created_at to shield the anchor with — an admin who
    opens the schedule form and presses Save moves the anchor to now: the firing
    alert resolves and re-fires a cadence later with a fresh outbound delivery.
    Investigating the alert makes it vanish.
    """

    NOW = datetime(2026, 5, 8, 12, 0, tzinfo=UTC)
    ENABLED_AT = NOW - timedelta(hours=49)      # 24 h interval + grace, twice over

    def _verdict(self, **kwargs):
        with frozen(self.NOW):
            return check_stale(_stale_repo(**kwargs), _settings(interval_hours=24))

    def test_a_no_op_resave_does_not_move_the_anchor(self):
        result = self._verdict(
            enabled_transition=self.ENABLED_AT,
            enabled_since=self.NOW - timedelta(minutes=1),   # the re-save
        )
        assert result.should_fire is True
        assert result.threshold["anchor_at"] == self.ENABLED_AT.isoformat()
        assert result.threshold["anchor_source"] == "schedule_enabled_transition"

    def test_the_updated_at_fallback_still_works_without_an_audit_row(self):
        """An installer writing system_settings directly via psql logs no audit
        row at all, and neither did any change made before this fix shipped."""
        result = self._verdict(enabled_since=self.ENABLED_AT)
        assert result.should_fire is True
        assert result.threshold["anchor_source"] == "schedule_enabled_since"

    def test_the_first_run_attempt_is_the_last_fallback(self):
        result = self._verdict(first_run=self.ENABLED_AT)
        assert result.should_fire is True
        assert result.threshold["anchor_source"] == "first_run_created_at"

    def test_the_anchor_is_the_earlier_of_run_and_schedule(self):
        """'How long have we been expecting a success' — a run attempted before
        the current switch-on still counts towards that."""
        result = self._verdict(
            first_run=self.ENABLED_AT - timedelta(hours=10),
            enabled_transition=self.ENABLED_AT,
        )
        assert result.threshold["anchor_source"] == "first_run_created_at"

    def test_the_anchor_is_the_earlier_source_the_other_way_round(self):
        result = self._verdict(
            first_run=self.ENABLED_AT + timedelta(hours=1),
            enabled_transition=self.ENABLED_AT,
        )
        assert result.threshold["anchor_source"] == "schedule_enabled_transition"


class TestNaiveSettingsTimestampsReachCheckStale:
    """Both settings-table anchors are `timestamp WITHOUT time zone`.

    settings_audit_log.created_at and system_settings.updated_at both come back
    naive from psycopg2, and check_stale compares them against an aware UTC now.
    Unstamped, that raises `TypeError: can't subtract offset-naive and
    offset-aware datetimes` inside run_check, which logs it and moves on — the
    stale check would then do nothing at all, every tick, silently.

    So this drives a REAL repository over a stubbed adapter: remove either
    stamp and these fail where a repository-level test alone would not prove
    the value survives the trip.
    """

    NOW = datetime(2026, 5, 8, 12, 0, tzinfo=UTC)
    NAIVE_ANCHOR = datetime(2026, 5, 6, 9, 0)   # as the columns store it

    def _repo_returning(self, audit_ts=None, updated_at=None):
        def execute_query(sql, params=None):
            if "settings_audit_log" in sql:
                return [{"ts": audit_ts}]
            if "updated_at" in sql:
                return [{"updated_at": updated_at}]
            if "system_settings" in sql:
                return []          # settings fall back to the schema defaults
            if "pipeline_runs" in sql:
                return []          # no runs: no success, nothing in flight
            raise AssertionError(f"unexpected query: {sql}")

        db = MagicMock()
        db.execute_query.side_effect = execute_query
        return PipelineWatchdogRepository(db)

    def _fires(self, **kwargs):
        with frozen(self.NOW):
            return check_stale(
                self._repo_returning(**kwargs), _settings(interval_hours=24),
            ).should_fire

    def test_a_naive_audit_timestamp_survives_the_trip(self):
        assert self._fires(audit_ts=self.NAIVE_ANCHOR) is True

    def test_a_naive_updated_at_survives_the_trip(self):
        assert self._fires(updated_at=self.NAIVE_ANCHOR) is True


class TestCheckStaleInIntervalMode:
    """Interval mode has no absolute schedule to consult — APScheduler's
    interval trigger re-anchors on every process restart — so the last success
    is the only honest reference point.
    """

    NOW = datetime(2026, 5, 8, 12, 0, tzinfo=UTC)

    def _verdict(self, last_success_hours_ago, interval_hours=24, grace=60):
        repo = _stale_repo(
            last_success=self.NOW - timedelta(hours=last_success_hours_ago),
            first_run=self.NOW - timedelta(days=90),
            grace_minutes=grace,
        )
        with frozen(self.NOW):
            return check_stale(repo, _settings(interval_hours=interval_hours))

    def test_fires_one_interval_plus_grace_after_the_last_success(self):
        """A 24 h schedule is reported at ~25 h, not at the old 48 h."""
        assert self._verdict(25.5).should_fire is True

    def test_quiet_at_one_interval_plus_part_of_the_grace(self):
        assert self._verdict(24.5).should_fire is False

    def test_quiet_before_the_next_run_is_even_due(self):
        assert self._verdict(6).should_fire is False

    def test_the_old_two_cadence_window_no_longer_gates_the_alert(self):
        """26 h into a 24 h schedule used to be 22 h short of the threshold."""
        result = self._verdict(26)
        assert result.should_fire is True
        assert result.threshold["overdue_seconds"] == int(3600)  # 26 h - (24 + 1)

    def test_a_shorter_interval_is_reported_sooner(self):
        assert self._verdict(2.5, interval_hours=1).should_fire is True
        assert self._verdict(1.5, interval_hours=1).should_fire is False


class TestCheckStaleGraceIsASetting:
    NOW = datetime(2026, 5, 8, 12, 0, tzinfo=UTC)

    def _verdict(self, grace_minutes):
        repo = _stale_repo(
            last_success=self.NOW - timedelta(hours=26),
            first_run=self.NOW - timedelta(days=90),
            grace_minutes=grace_minutes,
        )
        with frozen(self.NOW):
            return check_stale(repo, _settings(interval_hours=24))

    def test_a_wider_grace_holds_the_alert_back(self):
        """An install whose runs routinely start late can buy quiet without
        giving up the missed-run semantics."""
        assert self._verdict(60).should_fire is True
        assert self._verdict(180).should_fire is False

    def test_the_configured_grace_is_reported(self):
        """26 h into a 24 h schedule is 30 minutes past a 90-minute grace."""
        result = self._verdict(90)
        assert result.should_fire is True
        assert result.threshold["grace_minutes"] == 90
        assert result.threshold["overdue_seconds"] == 1800


class TestCheckStaleThreshold:
    """The card has to explain why it fired now and not yesterday."""

    NOW = datetime(2026, 5, 8, 12, 0, tzinfo=UTC)
    LAST_SUCCESS = NOW - timedelta(hours=26)
    FIRST_RUN = NOW - timedelta(days=90)

    @pytest.fixture
    def result(self):
        repo = _stale_repo(
            last_success=self.LAST_SUCCESS, first_run=self.FIRST_RUN,
            grace_minutes=60,
        )
        with frozen(self.NOW):
            return check_stale(repo, _settings(interval_hours=24))

    def test_carries_the_fire_that_was_missed(self, result):
        assert result.threshold["last_expected_fire"] == (
            self.LAST_SUCCESS + timedelta(hours=24)
        ).isoformat()

    def test_carries_the_grace_the_anchor_and_the_overdue_seconds(self, result):
        assert result.threshold["grace_minutes"] == 60
        assert result.threshold["last_successful_at"] == self.LAST_SUCCESS.isoformat()
        assert result.threshold["anchor_at"] == self.FIRST_RUN.isoformat()
        assert result.threshold["anchor_source"] == "first_run_created_at"
        assert result.threshold["overdue_seconds"] == 3600

    def test_the_description_names_the_run_that_was_due(self, result):
        assert "2026-05-08 10:00" in result.description

    def test_the_two_firing_paths_keep_distinct_titles(self):
        """One comparison, two stories: 'never ran' is a setup problem, 'stale'
        is a working pipeline that stopped."""
        never = _stale_repo(first_run=self.FIRST_RUN, grace_minutes=60)
        stale = _stale_repo(
            last_success=self.LAST_SUCCESS, first_run=self.FIRST_RUN,
            grace_minutes=60,
        )
        with frozen(self.NOW):
            never_result = check_stale(never, _settings(interval_hours=24))
            stale_result = check_stale(stale, _settings(interval_hours=24))

        assert never_result.should_fire and stale_result.should_fire
        assert never_result.title == (
            "Asset generation pipeline has never successfully run"
        )
        assert stale_result.title == "Asset generation pipeline is stale"
        assert never_result.threshold["last_successful_at"] is None


class TestCheckStaleNegativeBranches:
    """Every negative branch must return a not-firing CheckResult.

    A standing alert is auto-resolved by exactly that; an early `return None`
    would strand a `critical` alert on the dashboard forever.
    """

    NOW = datetime(2026, 5, 8, 12, 0, tzinfo=UTC)

    @staticmethod
    def _assert_not_firing(result, reason):
        assert result.should_fire is False
        assert result.check_type == "stale"
        assert result.severity == "critical"
        assert result.threshold["reason"] == reason

    def test_schedule_disabled(self, repo):
        result = check_stale(repo, _settings(enabled=False))
        self._assert_not_firing(result, "schedule_disabled")
        # Nothing to compute when the schedule is off.
        repo.has_run_in_flight.assert_not_called()
        repo.last_successful_completed_at.assert_not_called()

    def test_run_in_flight(self):
        result = check_stale(_stale_repo(in_flight=True), _settings())
        self._assert_not_firing(result, "run_in_flight")

    def test_no_anchor(self):
        result = check_stale(_stale_repo(), _settings())
        self._assert_not_firing(result, "no_anchor")

    def test_not_due_yet(self):
        with frozen(self.NOW):
            result = check_stale(
                _stale_repo(enabled_transition=self.NOW - timedelta(minutes=5)),
                _settings(mode="cron", cron="0 6 * * *"),
            )
        self._assert_not_firing(result, "not_due_yet")

    def test_within_grace(self):
        with frozen(self.NOW):
            result = check_stale(
                _stale_repo(last_success=self.NOW - timedelta(hours=24, minutes=30),
                            first_run=self.NOW - timedelta(days=90)),
                _settings(interval_hours=24),
            )
        self._assert_not_firing(result, "within_grace")

    def test_success_since_last_fire(self):
        with frozen(self.NOW):
            result = check_stale(
                _stale_repo(last_success=self.NOW - timedelta(hours=1),
                            first_run=self.NOW - timedelta(days=90)),
                _settings(mode="cron", cron="0 6 * * *"),
            )
        self._assert_not_firing(result, "success_since_last_fire")

    def test_unreadable_schedule(self):
        """`pipeline_schedule_cron` is a free-text setting with no validator.

        This has to be a branch rather than an escaping exception for the same
        reason #1168 replaced an `assert`: run_check contains the raise, logs
        it, and moves on — leaving the stale check silently dead while looking
        healthy. It must also leave no standing alert behind.
        """
        with frozen(self.NOW):
            result = check_stale(
                _stale_repo(first_run=self.NOW - timedelta(days=90)),
                _settings(mode="cron", cron="every other tuesday"),
            )
        self._assert_not_firing(result, "schedule_unreadable")

    def test_unreadable_schedule_survives_python_dash_o(self):
        """The same branch, stated as the deployment sees it: an `assert` would
        compile away under `python -O` and let a None reach the arithmetic."""
        settings = _settings(mode="cron", cron="", timezone_="Mars/Olympus")
        with frozen(self.NOW):
            result = check_stale(
                _stale_repo(first_run=self.NOW - timedelta(days=90)), settings,
            )
        self._assert_not_firing(result, "schedule_unreadable")


class TestAnUnreadableScheduleLeavesATrail:
    """Returning a branch is right; returning it silently was not.

    A not-firing result's threshold is discarded — only a firing result's is
    persisted onto the alert row — so `reason` reaches no human, and
    _apply_result resolves whatever stale alert was standing. Unlogged, a typo
    in either free-text setting switches the `critical` stale check off, clears
    what it had been reporting, and an operator sees an alert vanish into a
    quiet dashboard.
    """

    NOW = datetime(2026, 5, 8, 12, 0, tzinfo=UTC)

    def _check(self, cron="every other tuesday", timezone_="UTC"):
        with frozen(self.NOW):
            return check_stale(
                _stale_repo(first_run=self.NOW - timedelta(days=90)),
                _settings(mode="cron", cron=cron, timezone_=timezone_),
            )

    @staticmethod
    def _warnings(caplog):
        return [r for r in caplog.records if "unreadable" in r.message]

    def test_the_branch_warns(self, caplog):
        with caplog.at_level("WARNING"):
            result = self._check()

        assert result.threshold["reason"] == "schedule_unreadable"
        warnings = self._warnings(caplog)
        assert len(warnings) == 1
        assert warnings[0].levelname == "WARNING"

    def test_the_warning_names_both_settings_and_their_values(self, caplog):
        """Actionable without a debugger: which of the two free-text settings to
        retype, and what it currently holds. Repr'd, so an empty cron reads as
        `''` rather than as a truncated log line."""
        with caplog.at_level("WARNING"):
            self._check(cron="", timezone_="Mars/Olympus")

        message = self._warnings(caplog)[0].message
        assert "pipeline_schedule_cron=''" in message
        assert "pipeline_schedule_timezone='Mars/Olympus'" in message

    def test_it_warns_on_every_tick_and_not_only_the_first(self, caplog):
        """A line emitted once is missed by everyone who starts reading logs
        after the incident began — which is everyone. It also matches the volume
        the raising behaviour it replaced produced."""
        with caplog.at_level("WARNING"):
            self._check()
            self._check()

        assert len(self._warnings(caplog)) == 2

    def test_the_normal_operating_states_stay_silent(self, caplog):
        """Every other negative branch is a fine thing for the pipeline to be
        doing; warning about them every five minutes would bury the one line
        that matters. `no_anchor` is in this list deliberately — it is 'nothing
        has happened yet' rather than a misconfiguration, it names no setting an
        operator could act on, and it heals as soon as a run row exists.
        """
        old = self.NOW - timedelta(days=90)
        quiet = [
            (_stale_repo(), _settings(enabled=False)),
            (_stale_repo(in_flight=True), _settings()),
            (_stale_repo(), _settings()),
            (_stale_repo(enabled_transition=self.NOW - timedelta(minutes=5)),
             _settings(mode="cron", cron="0 6 * * *")),
            (_stale_repo(last_success=self.NOW - timedelta(hours=24, minutes=30),
                         first_run=old), _settings(interval_hours=24)),
            (_stale_repo(last_success=self.NOW - timedelta(hours=1),
                         first_run=old),
             _settings(mode="cron", cron="0 6 * * *")),
        ]

        with caplog.at_level("WARNING"), frozen(self.NOW):
            reasons = [check_stale(r, s).threshold["reason"] for r, s in quiet]

        # Named, so a branch that stops being reachable cannot pass by silence.
        assert reasons == [
            "schedule_disabled", "run_in_flight", "no_anchor", "not_due_yet",
            "within_grace", "success_since_last_fire",
        ]
        assert [r.message for r in caplog.records] == []


# ---------------------------------------------------------------------------
# check_last_run_failed
# ---------------------------------------------------------------------------

class TestCheckLastRunFailed:
    def test_no_finished_runs(self, repo):
        repo.last_finished_run.return_value = None
        result = check_last_run_failed(repo)
        assert result.should_fire is False

    def test_last_finished_completed(self, repo):
        repo.last_finished_run.return_value = {
            "id": uuid4(), "status": "completed",
            "completed_at": datetime.now(timezone.utc), "error_message": None,
        }
        result = check_last_run_failed(repo)
        assert result.should_fire is False

    def test_last_finished_failed(self, repo):
        run_id = uuid4()
        repo.last_finished_run.return_value = {
            "id": run_id, "status": "failed",
            "completed_at": datetime.now(timezone.utc),
            "error_message": "boom",
        }
        result = check_last_run_failed(repo)
        assert result.should_fire is True
        assert result.severity == "high"
        assert result.run_id == run_id
        assert result.threshold["run_id"] == str(run_id)
        assert "boom" in result.threshold["error_message"]

    def test_error_message_truncated_to_500(self, repo):
        repo.last_finished_run.return_value = {
            "id": uuid4(), "status": "failed",
            "completed_at": datetime.now(timezone.utc),
            "error_message": "x" * 2000,
        }
        result = check_last_run_failed(repo)
        assert len(result.threshold["error_message"]) == 500


# ---------------------------------------------------------------------------
# check_stuck
# ---------------------------------------------------------------------------

class TestCheckStuck:
    def test_no_stuck_runs(self, repo):
        repo.stuck_runs.return_value = []
        result = check_stuck(repo, max_duration_minutes=240)
        assert result.should_fire is False

    def test_stuck_running(self, repo):
        run_id = uuid4()
        repo.stuck_runs.return_value = [{
            "id": run_id, "status": "running",
            "started_at": datetime.now(timezone.utc) - timedelta(minutes=300),
            "created_at": datetime.now(timezone.utc) - timedelta(minutes=300),
            "running_seconds": 18000,
        }]
        result = check_stuck(repo, max_duration_minutes=240)
        assert result.should_fire is True
        assert result.severity == "high"
        assert result.run_id == run_id
        assert result.threshold["max_expected_duration_minutes"] == 240
        assert result.threshold["running_seconds"] == 18000

    def test_stuck_pending_uses_created_at_in_threshold(self, repo):
        run_id = uuid4()
        created = datetime.now(timezone.utc) - timedelta(minutes=500)
        repo.stuck_runs.return_value = [{
            "id": run_id, "status": "pending",
            "started_at": None, "created_at": created,
            "running_seconds": 30000,
        }]
        result = check_stuck(repo, max_duration_minutes=240)
        assert result.should_fire is True
        # started_at is null; threshold reports created_at as the age anchor
        assert result.threshold["started_at"] is None
