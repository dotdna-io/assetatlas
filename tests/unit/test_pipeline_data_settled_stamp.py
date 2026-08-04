# SPDX-License-Identifier: AGPL-3.0-or-later
"""The worker stamps `data_settled_at` when the data actually settled (#1185).

`GET /api/pipeline/data-version` tells every open tab which run's output is in
the `assets` table, and a change means "drop your whole client-side cache". It
used to derive that moment from `pipeline_runs.completed_at`, which is a
CONTESTED column — two writers set it:

  * `_execute_run`, when the worker finishes, guarded on the row still being
    pending/running so a late worker cannot rewrite history (#1166/#1170);
  * `pipeline_watchdog.recover_orphaned_running_runs`, on any row still
    'running' past the recovery window — which, as its own docstring admits,
    "kills the row of a run that is merely slow ... while its worker is still
    writing".

On a slow-but-alive run those two combine into a signal that is wrong twice
over: the watchdog stamps `completed_at` MID-RUN, so within 60s every tab
re-caches against a half-rebuilt `assets` table; and when the worker genuinely
finishes, its guarded UPDATE matches zero rows by design, so `completed_at` is
never corrected and no second bump ever comes. Tabs stay wrong until the next
run — a full pipeline cadence, 24h by default.

`data_settled_at` is the uncontested alternative: written only by the worker,
only in the `finally` that already flushes the dashboard cache — the one place
that knows when the data settled, whoever owns the row by then.

The DB is a mock here, so a WHERE clause cannot be enforced by running it: each
test asserts the statement the code emits as well as what it does. See
test_pipeline_service_terminal_guard.py for the guarded UPDATEs this one is
deliberately NOT like, and test_pipeline_data_version.py for the read side.
"""
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import UUID

from src.modules.pipeline.pipeline_service import PipelineService

RUN_ID = UUID("11111111-2222-3333-4444-555555555555")


def _service(result=None, raises=None):
    svc = PipelineService.__new__(PipelineService)
    svc.connection_manager = MagicMock()
    svc.trigger_service = MagicMock()
    gen = svc.trigger_service.generate_assets_for_latest_batches
    if raises is not None:
        gen.side_effect = raises
    else:
        gen.return_value = result
    # Cache invalidation imports the Flask app package and is unrelated to the
    # stamp; stubbed except where the ordering between the two is the subject.
    svc._invalidate_dashboard_cache = MagicMock()
    return svc


def _execute(svc, db_returns):
    """Run _execute_run against a mock adapter and hand back the mock.

    db_returns feeds execute_query in statement order:
    [mark-running, terminal-update, settle-stamp].
    """
    db = MagicMock()
    db.execute_query.side_effect = list(db_returns)
    with patch("src.modules.pipeline.pipeline_service.PostgreSQLAdapter",
               return_value=db):
        svc._execute_run(RUN_ID)
    return db


def _settle_call(db):
    """The `finally`'s stamp — statement 3 of 3."""
    return db.execute_query.call_args_list[2]


def _settle_sql(db):
    """Whitespace-collapsed, so reformatting the statement is free."""
    return " ".join(_settle_call(db).args[0].split())


def _settle_params(db):
    return _settle_call(db).args[1]


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]


class TestTheStampIsUnguarded:
    """The entire fix, in one predicate that must not exist."""

    def test_the_update_carries_no_status_predicate(self):
        """Every OTHER terminal UPDATE in `_execute_run` is guarded on `status IN
        ('pending','running')`, and rightly so — an unguarded write there would
        resurrect a row the watchdog finalised, silently resolve the operational
        alert and rewrite run history.

        That same guard is precisely why `completed_at` cannot serve as the
        settle moment: on the run where it matters the guard bites, the UPDATE
        matches nothing, and the mid-run value the watchdog wrote stands
        forever. `data_settled_at` is a fact only this worker knows and no other
        writer ever touches, so there is no history to protect and no race to
        guard against — adding a guard here would reproduce the bug.
        """
        svc = _service(result={'success': True})
        db = _execute(svc, [None, [{'id': str(RUN_ID)}], None])

        sql = _settle_sql(db)
        assert 'data_settled_at' in sql, sql
        assert not re.search(r"status\s+IN", sql), sql
        assert 'status' not in sql, (
            "the settle stamp must not mention status at all: it claims the row "
            f"by id, whoever finalised it — got {sql!r}"
        )

    def test_it_claims_the_row_by_id_alone(self):
        svc = _service(result={'success': True})
        db = _execute(svc, [None, [{'id': str(RUN_ID)}], None])

        assert _settle_sql(db) == (
            "UPDATE pipeline_runs SET data_settled_at = %s WHERE id = %s"
        )
        assert _settle_params(db)[1] == str(RUN_ID)

    def test_it_does_not_need_the_row_back(self):
        """No RETURNING, no fetch: unlike the guarded UPDATEs there is no
        zero-row case to interpret."""
        svc = _service(result={'success': True})
        db = _execute(svc, [None, [{'id': str(RUN_ID)}], None])

        assert 'RETURNING' not in _settle_sql(db)
        assert _settle_call(db).kwargs['fetch'] is False


