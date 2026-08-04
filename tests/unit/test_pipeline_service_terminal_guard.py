# SPDX-License-Identifier: AGPL-3.0-or-later
"""_execute_run must not resurrect a run the watchdog already finalised.

When the watchdog decides a run's worker is dead it writes the row to 'failed'.
If that worker is in fact alive, its own terminal UPDATE used to overwrite the
row back to 'completed' — silently resolving the operational alert and rewriting
run history. Every terminal UPDATE is therefore guarded on the row still being
pending/running, and reports the zero-row case rather than retrying it.

The DB is a mock here, so it cannot enforce a WHERE clause: each test asserts
both the guard the code emits AND what it does with the zero-row answer.
Asserting only the latter would pass against an unguarded UPDATE.

The guard applies to the STATUS transition only. The `data_settled_at` stamp the
same `finally` fires is deliberately unguarded — it is the column nothing else
writes, added precisely because this guard makes `completed_at` unreliable as a
settle moment (#1185). Do not generalise the rule to it;
test_pipeline_data_settled_stamp.py asserts its absence.
"""
import re
from unittest.mock import MagicMock, patch
from uuid import UUID

from src.modules.pipeline.pipeline_service import PipelineService

RUN_ID = UUID("11111111-2222-3333-4444-555555555555")

# Matched against whitespace-collapsed SQL so reformatting the statement does
# not fail the test — only dropping the guard does.
GUARD = re.compile(r"AND\s+status\s+IN\s*\(\s*'pending'\s*,\s*'running'\s*\)")


def _service(result=None, raises=None):
    svc = PipelineService.__new__(PipelineService)
    svc.connection_manager = MagicMock()
    svc.trigger_service = MagicMock()
    gen = svc.trigger_service.generate_assets_for_latest_batches
    if raises is not None:
        gen.side_effect = raises
    else:
        gen.return_value = result
    # Cache invalidation imports the Flask app package and is unrelated to row
    # ownership; stubbed so the assertions below are about the UPDATE only.
    svc._invalidate_dashboard_cache = MagicMock()
    return svc


# Statement order inside _execute_run: mark-running, the terminal status UPDATE,
# then the data_settled_at stamp fired from the `finally` (#1185). The stamp is
# padded in below so a test only has to describe the statements it cares about;
# it is unguarded by design and owned by test_pipeline_data_settled_stamp.py.
_TERMINAL = 1


def _execute(svc, db_returns):
    """Run _execute_run against a mock adapter and hand back the mock.

    db_returns feeds execute_query in order: [mark-running, terminal-update].
    """
    db = MagicMock()
    db.execute_query.side_effect = list(db_returns) + [None]
    with patch("src.modules.pipeline.pipeline_service.PostgreSQLAdapter",
               return_value=db):
        svc._execute_run(RUN_ID)
    return db


def _terminal_sql(db):
    return " ".join(db.execute_query.call_args_list[_TERMINAL].args[0].split())


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]


def _assert_guarded(db):
    """The terminal UPDATE claims the row only while it is still unfinished."""
    sql = _terminal_sql(db)
    assert GUARD.search(sql), sql
    assert "RETURNING id" in sql
    assert db.execute_query.call_args_list[_TERMINAL].kwargs["fetch"] is True


def _assert_sets_status(db, status):
    assert re.search(rf"SET\s+status\s*=\s*'{status}'", _terminal_sql(db))


def _assert_did_not_retry(db):
    """One terminal attempt, never a retry loop. Three statements in all:
    mark-running, that attempt, and the `finally`'s settle stamp."""
    assert db.execute_query.call_count == 3, [
        " ".join(c.args[0].split()) for c in db.execute_query.call_args_list
    ]


class TestCompletedPath:
    def test_update_is_guarded_and_returns_the_claimed_row(self):
        svc = _service(result={'success': True})
        db = _execute(svc, [None, [{'id': str(RUN_ID)}]])
        _assert_guarded(db)

    def test_still_marks_the_row_completed_when_it_owns_it(self, caplog):
        svc = _service(result={'success': True})
        with caplog.at_level("INFO"):
            db = _execute(svc, [None, [{'id': str(RUN_ID)}]])

        _assert_sets_status(db, "completed")
        assert any("completed in" in r.getMessage() for r in caplog.records)
        assert not _warnings(caplog)

    def test_warns_and_gives_up_when_the_watchdog_already_finalised_the_row(
        self, caplog,
    ):
        svc = _service(result={'success': True})
        with caplog.at_level("WARNING"):
            db = _execute(svc, [None, []])  # zero rows: the row is no longer ours

        warned = _warnings(caplog)
        assert any(str(RUN_ID) in m and "watchdog" in m for m in warned)
        _assert_did_not_retry(db)

    def test_dashboard_cache_is_dropped_even_when_the_row_was_not_claimed(self):
        # The pipeline wrote assets whoever owns the row, so the cached
        # aggregations are stale either way.
        svc = _service(result={'success': True})
        _execute(svc, [None, []])  # zero rows: the watchdog owns the row
        svc._invalidate_dashboard_cache.assert_called_once_with(RUN_ID)

    def test_dashboard_cache_is_dropped_when_the_row_was_claimed(self):
        svc = _service(result={'success': True})
        _execute(svc, [None, [{'id': str(RUN_ID)}]])
        svc._invalidate_dashboard_cache.assert_called_once_with(RUN_ID)


