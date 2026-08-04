# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for pipeline_watchdog.domain."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from src.modules.pipeline_watchdog.domain import (
    CheckResult,
    ScheduleSettings,
    last_expected_fire_at,
)


class TestCheckResult:
    def test_happy_path_fields(self):
        run_id = uuid4()
        cr = CheckResult(
            check_type="stale",
            should_fire=True,
            severity="critical",
            title="t",
            description="d",
            threshold={"gap_seconds": 200},
            run_id=run_id,
        )
        assert cr.check_type == "stale"
        assert cr.should_fire is True
        assert cr.severity == "critical"
        assert cr.threshold == {"gap_seconds": 200}
        assert cr.run_id == run_id

    def test_run_id_optional(self):
        cr = CheckResult(
            check_type="stale", should_fire=False, severity="critical",
            title="", description="", threshold={},
        )
        assert cr.run_id is None

    def test_invalid_check_type_rejected(self):
        with pytest.raises(ValueError):
            CheckResult(
                check_type="unknown", should_fire=True, severity="high",
                title="t", description="d", threshold={},
            )


class TestLastExpectedFireAt:
    """The most recent moment a run should have STARTED.

    This replaces the expected-cadence proxy. Sizing staleness by the schedule's
    longest quiet stretch was too blunt for a non-uniform schedule: ``0 6 * * 1-5``
    has a 72 h Friday-to-Monday gap, so a 2x-cadence window meant 144 h — a
    pipeline that died after Monday's run went unreported until Sunday. Asking
    "when was a run last due" costs one missed fire whatever the schedule shape.
    """

    UTC = timezone.utc
    MONDAY = datetime(2026, 5, 4, tzinfo=UTC)  # 2026-05-04 is a Monday

    @classmethod
    def _at(cls, day_offset, hour, minute=0):
        return cls.MONDAY + timedelta(days=day_offset, hours=hour, minutes=minute)

    @staticmethod
    def _settings(mode="cron", cron="0 6 * * 1-5", interval_hours=24,
                  enabled=True, tz="UTC"):
        return ScheduleSettings(
            enabled=enabled, mode=mode, interval_hours=interval_hours,
            cron=cron, timezone=tz,
        )

    @staticmethod
    def _fire_at(settings, when, reference=None):
        with patch("src.modules.pipeline_watchdog.domain._now") as now_mock:
            now_mock.side_effect = lambda tz: when.astimezone(ZoneInfo(tz))
            return last_expected_fire_at(settings, reference or when)

    # -- cron -------------------------------------------------------------

    def test_cron_returns_the_most_recent_fire_before_now(self):
        assert self._fire_at(
            self._settings(), self._at(1, 7, 30),
        ) == self._at(1, 6)

    def test_cron_on_a_weekend_reaches_back_to_fridays_fire(self):
        """Saturday's answer is Friday 06:00 — a fire that already succeeded.

        This is what keeps the weekend quiet without a 2x fudge: there is no
        Saturday fire to miss, so nothing is overdue.
        """
        assert self._fire_at(
            self._settings(), self._at(5, 10),
        ) == self._at(4, 6)

    def test_cron_at_the_fire_minute_reports_the_previous_one(self):
        """06:00 sharp is the fire that is only just due, not one that is late.

        croniter's get_prev is strictly-before, and that is the behaviour we
        want: a run triggered this minute has not had a chance to complete.
        """
        assert self._fire_at(
            self._settings(), self._at(1, 6),
        ) == self._at(0, 6)

    def test_cron_ignores_the_reference(self):
        """A cron schedule is absolute: when the last run succeeded says nothing
        about when the next one was due."""
        assert self._fire_at(
            self._settings(), self._at(1, 7, 30),
            reference=self._at(-30, 0),
        ) == self._at(1, 6)

    def test_cron_result_is_aware_and_subtracts_against_utc_now(self):
        """check_stale compares this against datetime.now(timezone.utc).

        A naive return raises `TypeError: can't subtract offset-naive and
        offset-aware datetimes` inside run_check, which logs and moves on — the
        stale alert would then do nothing at all, every tick.
        """
        fire = self._fire_at(
            self._settings(tz="America/New_York"), self._at(1, 14),
        )
        assert fire.tzinfo is not None
        assert (datetime.now(self.UTC) - fire).total_seconds() != 0

    # -- interval ---------------------------------------------------------

    def test_interval_is_one_period_past_the_reference(self):
        """No absolute schedule exists to consult — APScheduler's interval
        trigger re-anchors on every process restart — so the reference (the last
        success, or the first moment one was expected) is the only honest
        starting point."""
        reference = self._at(0, 6, 5)
        assert self._fire_at(
            self._settings(mode="interval", interval_hours=24),
            self._at(1, 9), reference=reference,
        ) == reference + timedelta(hours=24)

    def test_interval_honours_a_non_default_period(self):
        reference = self._at(0, 6, 5)
        assert self._fire_at(
            self._settings(mode="interval", interval_hours=6),
            self._at(0, 20), reference=reference,
        ) == reference + timedelta(hours=6)

    # -- unknowable -------------------------------------------------------

    def test_disabled_schedule_has_no_expected_fire(self):
        assert self._fire_at(
            self._settings(enabled=False), self._at(1, 7, 30),
        ) is None

    def test_unparseable_cron_returns_none_rather_than_raising(self):
        """`pipeline_schedule_cron` is a free-text setting with no validator.

        An exception here is contained by run_check, which logs it and moves on
        — leaving the stale check silently dead for as long as the typo lives.
        A None is a branch the caller can report instead.
        """
        assert self._fire_at(
            self._settings(cron="every second tuesday"), self._at(1, 7, 30),
        ) is None

    def test_unknown_timezone_returns_none_rather_than_raising(self):
        settings = self._settings(tz="Mars/Olympus")
        assert last_expected_fire_at(settings, self._at(1, 7, 30)) is None
