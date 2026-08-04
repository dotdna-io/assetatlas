# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the PipelineWatchdogRepository reads that back the stale
grace period.

The repository's other methods are covered against a live database in
tests/integration/test_pipeline_watchdog_repository.py, which is
@pytest.mark.integration and so does not run in the default suite. These two
reads carry logic that survives a stubbed adapter — which column they read, and
the timezone the value comes back in — so they are pinned here, where the gate
actually runs them.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.modules.pipeline_watchdog.repository import PipelineWatchdogRepository


@pytest.fixture
def db():
    return MagicMock()


@pytest.fixture
def repo(db):
    return PipelineWatchdogRepository(db)


class TestFirstRunCreatedAt:
    def test_returns_the_earliest_run_timestamp(self, repo, db):
        first = datetime(2026, 5, 4, 9, 5, tzinfo=timezone.utc)
        db.execute_query.return_value = [{"ts": first}]
        assert repo.first_run_created_at() == first

    def test_none_when_no_runs_exist(self, repo, db):
        # MIN() over an empty table yields one row holding NULL, not no rows.
        db.execute_query.return_value = [{"ts": None}]
        assert repo.first_run_created_at() is None

    def test_none_when_the_query_returns_nothing(self, repo, db):
        db.execute_query.return_value = []
        assert repo.first_run_created_at() is None

    def test_counts_every_run_not_just_successful_ones(self, repo, db):
        """The anchor is 'when did we start expecting a success', so a run that
        failed still counts — it proves the pipeline was already being asked to
        do work."""
        db.execute_query.return_value = [{"ts": datetime.now(timezone.utc)}]
        repo.first_run_created_at()
        sql = db.execute_query.call_args.args[0]
        assert "pipeline_runs" in sql
        assert "status" not in sql.lower()


class TestScheduleEnabledSince:
    ROW_TS = datetime(2026, 5, 4, 9, 0, 0)  # naive, as the column stores it

    def test_reads_the_pipeline_schedule_enabled_row(self, repo, db):
        db.execute_query.return_value = [{"updated_at": self.ROW_TS}]

        repo.schedule_enabled_since()

        sql, params = (
            db.execute_query.call_args.args[0],
            db.execute_query.call_args.args[1],
        )
        assert "system_settings" in sql
        assert params["category"] == "asset_generation"
        assert params["key"] == "pipeline_schedule_enabled"

    def test_none_when_the_setting_row_is_absent(self, repo, db):
        """A fresh install that never wrote the setting has no row at all."""
        db.execute_query.return_value = []
        assert repo.schedule_enabled_since() is None

    def test_none_when_updated_at_is_null(self, repo, db):
        db.execute_query.return_value = [{"updated_at": None}]
        assert repo.schedule_enabled_since() is None

    def test_naive_column_value_comes_back_as_aware_utc(self, repo, db):
        """system_settings.updated_at is `timestamp WITHOUT time zone`, written
        with NOW(). psycopg2 hands that back naive."""
        db.execute_query.return_value = [{"updated_at": self.ROW_TS}]

        result = repo.schedule_enabled_since()

        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)
        assert result == self.ROW_TS.replace(tzinfo=timezone.utc)

    def test_result_subtracts_against_an_aware_now(self, repo, db):
        """The consequence, stated as the caller sees it.

        Every other timestamp in this module is aware UTC and check_stale does
        `now - anchor`. Hand back the column value unnormalised and that line
        raises `TypeError: can't subtract offset-naive and offset-aware
        datetimes` — inside #1166's run_check, which logs it and moves on. The
        stale check would then do nothing at all, every tick, silently.
        """
        db.execute_query.return_value = [{"updated_at": self.ROW_TS}]

        gap = datetime.now(timezone.utc) - repo.schedule_enabled_since()

        assert gap.total_seconds() > 0

    def test_already_aware_value_is_left_alone(self, repo, db):
        """Defensive: if the column is ever migrated to timestamptz, don't
        stamp UTC over a real offset."""
        aware = datetime(2026, 5, 4, 9, 0, tzinfo=timezone(timedelta(hours=3)))
        db.execute_query.return_value = [{"updated_at": aware}]
        assert repo.schedule_enabled_since() == aware


