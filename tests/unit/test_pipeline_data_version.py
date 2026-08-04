# SPDX-License-Identifier: AGPL-3.0-or-later
"""The data-version signal behind GET /api/pipeline/data-version (#1144).

Every pipeline run rebuilds the asset table from scratch (`TRUNCATE assets
CASCADE`), which makes every client-side cache in the app stale the instant a
run finishes — Explorer, the rule builders, every field picker and value
dropdown kept serving pre-run data until their own staleTime lapsed. This is
the one thing the whole UI polls to learn that it happened, so what matters is
WHICH run it reports and how little it costs to ask.

The DB is a mock here, so these assert both the returned shape AND the SQL that
produced it: "reports the newest finished run" is a claim about the query, and
a test that only checked the dict would pass against `ORDER BY created_at`.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.modules.pipeline.pipeline_service import PipelineService


def _service():
    """A PipelineService whose adapter is a mock (no DB, no __init__ side effects)."""
    with patch('src.modules.pipeline.pipeline_service.PostgreSQLAdapter') as MockAdapter:
        db = MagicMock()
        MockAdapter.return_value = db
        svc = PipelineService(
            connection_manager=MagicMock(), trigger_service=MagicMock(),
        )
    return svc, db


def _sql(db):
    """The last statement issued, whitespace-collapsed so reformatting is free."""
    return " ".join(db.execute_query.call_args.args[0].split())


class TestWhichRunIsReported:
    """The newest SETTLED run — by the moment its data landed, whatever its
    outcome. See TestTheOrderingKeyIsTheSettleMoment for why that is not the
    same as its completion time."""

    def test_reports_the_newest_finished_run(self):
        svc, db = _service()
        run_id = uuid4()
        db.execute_query.return_value = [{
            'id': run_id,
            'status': 'completed',
            'completed_at': datetime(2026, 7, 29, 10, 30, tzinfo=timezone.utc),
        }]

        version = svc.get_data_version()

        assert version['run_id'] == str(run_id)
        assert version['status'] == 'completed'
        assert version['completed_at'] == '2026-07-29T10:30:00+00:00'

    def test_a_failed_run_still_bumps_the_version(self):
        """A failed run truncated `assets` and committed whatever stages got
        that far, so it invalidates exactly the caches a successful one does.
        Selecting on `status = 'completed'` would leave every client serving
        data the failed run destroyed, until each cache aged out on its own."""
        svc, db = _service()
        run_id = uuid4()
        db.execute_query.return_value = [{
            'id': run_id,
            'status': 'failed',
            'completed_at': datetime(2026, 7, 29, 11, 0, tzinfo=timezone.utc),
        }]

        version = svc.get_data_version()

        assert version['run_id'] == str(run_id)
        assert version['status'] == 'failed'

    def test_query_takes_the_newest_finished_run_not_the_newest_row(self):
        """Pins the ordering the two tests above cannot see through the mock.

        `data_settled_at IS NOT NULL` excludes the pending/running row a manual
        trigger creates — without it a *starting* run would bump the version
        and every tab would refetch against data the run is midway through
        replacing. `status` may be named only inside the negated no-op
        exclusion (see TestARunThatChangedNothing); selecting *to* completed
        runs would drop the failed ones, which invalidate the same caches.
        """
        svc, db = _service()
        db.execute_query.return_value = []

        svc.get_data_version()

        sql = _sql(db)
        assert 'data_settled_at IS NOT NULL' in sql, sql
        assert 'ORDER BY data_settled_at DESC' in sql, sql
        assert 'LIMIT 1' in sql, sql
        assert sql.count("status = 'completed'") == 1, sql
        assert "NOT (status = 'completed'" in sql, (
            "the one permitted mention of a completed status is the negated "
            "no-op exclusion; filtering TO a completed status hides "
            "failed/partial runs, which invalidate the same caches"
        )


class TestTheOrderingKeyIsTheSettleMoment:
    """Ordered on `data_settled_at`, never on `completed_at` (#1185 finding 2).

    `completed_at` has two writers. The worker sets it when it finishes, guarded
    on the row still being pending/running; the watchdog sets it on any row still
    'running' past the recovery window, including a run that is merely slow and
    whose worker is still writing. On that run the watchdog's mid-run stamp made
    every tab re-cache against a half-rebuilt `assets` table, and because the
    worker's guarded UPDATE then matched zero rows by design, the value was never
    corrected and no second bump ever came.

    `data_settled_at` is written by the worker alone, in the `finally` that also
    flushes the dashboard cache — see test_pipeline_data_settled_stamp.py.
    """

    def test_never_orders_on_completed_at(self):
        """The regression test proper. Ordering on the contested column is the
        bug, and it is invisible through a mock adapter."""
        svc, db = _service()
        db.execute_query.return_value = []

        svc.get_data_version()

        sql = _sql(db)
        assert 'ORDER BY completed_at' not in sql, sql
        assert 'completed_at IS NOT NULL' not in sql, sql

    def test_completed_at_is_still_the_reported_timestamp(self):
        """The response shape is unchanged — the frontend types
        `{run_id, completed_at, status}` — so the row's own `completed_at` is
        still what comes back. It is the status-transition moment and may
        PREDATE the settle moment on a watchdog-finalised run; only `run_id` is
        keyed off, so that is a label, not the signal."""
        svc, db = _service()
        run_id = uuid4()
        db.execute_query.return_value = [{
            'id': run_id,
            'status': 'failed',
            'completed_at': datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc),
        }]

        version = svc.get_data_version()

        assert set(version) == {'run_id', 'completed_at', 'status'}
        assert version['completed_at'] == '2026-07-30T09:00:00+00:00'

    def test_a_run_whose_worker_has_not_finished_is_not_reported(self):
        """`data_settled_at IS NOT NULL` is the whole exclusion: it covers the
        pending/running row a trigger has just created AND the row the watchdog
        finalised while its worker is still rebuilding the table. The old
        `completed_at IS NOT NULL` caught only the first."""
        svc, db = _service()
        db.execute_query.return_value = []

        assert svc.get_data_version()['run_id'] is None
        assert 'WHERE data_settled_at IS NOT NULL' in _sql(db), _sql(db)


class TestARunThatChangedNothing:
    """A completed run that processed zero batches must not bump the version.

    `generate_assets_for_latest_batches` returns early — success, zero
    batches — when `_get_latest_pending_batches` comes back empty, so the
    orchestrator never starts and `TRUNCATE assets CASCADE` never fires.
    `_execute_run` stamps `completed_at` all the same, so with nothing to
    distinguish it the version reports a fresh `run_id` and every open tab
    drops its entire cache for a run that touched no data. That query re-picks
    the latest completed batch of every ENABLED integration on every run,
    whether or not it was generated before, so the early return is reached
    only when no enabled integration has a completed batch carrying records —
    not merely when the sources have not re-synced, which still rebuilds
    `assets` and still bumps the version (#1185).

    Postgres does the filtering and the adapter here is a mock, so *which* rows
    survive is a claim about the WHERE clause and is asserted there; the dict
    assertions pin what a surviving row still answers.
    """

    def test_a_completed_run_that_processed_nothing_is_excluded(self):
        svc, db = _service()
        db.execute_query.return_value = []

        svc.get_data_version()

        assert (
            "NOT (status = 'completed' "
            "AND COALESCE((stats->>'batches_processed')::int, 1) = 0)"
        ) in _sql(db), _sql(db)

    def test_a_completed_run_that_processed_batches_is_reported(self):
        """Zero is the only batch count that proves nothing happened. One or
        more means the run truncated and refilled `assets`, which is the entire
        reason clients poll this."""
        svc, db = _service()
        run_id = uuid4()
        db.execute_query.return_value = [{
            'id': run_id,
            'status': 'completed',
            'completed_at': datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
        }]

        version = svc.get_data_version()

        assert version['run_id'] == str(run_id)
        assert version['status'] == 'completed'
        assert "::int, 1) = 0" in _sql(db), (
            "the exclusion must test for zero exactly — anything broader drops "
            "runs that did rebuild the table"
        )

    def test_a_failed_run_that_processed_nothing_is_still_reported(self):
        """`batches_processed` is 0 on a run that died before the trigger
        counted any, and that run may still have got past `TRUNCATE assets
        CASCADE` first. Only completed AND zero is provably a no-op, which is
        why the two are ANDed inside the negation rather than the batch count
        standing alone."""
        svc, db = _service()
        run_id = uuid4()
        db.execute_query.return_value = [{
            'id': run_id,
            'status': 'failed',
            'completed_at': datetime(2026, 7, 29, 13, 0, tzinfo=timezone.utc),
        }]

        version = svc.get_data_version()

        assert version['run_id'] == str(run_id)
        assert version['status'] == 'failed'
        assert "NOT (status = 'completed' AND" in _sql(db), (
            "dropping the status half would exclude failed runs too, and a "
            "failed run may have truncated before it stopped"
        )

    def test_the_batch_count_is_coalesced_before_it_is_compared(self):
        """The NULL trap, and the reason this has its own test.

        `stats` is nullable — the INSERT creates the row without it, and the
        watchdog finalises a run without ever writing one — so the sub-path is
        NULL for those rows. Unwrapped, `NOT (true AND NULL)` evaluates to
        NULL rather than true and Postgres drops the row: every such run
        disappears, an install with history starts answering all-nulls, and
        nothing raises. COALESCE to 1 — a count that cannot be a no-op — keeps
        rows whose batch count is unknown.

        Asserted against the SQL because the mock never evaluates it: this
        regression passes every dict assertion above.
        """
        svc, db = _service()
        db.execute_query.return_value = []

        svc.get_data_version()

        assert "COALESCE((stats->>'batches_processed')::int, 1)" in _sql(db), _sql(db)


class TestFreshInstall:
    """No run has ever finished — must answer, not raise."""

    def test_all_nulls_when_pipeline_runs_is_empty(self):
        svc, db = _service()
        db.execute_query.return_value = []

        assert svc.get_data_version() == {
            'run_id': None, 'completed_at': None, 'status': None,
        }

    def test_all_nulls_when_the_adapter_returns_none(self):
        svc, db = _service()
        db.execute_query.return_value = None

        assert svc.get_data_version()['run_id'] is None


class TestCost:
    """Polled by every open browser tab, so it must stay cheap."""

    def test_does_not_select_the_stats_blob(self):
        """`stats` carries batch_results, stage_timings and the per-source
        summaries — kilobytes of JSON per row, fetched once a minute per tab
        for a three-field answer. The WHERE clause reads one scalar sub-path
        out of it, as `get_current_status` does; what must never cross the
        wire is the whole blob."""
        svc, db = _service()
        db.execute_query.return_value = []

        svc.get_data_version()

        sql = _sql(db)
        selected = sql.split('SELECT ', 1)[1].split(' FROM ')[0]
        assert 'stats' not in selected, sql

    def test_selects_only_the_three_columns_it_returns(self):
        svc, db = _service()
        db.execute_query.return_value = []

        svc.get_data_version()

        selected = _sql(db).split('SELECT ', 1)[1].split(' FROM ')[0]
        assert {c.strip() for c in selected.split(',')} == {
            'id', 'status', 'completed_at',
        }
