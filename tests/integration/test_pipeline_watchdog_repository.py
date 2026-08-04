# SPDX-License-Identifier: AGPL-3.0-or-later
"""Integration tests for PipelineWatchdogRepository."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.modules.pipeline_watchdog.repository import PipelineWatchdogRepository


def _insert_run(db, status, started_at=None, completed_at=None, duration_seconds=None,
                created_at=None, error_message=None):
    """Insert a pipeline_runs row and return its id."""
    run_id = str(uuid.uuid4())
    db.execute_query(
        """
        INSERT INTO pipeline_runs
            (id, triggered_by, status, started_at, completed_at,
             duration_seconds, error_message, created_at)
        VALUES
            (%(id)s::uuid, 'test', %(status)s, %(started_at)s, %(completed_at)s,
             %(duration_seconds)s, %(error_message)s,
             COALESCE(%(created_at)s, now()))
        """,
        {
            "id": run_id, "status": status,
            "started_at": started_at, "completed_at": completed_at,
            "duration_seconds": duration_seconds,
            "error_message": error_message,
            "created_at": created_at,
        },
        fetch=False,
    )
    return run_id


@pytest.fixture
def repo(live_db):
    # Wipe pipeline_runs for a clean slate. The test DB is not shared with prod.
    live_db.execute_query("DELETE FROM pipeline_runs", fetch=False)
    return PipelineWatchdogRepository(live_db)


@pytest.mark.integration
class TestLastSuccessfulCompletedAt:
    def test_no_runs(self, repo):
        assert repo.last_successful_completed_at() is None

    def test_only_failed_runs(self, repo, live_db):
        _insert_run(live_db, status="failed",
                    started_at=datetime.now(timezone.utc) - timedelta(hours=2),
                    completed_at=datetime.now(timezone.utc) - timedelta(hours=1))
        assert repo.last_successful_completed_at() is None

    def test_returns_latest_completed(self, repo, live_db):
        old = datetime.now(timezone.utc) - timedelta(days=2)
        new = datetime.now(timezone.utc) - timedelta(hours=1)
        _insert_run(live_db, status="completed",
                    started_at=old - timedelta(minutes=30), completed_at=old)
        _insert_run(live_db, status="completed",
                    started_at=new - timedelta(minutes=30), completed_at=new)
        _insert_run(live_db, status="failed",
                    started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
                    completed_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        result = repo.last_successful_completed_at()
        # Compare at second granularity to avoid tz/microsecond precision noise
        assert abs((result - new).total_seconds()) < 1


@pytest.mark.integration
class TestFirstRunCreatedAt:
    """Anchor for the stale check when nothing has ever succeeded.

    The logic is unit-tested against a stubbed adapter in
    tests/unit/modules/pipeline_watchdog/test_repository.py; what only a live
    database can check is that the SQL names real columns.
    """

    def test_no_runs(self, repo):
        assert repo.first_run_created_at() is None

    def test_returns_the_earliest_created_at(self, repo, live_db):
        oldest = datetime.now(timezone.utc) - timedelta(days=4)
        _insert_run(live_db, status="failed", created_at=oldest,
                    started_at=oldest, completed_at=oldest + timedelta(minutes=1))
        _insert_run(live_db, status="completed",
                    created_at=datetime.now(timezone.utc) - timedelta(days=1))
        result = repo.first_run_created_at()
        assert abs((result - oldest).total_seconds()) < 1

    def test_counts_runs_that_never_succeeded(self, repo, live_db):
        """A day-one install whose every attempt failed still has an anchor —
        that is the whole point of the read."""
        first = datetime.now(timezone.utc) - timedelta(hours=3)
        _insert_run(live_db, status="failed", created_at=first,
                    started_at=first, completed_at=first + timedelta(minutes=2),
                    error_message="boom")
        assert abs((repo.first_run_created_at() - first).total_seconds()) < 1


@pytest.mark.integration
class TestLastFinishedRun:
    def test_no_runs(self, repo):
        assert repo.last_finished_run() is None

    def test_running_rows_ignored(self, repo, live_db):
        _insert_run(live_db, status="running",
                    started_at=datetime.now(timezone.utc) - timedelta(minutes=10))
        assert repo.last_finished_run() is None

    def test_returns_most_recent_finished(self, repo, live_db):
        old = datetime.now(timezone.utc) - timedelta(hours=5)
        new = datetime.now(timezone.utc) - timedelta(hours=1)
        _insert_run(live_db, status="completed",
                    started_at=old - timedelta(minutes=30), completed_at=old)
        failed_id = _insert_run(live_db, status="failed",
                                started_at=new - timedelta(minutes=30),
                                completed_at=new,
                                error_message="boom")
        _insert_run(live_db, status="running",
                    started_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        row = repo.last_finished_run()
        assert row is not None
        assert str(row["id"]) == failed_id
        assert row["status"] == "failed"
        assert row["error_message"] == "boom"


@pytest.mark.integration
class TestStuckRuns:
    def test_no_stuck_runs(self, repo, live_db):
        _insert_run(live_db, status="running",
                    started_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        assert repo.stuck_runs(max_duration_minutes=240) == []

    def test_running_longer_than_max(self, repo, live_db):
        started = datetime.now(timezone.utc) - timedelta(minutes=300)
        stuck_id = _insert_run(live_db, status="running", started_at=started)
        rows = repo.stuck_runs(max_duration_minutes=240)
        assert len(rows) == 1
        assert str(rows[0]["id"]) == stuck_id
        assert rows[0]["status"] == "running"

    def test_pending_with_null_started_at_uses_created_at(self, repo, live_db):
        created = datetime.now(timezone.utc) - timedelta(minutes=300)
        stuck_id = _insert_run(live_db, status="pending", started_at=None,
                               created_at=created)
        rows = repo.stuck_runs(max_duration_minutes=240)
        assert len(rows) == 1
        assert str(rows[0]["id"]) == stuck_id


@pytest.mark.integration
class TestRecoverOrphanedPendingRuns:
    def test_no_pending_rows(self, repo):
        assert repo.recover_orphaned_pending_runs(threshold_minutes=5) == 0

    def test_recovers_pending_with_null_started_at_past_threshold(
        self, repo, live_db,
    ):
        old_created = datetime.now(timezone.utc) - timedelta(minutes=10)
        orphan_id = _insert_run(
            live_db, status="pending", started_at=None, created_at=old_created,
        )

        recovered = repo.recover_orphaned_pending_runs(threshold_minutes=5)

        assert recovered == 1
        rows = live_db.execute_query(
            "SELECT status, completed_at, error_message "
            "FROM pipeline_runs WHERE id = %(id)s::uuid",
            {"id": orphan_id},
        )
        assert rows[0]["status"] == "failed"
        assert rows[0]["completed_at"] is not None
        assert "Orphaned" in rows[0]["error_message"]

    def test_skips_pending_within_threshold(self, repo, live_db):
        recent = datetime.now(timezone.utc) - timedelta(minutes=1)
        _insert_run(live_db, status="pending", started_at=None, created_at=recent)
        assert repo.recover_orphaned_pending_runs(threshold_minutes=5) == 0

    def test_skips_running_rows(self, repo, live_db):
        """Running rows have a started_at — they're real, not orphans."""
        old_started = datetime.now(timezone.utc) - timedelta(hours=2)
        _insert_run(live_db, status="running", started_at=old_started)
        assert repo.recover_orphaned_pending_runs(threshold_minutes=5) == 0

    def test_skips_pending_with_started_at_set(self, repo, live_db):
        """Defensive: status='pending' + started_at set is unusual but not orphaned."""
        started = datetime.now(timezone.utc) - timedelta(hours=2)
        created = datetime.now(timezone.utc) - timedelta(hours=2, minutes=1)
        _insert_run(
            live_db, status="pending", started_at=started, created_at=created,
        )
        assert repo.recover_orphaned_pending_runs(threshold_minutes=5) == 0