class TestAWatchdogFinalisedRunStillSettles:
    """The gap this change closes, both halves of it."""

    def test_the_stamp_lands_although_the_terminal_update_claimed_no_row(self):
        """Zero rows back means the watchdog got there first and the worker is
        the slow-but-alive case. Its status write is correctly abandoned; the
        settle stamp is not, because the assets were rebuilt whoever owns the
        row — exactly the reasoning that already puts the dashboard flush in
        this `finally`."""
        svc = _service(result={'success': True})
        db = _execute(svc, [None, [], None])  # zero rows: the row is not ours

        assert 'data_settled_at' in _settle_sql(db)
        assert _settle_params(db)[1] == str(RUN_ID)

    def test_and_get_data_version_then_reports_that_run(self):
        """The read side of the same run. Its `completed_at` is the watchdog's
        mid-run value and is never corrected, so a query ordered on that column
        surfaced this id while the rebuild was still in flight and never again
        afterwards. Ordered on the settle stamp it surfaces once, when the table
        is genuinely finished."""
        svc = PipelineService.__new__(PipelineService)
        db = MagicMock()
        db.execute_query.return_value = [{
            'id': RUN_ID,
            'status': 'failed',  # what the watchdog wrote
            'completed_at': datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc),
        }]
        svc._db = db

        version = svc.get_data_version()

        assert version['run_id'] == str(RUN_ID)
        sql = " ".join(db.execute_query.call_args.args[0].split())
        assert 'WHERE data_settled_at IS NOT NULL' in sql, sql
        assert 'ORDER BY data_settled_at DESC' in sql, sql


class TestTheSettleMoment:
    """Which instant gets written, and where it comes from."""

    def test_the_stamp_is_timezone_aware(self):
        """`pipeline_runs` is entirely `timestamptz` — it is not one of the
        naive-timestamp tables (system_settings, settings_audit_log,
        system_info, saved_filter_grants, schema_migrations). A naive value
        would be read as the server's local zone and land the settle moment
        hours out, which on the ORDER BY key means reporting the wrong run."""
        svc = _service(result={'success': True})
        db = _execute(svc, [None, [{'id': str(RUN_ID)}], None])

        settled_at = _settle_params(db)[0]
        assert isinstance(settled_at, datetime)
        assert settled_at.utcoffset() == timedelta(0), settled_at

    def test_it_is_taken_in_the_finally_not_reused_from_the_status_write(self):
        """`completed_at` is computed before the terminal UPDATE runs; the settle
        moment is when the `finally` reaches this statement — after the row is
        written and the cache flushed. Reusing the earlier value would re-couple
        the two timestamps this change exists to separate."""
        svc = _service(result={'success': True})
        db = _execute(svc, [None, [{'id': str(RUN_ID)}], None])

        completed_at = db.execute_query.call_args_list[1].args[1][0]
        assert _settle_params(db)[0] >= completed_at


class TestOrderingAgainstTheCacheFlush:
    def test_the_dashboard_cache_is_flushed_before_the_stamp(self):
        """A client that sees the new version immediately refetches the dashboard
        endpoints. Stamping first would let that refetch race the flush and
        re-cache the very aggregations the run just superseded — the #1136 bug
        with a new trigger. Order is load-bearing, not tidiness."""
        order = []
        svc = _service(result={'success': True})
        svc._invalidate_dashboard_cache = MagicMock(
            side_effect=lambda run_id: order.append('flush'))

        db = MagicMock()

        def _record(sql, *args, **kwargs):
            if 'data_settled_at' in sql:
                order.append('stamp')
            return [{'id': str(RUN_ID)}]

        db.execute_query.side_effect = _record
        with patch("src.modules.pipeline.pipeline_service.PostgreSQLAdapter",
                   return_value=db):
            svc._execute_run(RUN_ID)

        assert order == ['flush', 'stamp']


class TestAStampFailureCannotFailARun:
    """A missed stamp is a stale cache; an escaping exception is a corrupt run.

    The stamp sits in the same `finally` as the dashboard flush, so anything it
    lets escape propagates out of the worker thread AFTER the row was written —
    turning a run that succeeded into one with no recorded outcome at all. It
    has to swallow, and the warning is what proves these tests are not passing
    vacuously against a build that never stamps.
    """

    def _terminal_sql(self, db):
        return " ".join(db.execute_query.call_args_list[1].args[0].split())

    def test_a_completed_run_stays_completed(self, caplog):
        svc = _service(result={'success': True})
        with caplog.at_level("WARNING"):
            db = _execute(svc, [None, [{'id': str(RUN_ID)}],
                                RuntimeError("db down")])  # must not raise

        assert re.search(r"SET\s+status\s*=\s*'completed'", self._terminal_sql(db))
        assert any("data version not stamped" in m for m in _warnings(caplog)), (
            _warnings(caplog))

    def test_a_failed_run_stays_failed(self, caplog):
        svc = _service(result={'success': False, 'error': 'boom'})
        with caplog.at_level("WARNING"):
            db = _execute(svc, [None, [{'id': str(RUN_ID)}],
                                RuntimeError("db down")])  # must not raise

        assert re.search(r"SET\s+status\s*=\s*'failed'", self._terminal_sql(db))
        assert any("data version not stamped" in m for m in _warnings(caplog)), (
            _warnings(caplog))

    def test_the_warning_names_the_run(self, caplog):
        """An operator reading the log needs to know WHICH run's version is
        missing — that run's data is in the table and no tab will hear about
        it."""
        svc = _service(result={'success': True})
        with caplog.at_level("WARNING"):
            _execute(svc, [None, [{'id': str(RUN_ID)}], RuntimeError("db down")])

        assert any(str(RUN_ID) in m and "data version not stamped" in m
                   for m in _warnings(caplog))

    def test_it_still_stamps_when_the_run_raised(self, caplog):
        """A run that died part-way may already have got past `TRUNCATE assets
        CASCADE`, so the table it left behind still needs a version."""
        svc = _service(raises=RuntimeError("stage exploded"))
        with caplog.at_level("WARNING"):
            db = _execute(svc, [None, [{'id': str(RUN_ID)}], None])

        assert 'data_settled_at' in _settle_sql(db)
        assert not any("data version not stamped" in m for m in _warnings(caplog))