class TestScheduleEnabledTransitionAt:
    """The last false->true transition of pipeline_schedule_enabled.

    `schedule_enabled_since` reads system_settings.updated_at, which
    settings_manager rewrites on EVERY write — including a re-save that changes
    nothing. On an install with no pipeline_runs rows there is no earlier
    first_run_created_at to shield the anchor, so an admin opening the schedule
    form and pressing Save moves it to now: the firing stale alert resolves and
    re-fires a cadence later with a fresh outbound delivery. Investigating the
    alert makes it disappear.

    settings_audit_log records old_value/new_value per change, so a real
    transition is recoverable and a no-op re-save is not mistaken for one.
    """

    ROW_TS = datetime(2026, 5, 4, 9, 0, 0)  # naive, as the column stores it

    def test_reads_the_false_to_true_transition_only(self, repo, db):
        db.execute_query.return_value = [{"ts": self.ROW_TS}]

        repo.schedule_enabled_transition_at()

        sql = db.execute_query.call_args.args[0]
        params = db.execute_query.call_args.args[1]
        assert "settings_audit_log" in sql
        assert params["category"] == "asset_generation"
        assert params["key"] == "pipeline_schedule_enabled"
        # A re-save writes new_value='true' with old_value='true' — the row
        # exists and must not be read as a transition.
        assert "new_value" in sql and "old_value" in sql
        assert "IS DISTINCT FROM" in sql

    def test_returns_the_most_recent_transition(self, repo, db):
        """MAX(), so a schedule toggled off and on again anchors on the latest
        switch-on rather than the first one this install ever saw."""
        db.execute_query.return_value = [{"ts": self.ROW_TS}]
        assert repo.schedule_enabled_transition_at() == (
            self.ROW_TS.replace(tzinfo=timezone.utc)
        )
        assert "MAX(" in db.execute_query.call_args.args[0].upper()

    def test_none_when_no_transition_was_ever_logged(self, repo, db):
        """An installer that writes system_settings directly via psql produces
        no audit row at all — the caller falls back from there."""
        db.execute_query.return_value = [{"ts": None}]
        assert repo.schedule_enabled_transition_at() is None

    def test_none_when_the_query_returns_nothing(self, repo, db):
        db.execute_query.return_value = []
        assert repo.schedule_enabled_transition_at() is None

    def test_naive_column_value_comes_back_as_aware_utc(self, repo, db):
        """settings_audit_log.created_at is `timestamp WITHOUT time zone`
        DEFAULT now(), exactly like system_settings.updated_at."""
        db.execute_query.return_value = [{"ts": self.ROW_TS}]

        result = repo.schedule_enabled_transition_at()

        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)

    def test_result_subtracts_against_an_aware_now(self, repo, db):
        """check_stale does `now - anchor` with this value. Unstamped, that
        raises TypeError inside run_check, which logs it and moves on — the
        stale check would then do nothing at all, every tick, silently."""
        db.execute_query.return_value = [{"ts": self.ROW_TS}]

        gap = datetime.now(timezone.utc) - repo.schedule_enabled_transition_at()

        assert gap.total_seconds() > 0

    def test_already_aware_value_is_left_alone(self, repo, db):
        aware = datetime(2026, 5, 4, 9, 0, tzinfo=timezone(timedelta(hours=3)))
        db.execute_query.return_value = [{"ts": aware}]
        assert repo.schedule_enabled_transition_at() == aware


class TestHasRunInFlight:
    """A run that started on time and is still going is not stale."""

    def test_true_when_a_pending_or_running_row_exists(self, repo, db):
        db.execute_query.return_value = [{"one": 1}]
        assert repo.has_run_in_flight() is True

    def test_false_when_nothing_is_in_flight(self, repo, db):
        db.execute_query.return_value = []
        assert repo.has_run_in_flight() is False

    def test_asks_only_about_pending_and_running_rows(self, repo, db):
        """Unbounded by duration, unlike stuck_runs: how long the run has been
        going is 'pipeline-stuck' business, at its own threshold and severity."""
        db.execute_query.return_value = []

        repo.has_run_in_flight()

        sql = db.execute_query.call_args.args[0]
        assert "pipeline_runs" in sql
        assert "'pending'" in sql and "'running'" in sql
        assert "make_interval" not in sql


class TestStaleGraceMinutes:
    def test_reads_the_stored_value(self, repo, db):
        db.execute_query.return_value = [
            {"key": "pipeline_stale_grace_minutes", "value": "90"},
        ]
        assert repo.get_stale_grace_minutes() == 90

    def test_falls_back_to_the_schema_default(self, repo, db):
        """Pipeline settings materialise in system_settings on first write, so
        every install that has never touched this knob has no row for it."""
        from src.api.modules.settings.settings_schema import get_default_value

        db.execute_query.return_value = []

        assert repo.get_stale_grace_minutes() == get_default_value(
            "asset_generation", "pipeline_stale_grace_minutes",
        ) == 60