@pytest.mark.integration
class TestRecoverOrphanedRunningRuns:
    def test_no_running_rows(self, repo):
        assert repo.recover_orphaned_running_runs(threshold_minutes=240) == 0

    def test_recovers_running_past_threshold(self, repo, live_db):
        old_started = datetime.now(timezone.utc) - timedelta(minutes=300)
        orphan_id = _insert_run(
            live_db, status="running", started_at=old_started,
        )

        recovered = repo.recover_orphaned_running_runs(threshold_minutes=240)

        assert recovered == 1
        rows = live_db.execute_query(
            "SELECT status, completed_at, duration_seconds, "
            "current_stage, error_message "
            "FROM pipeline_runs WHERE id = %(id)s::uuid",
            {"id": orphan_id},
        )
        assert rows[0]["status"] == "failed"
        assert rows[0]["completed_at"] is not None
        assert rows[0]["duration_seconds"] is not None
        assert rows[0]["duration_seconds"] > 0
        assert rows[0]["current_stage"] is None
        assert "Orphaned" in rows[0]["error_message"]

    def test_skips_running_within_threshold(self, repo, live_db):
        recent = datetime.now(timezone.utc) - timedelta(minutes=30)
        _insert_run(live_db, status="running", started_at=recent)
        assert repo.recover_orphaned_running_runs(threshold_minutes=240) == 0

    def test_skips_pending_rows(self, repo, live_db):
        """Pending rows are handled by recover_orphaned_pending_runs."""
        old_started = datetime.now(timezone.utc) - timedelta(hours=10)
        old_created = old_started - timedelta(minutes=1)
        _insert_run(
            live_db, status="pending", started_at=None, created_at=old_created,
        )
        assert repo.recover_orphaned_running_runs(threshold_minutes=240) == 0

    def test_skips_completed_rows(self, repo, live_db):
        old_started = datetime.now(timezone.utc) - timedelta(minutes=300)
        old_completed = datetime.now(timezone.utc) - timedelta(minutes=100)
        _insert_run(
            live_db, status="completed",
            started_at=old_started, completed_at=old_completed,
        )
        assert repo.recover_orphaned_running_runs(threshold_minutes=240) == 0


@pytest.mark.integration
class TestSettingsReads:
    """Settings-read path is exercised end-to-end by
    tests/integration/test_pipeline_watchdog.py. A separate integration test
    here would have to mutate the shared system_settings table, risking
    cross-test contamination. Defaults are also exercised by unit tests via
    get_default_value in the settings_schema module. Intentionally empty."""
    pass