class TestFailedPath:
    def test_update_is_guarded(self):
        svc = _service(result={'success': False, 'error': 'boom'})
        db = _execute(svc, [None, [{'id': str(RUN_ID)}]])
        _assert_guarded(db)
        _assert_sets_status(db, "failed")

    def test_warns_when_the_watchdog_already_finalised_the_row(self, caplog):
        svc = _service(result={'success': False, 'error': 'boom'})
        with caplog.at_level("WARNING"):
            db = _execute(svc, [None, []])

        assert any(str(RUN_ID) in m and "watchdog" in m for m in _warnings(caplog))
        _assert_did_not_retry(db)

    def test_dashboard_cache_is_dropped(self):
        # A failed run reached the pipeline's TRUNCATE and committed whatever
        # stages ran before it stopped, so the cached aggregations describe
        # data that no longer exists. Invalidating only on success left the
        # dashboard quoting numbers for a run that had just destroyed them.
        svc = _service(result={'success': False, 'error': 'boom'})
        _execute(svc, [None, [{'id': str(RUN_ID)}]])
        svc._invalidate_dashboard_cache.assert_called_once_with(RUN_ID)

    def test_dashboard_cache_is_dropped_after_a_partial_run(self):
        # Some batches landed, some didn't: the assets table is neither the
        # old contents nor a clean new set — the worst thing to keep cached.
        svc = _service(result={'success': False, 'error': '1 of 3 batches failed',
                               'batches_processed': 2, 'batches_failed': 1})
        _execute(svc, [None, [{'id': str(RUN_ID)}]])
        svc._invalidate_dashboard_cache.assert_called_once_with(RUN_ID)


class TestExceptionPath:
    def test_update_is_guarded(self):
        svc = _service(raises=RuntimeError("stage exploded"))
        db = _execute(svc, [None, [{'id': str(RUN_ID)}]])
        _assert_guarded(db)
        _assert_sets_status(db, "failed")

    def test_warns_when_the_watchdog_already_finalised_the_row(self, caplog):
        svc = _service(raises=RuntimeError("stage exploded"))
        with caplog.at_level("WARNING"):
            db = _execute(svc, [None, []])  # must not raise

        assert any(str(RUN_ID) in m and "watchdog" in m for m in _warnings(caplog))
        _assert_did_not_retry(db)

    def test_dashboard_cache_is_dropped(self):
        svc = _service(raises=RuntimeError("stage exploded"))
        _execute(svc, [None, [{'id': str(RUN_ID)}]])
        svc._invalidate_dashboard_cache.assert_called_once_with(RUN_ID)

    def test_dashboard_cache_is_dropped_when_the_terminal_update_also_fails(self):
        # Redis and Postgres fail independently; losing the row update is no
        # reason to leave the operator staring at superseded dashboard cards.
        svc = _service(raises=RuntimeError("stage exploded"))
        _execute(svc, [None, RuntimeError("db down")])
        svc._invalidate_dashboard_cache.assert_called_once_with(RUN_ID)


class TestCacheInvalidationCannotFailARun:
    """A stale cache beats a successful run reported as failed.

    The invalidation now sits in a `finally`, i.e. on the path that decides
    nothing but runs always — so anything it lets escape would propagate out of
    the worker thread AFTER the row was written. It has to swallow, and these
    exercise the real method rather than the MagicMock `_service` installs.
    """

    def _real_invalidator(self, svc):
        # Drop the stub so the class's own (swallowing) implementation runs.
        del svc._invalidate_dashboard_cache
        return svc

    def test_a_broken_cache_backend_leaves_the_run_completed(self, caplog):
        svc = self._real_invalidator(_service(result={'success': True}))
        with patch("src.api.utils.caching.invalidate_dashboard_cache",
                   side_effect=ConnectionError("redis is down")):
            with caplog.at_level("WARNING"):
                db = _execute(svc, [None, [{'id': str(RUN_ID)}]])  # must not raise

        _assert_sets_status(db, "completed")
        assert any("dashboard cache not cleared" in m for m in _warnings(caplog))

    def test_a_broken_cache_backend_leaves_a_failed_run_failed(self, caplog):
        svc = self._real_invalidator(
            _service(result={'success': False, 'error': 'boom'}))
        with patch("src.api.utils.caching.invalidate_dashboard_cache",
                   side_effect=ConnectionError("redis is down")):
            with caplog.at_level("WARNING"):
                db = _execute(svc, [None, [{'id': str(RUN_ID)}]])  # must not raise

        _assert_sets_status(db, "failed")
        # The warning is what proves the failed path REACHED the invalidator at
        # all — without it this test passes vacuously against a build that
        # never invalidates on failure, which is the bug being fixed.
        assert any("dashboard cache not cleared" in m for m in _warnings(caplog))
