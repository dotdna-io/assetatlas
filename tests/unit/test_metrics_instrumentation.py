# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prometheus instrumentation wiring (#922, Phase 1).

Each test drives a real instrumented code path (or the exact helper it calls)
and asserts the corresponding ``assetatlas_*`` series moves off its prior value
via prometheus_client collector introspection. Counters are global and
monotonic across the session, so tests either measure a before/after delta or
use a label set unique to the test (a fresh integration_id / job_id) that
starts at zero.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import uuid
from unittest.mock import MagicMock

from prometheus_client import CollectorRegistry, REGISTRY
import psycopg2

from src.api.utils import metrics as m

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample(metric, name, labels=None):
    """Current value of ``metric``'s sample named ``name`` (optionally filtered
    by ``labels``). Returns 0.0 if the labelled child does not exist yet."""
    for family in metric.collect():
        for s in family.samples:
            if s.name != name:
                continue
            if labels is None or all(s.labels.get(k) == v for k, v in labels.items()):
                return s.value
    return 0.0


# ---------------------------------------------------------------------------
# Asset-generation pipeline  (track_asset_generation_from_stats)
# ---------------------------------------------------------------------------


def test_asset_generation_stats_success_moves_series():
    runs = _sample(
        m.asset_generation_runs,
        "assetatlas_asset_generation_runs_total",
        {"status": "success"},
    )
    created = _sample(
        m.assets_created, "assetatlas_assets_created_total", {"source": "integration"}
    )
    updated = _sample(
        m.assets_updated, "assetatlas_assets_updated_total", {"source": "integration"}
    )
    dur = _sample(
        m.asset_generation_duration,
        "assetatlas_asset_generation_duration_seconds_count",
    )

    m.track_asset_generation_from_stats(
        {
            "success": True,
            "pipeline_duration": 12.5,
            "new_assets": 3,
            "updated_assets": 7,
        }
    )

    assert (
        _sample(
            m.asset_generation_runs,
            "assetatlas_asset_generation_runs_total",
            {"status": "success"},
        )
        == runs + 1
    )
    assert (
        _sample(
            m.assets_created,
            "assetatlas_assets_created_total",
            {"source": "integration"},
        )
        == created + 3
    )
    assert (
        _sample(
            m.assets_updated,
            "assetatlas_assets_updated_total",
            {"source": "integration"},
        )
        == updated + 7
    )
    assert (
        _sample(
            m.asset_generation_duration,
            "assetatlas_asset_generation_duration_seconds_count",
        )
        == dur + 1
    )


def test_asset_generation_stats_failure_counts_failed():
    before = _sample(
        m.asset_generation_runs,
        "assetatlas_asset_generation_runs_total",
        {"status": "failed"},
    )
    # A run that failed before change-tracking: no created/updated keys present.
    m.track_asset_generation_from_stats({"success": False, "pipeline_duration": 1.0})
    assert (
        _sample(
            m.asset_generation_runs,
            "assetatlas_asset_generation_runs_total",
            {"status": "failed"},
        )
        == before + 1
    )


# ---------------------------------------------------------------------------
# Live DB gauges  (DatabaseGaugeCollector / register_db_gauges)
# ---------------------------------------------------------------------------


class _FakeDB:
    """Minimal db_adapter stub: routes gauge queries by source table.

    ``assets_exc`` makes the matview query raise instead of returning rows —
    the real failure modes (unpopulated matview, lock timeout) are RAISES, not
    empty results.
    """

    def __init__(self, alerts_rows, assets_rows, fallback_rows=None,
                 assets_exc=None):
        self._alerts = alerts_rows
        self._assets = assets_rows
        self._fallback = fallback_rows or []
        self._assets_exc = assets_exc
        self.queries = []

    def execute_query(self, query, *args, **kwargs):
        self.queries.append(query)
        if "FROM alerts" in query:
            return self._alerts
        if "FROM mv_dashboard_summary" in query:
            if self._assets_exc is not None:
                raise self._assets_exc
            return self._assets
        if "FROM pipeline_runs" in query:
            return self._fallback
        return []


def test_db_gauge_queries_never_read_assets():
    """The SQL text is the behaviour here — a MagicMock DB never evaluates it."""
    assert "FROM mv_dashboard_summary" in m._ASSETS_TOTAL_SQL
    assert "FROM pipeline_runs" in m._ASSETS_TOTAL_FALLBACK_SQL
    # The fallback must pick the latest run that actually rebuilt `assets`:
    # completed (a failed run rolled back and never changed the table), with a
    # recorded count, excluding provable no-op completions.
    assert "status = 'completed'" in m._ASSETS_TOTAL_FALLBACK_SQL
    assert "stats->>'total_assets' IS NOT NULL" in m._ASSETS_TOTAL_FALLBACK_SQL
    assert "batches_processed" in m._ASSETS_TOTAL_FALLBACK_SQL
    # The gauge must never block on the pipeline's TRUNCATE assets lock.
    assert "FROM assets" not in m._ASSETS_TOTAL_SQL
    assert "FROM assets" not in m._ASSETS_TOTAL_FALLBACK_SQL


def test_db_gauge_collector_reports_alerts_by_severity_and_assets():
    db = _FakeDB(
        alerts_rows=[
            {"severity": "critical", "n": 2},
            {"severity": "high", "n": 5},
        ],
        assets_rows=[{"n": 1234}],
    )
    reg = CollectorRegistry()
    reg.register(m.DatabaseGaugeCollector(db))

    assert (
        reg.get_sample_value("assetatlas_active_alerts", {"severity": "critical"})
        == 2.0
    )
    assert reg.get_sample_value("assetatlas_active_alerts", {"severity": "high"}) == 5.0
    assert reg.get_sample_value("assetatlas_assets_total") == 1234.0
    # The matview answered, so the pipeline_runs fallback is never queried.
    assert not any("FROM pipeline_runs" in q for q in db.queries)
    # Asserted on what collect() actually handed to execute_query, not on the
    # module constants: every query must bound its lock wait in the SAME
    # execute string (a separate SET LOCAL is rolled back with the request
    # scope; a session-level SET leaks onto the pooled connection).
    assert db.queries and all(
        q.startswith("SET LOCAL lock_timeout = '2s';") for q in db.queries
    )


def test_db_gauge_collector_falls_back_when_matview_unusable():
    # The only two primary-query errors that mean "use pipeline_runs": the
    # fresh-install unpopulated matview (55000) and a DB from before the
    # matview's migration (42P01).
    for exc in (
        psycopg2.errors.ObjectNotInPrerequisiteState(
            'materialized view "mv_dashboard_summary" has not been populated'),
        psycopg2.errors.UndefinedTable(
            'relation "mv_dashboard_summary" does not exist'),
    ):
        db = _FakeDB(
            alerts_rows=[{"severity": "high", "n": 1}],
            assets_rows=[],
            fallback_rows=[{"n": "4321"}],
            assets_exc=exc,
        )
        reg = CollectorRegistry()
        reg.register(m.DatabaseGaugeCollector(db))

        assert reg.get_sample_value("assetatlas_assets_total") == 4321.0
        fallback_qs = [q for q in db.queries if "FROM pipeline_runs" in q]
        assert len(fallback_qs) == 1
        # The fallback SQL actually handed to the DB picks a completed run
        # that rebuilt `assets`, and is lock-bounded in the same string.
        assert "status = 'completed'" in fallback_qs[0]
        assert fallback_qs[0].startswith("SET LOCAL lock_timeout = '2s';")
        # The other gauge is unaffected.
        assert (
            reg.get_sample_value("assetatlas_active_alerts", {"severity": "high"})
            == 1.0
        )


def test_db_gauge_collector_lock_error_skips_fallback():
    # A lock/timeout on the matview says nothing about whether it is
    # populated: no fallback query, no sample for that scrape.
    for exc in (
        psycopg2.errors.LockNotAvailable("lock not available"),
        psycopg2.errors.QueryCanceled("statement timeout"),
    ):
        db = _FakeDB(
            alerts_rows=[{"severity": "high", "n": 1}],
            assets_rows=[],
            fallback_rows=[{"n": "4321"}],
            assets_exc=exc,
        )
        reg = CollectorRegistry()
        reg.register(m.DatabaseGaugeCollector(db))

        assert reg.get_sample_value("assetatlas_assets_total") is None
        assert not any("FROM pipeline_runs" in q for q in db.queries)
        assert (
            reg.get_sample_value("assetatlas_active_alerts", {"severity": "high"})
            == 1.0
        )


def test_db_gauge_collector_fallback_without_total_yields_no_sample():
    # The fallback ran (matview unpopulated) but no completed run carries a
    # total (stats nullable) -> no sample, never a misleading zero.
    for fallback_rows in ([], [{"n": None}]):
        db = _FakeDB(
            alerts_rows=[{"severity": "high", "n": 1}],
            assets_rows=[],
            fallback_rows=fallback_rows,
            assets_exc=psycopg2.errors.ObjectNotInPrerequisiteState(
                "not populated"),
        )
        reg = CollectorRegistry()
        reg.register(m.DatabaseGaugeCollector(db))

        assert reg.get_sample_value("assetatlas_assets_total") is None
        # The other gauge is unaffected.
        assert (
            reg.get_sample_value("assetatlas_active_alerts", {"severity": "high"})
            == 1.0
        )


def test_db_gauge_collector_one_failing_query_does_not_sink_the_other():
    class _AlertsBoomDB:
        def execute_query(self, query, *a, **k):
            if "FROM alerts" in query:
                raise RuntimeError("lock not available")
            if "FROM mv_dashboard_summary" in query:
                return [{"n": 77}]
            return []

    reg = CollectorRegistry()
    reg.register(m.DatabaseGaugeCollector(_AlertsBoomDB()))
    assert reg.get_sample_value("assetatlas_assets_total") == 77.0
    assert (
        reg.get_sample_value("assetatlas_active_alerts", {"severity": "high"})
        is None
    )

    class _AssetsBoomDB:
        def execute_query(self, query, *a, **k):
            if "FROM alerts" in query:
                return [{"severity": "low", "n": 3}]
            raise RuntimeError("lock not available")

    reg = CollectorRegistry()
    reg.register(m.DatabaseGaugeCollector(_AssetsBoomDB()))
    assert reg.get_sample_value("assetatlas_assets_total") is None
    assert reg.get_sample_value("assetatlas_active_alerts", {"severity": "low"}) == 3.0


def test_db_gauge_collector_is_best_effort_on_db_error():
    class _BoomDB:
        def execute_query(self, *a, **k):
            raise RuntimeError("db down")

    reg = CollectorRegistry()
    reg.register(m.DatabaseGaugeCollector(_BoomDB()))

    # Collection must not raise; a failed query yields no sample rather than a
    # misleading zero.
    assert reg.get_sample_value("assetatlas_assets_total") is None
    assert (
        reg.get_sample_value("assetatlas_active_alerts", {"severity": "high"}) is None
    )


def test_register_db_gauges_is_idempotent_and_repoints_adapter():
    # Start from a clean slate on the global registry.
    if m._db_gauge_collector is not None:
        try:
            REGISTRY.unregister(m._db_gauge_collector)
        except Exception:
            pass
        m._db_gauge_collector = None

    db1 = _FakeDB([{"severity": "high", "n": 1}], [{"n": 10}])
    db2 = _FakeDB([{"severity": "high", "n": 9}], [{"n": 99}])
    try:
        m.register_db_gauges(db1)
        first = m._db_gauge_collector
        # A second call must not raise a duplicate-collector error; it re-points.
        m.register_db_gauges(db2)
        assert m._db_gauge_collector is first
        assert first.db_adapter is db2
        assert REGISTRY.get_sample_value("assetatlas_assets_total") == 99.0
    finally:
        try:
            REGISTRY.unregister(m._db_gauge_collector)
        except Exception:
            pass
        m._db_gauge_collector = None


# ---------------------------------------------------------------------------
# DB query timing  (postgres_adapter.execute_query / _classify_query_type)
# ---------------------------------------------------------------------------


def test_classify_query_type():
    from src.database.postgres_adapter import _classify_query_type

    assert _classify_query_type("SELECT 1") == "select"
    assert _classify_query_type("  insert INTO t VALUES (1)") == "insert"
    assert _classify_query_type("UPDATE t SET x = 1") == "update"
    assert _classify_query_type("delete from t") == "delete"
    assert _classify_query_type("WITH cte AS (...) SELECT 1") == "other"


def test_execute_query_records_db_query_duration():
    from src.database.postgres_connection import PostgreSQLConnectionManager
    from src.database.postgres_adapter import PostgreSQLAdapter

    cm = PostgreSQLConnectionManager(database_url="postgresql://u:p@localhost:1/x")
    cm.is_scope_active = MagicMock(return_value=True)  # avoid a commit on the mock
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = []

    @contextmanager
    def fake_get_cursor(*a, **k):
        yield (cursor, conn)

    cm.get_cursor = fake_get_cursor
    adapter = PostgreSQLAdapter(cm)

    before = _sample(
        m.db_query_duration,
        "assetatlas_db_query_duration_seconds_count",
        {"query_type": "select"},
    )
    adapter.execute_query("SELECT 1", fetch=True)
    after = _sample(
        m.db_query_duration,
        "assetatlas_db_query_duration_seconds_count",
        {"query_type": "select"},
    )
    assert after == before + 1


# ---------------------------------------------------------------------------
# DB connection-pool gauges  (_publish_pool_metrics + acquire/release wiring)
# ---------------------------------------------------------------------------


def test_publish_pool_metrics_sets_size_and_available():
    from src.database.postgres_connection import _publish_pool_metrics

    class _Pool:
        closed = False
        maxconn = 20
        _used = {1: object(), 2: object(), 3: object()}  # 3 checked out

    _publish_pool_metrics(_Pool(), 20)
    assert (
        _sample(m.db_connection_pool_size, "assetatlas_db_connection_pool_size") == 20.0
    )
    assert (
        _sample(
            m.db_connection_pool_available, "assetatlas_db_connection_pool_available"
        )
        == 17.0
    )


def test_publish_pool_metrics_best_effort_on_closed_pool():
    from src.database.postgres_connection import _publish_pool_metrics

    class _Closed:
        closed = True

    # Must not raise and must leave the gauges as they were (no update).
    _publish_pool_metrics(_Closed(), 20)
    _publish_pool_metrics(None, 20)


def test_acquire_and_release_update_pool_gauges():
    from src.database.postgres_connection import PostgreSQLConnectionManager

    cm = PostgreSQLConnectionManager(database_url="postgresql://u:p@localhost:1/x")
    pool = MagicMock()
    pool.closed = False
    pool.maxconn = 10
    pool._used = {1: object()}  # 1 checked out
    conn = MagicMock()
    conn.closed = False
    pool.getconn.return_value = conn
    cm.get_pool = MagicMock(return_value=pool)
    cm._pool = pool

    got = cm._get_connection_with_retry()
    assert got is conn
    assert (
        _sample(m.db_connection_pool_size, "assetatlas_db_connection_pool_size") == 10.0
    )
    assert (
        _sample(
            m.db_connection_pool_available, "assetatlas_db_connection_pool_available"
        )
        == 9.0
    )

    # Releasing frees the slot; the release path re-publishes.
    pool._used.clear()
    cm._release_connection(conn)
    assert (
        _sample(
            m.db_connection_pool_available, "assetatlas_db_connection_pool_available"
        )
        == 10.0
    )


# ---------------------------------------------------------------------------
# Scheduler job outcomes  (SchedulerWorker._on_job_event)
# ---------------------------------------------------------------------------


def _worker_with_jobs(n_jobs):
    import src.scheduler_worker as sw

    worker = sw.SchedulerWorker.__new__(sw.SchedulerWorker)  # skip signal setup
    scheduler = MagicMock()
    scheduler.get_jobs.return_value = list(range(n_jobs))
    worker.scheduler_service = MagicMock(scheduler=scheduler)
    return worker


def test_on_job_event_success_records_outcome_and_jobs_total():
    worker = _worker_with_jobs(4)
    job_id = "job_metrics_ok_unique"
    event = MagicMock()
    event.job_id = job_id
    event.exception = None
    event.scheduled_run_time = datetime.now(timezone.utc)

    worker._on_job_event(event)

    assert (
        _sample(
            m.scheduled_job_executions,
            "assetatlas_scheduled_job_executions_total",
            {"job_id": job_id, "status": "success"},
        )
        == 1.0
    )
    assert (
        _sample(
            m.scheduled_job_duration,
            "assetatlas_scheduled_job_duration_seconds_count",
            {"job_id": job_id},
        )
        == 1.0
    )
    assert _sample(m.scheduled_jobs_total, "assetatlas_scheduled_jobs_total") == 4.0


def test_on_job_event_error_records_failed():
    worker = _worker_with_jobs(2)
    job_id = "job_metrics_fail_unique"
    event = MagicMock()
    event.job_id = job_id
    event.exception = RuntimeError("boom")
    event.scheduled_run_time = datetime.now(timezone.utc)

    worker._on_job_event(event)

    assert (
        _sample(
            m.scheduled_job_executions,
            "assetatlas_scheduled_job_executions_total",
            {"job_id": job_id, "status": "failed"},
        )
        == 1.0
    )


def test_on_job_event_never_raises_on_bad_event():
    worker = _worker_with_jobs(1)
    # A malformed event (get raises) must be swallowed, not propagated.
    worker._on_job_event(object())


# ---------------------------------------------------------------------------
# Integration executions  (IntegrationService.run_integration)
# ---------------------------------------------------------------------------


def _integration_service_with_mocks():
    import src.modules.integrations.services.integration_service as isvc

    svc = isvc.IntegrationService.__new__(isvc.IntegrationService)  # skip __init__
    svc.repository = MagicMock()
    svc.batch_repository = MagicMock()
    svc.raw_data_repository = MagicMock()
    svc.mapping_service = MagicMock()
    svc.conn_manager = MagicMock(database_url="postgresql://u:p@localhost:1/x")
    return svc, isvc


def _enabled_integration(name, connector_type):
    integ = MagicMock()
    integ.is_enabled.return_value = True
    integ.name = name
    integ.effective_connector_type = connector_type
    integ.config = {}
    integ.data_filters = []
    integ.field_mapping = None
    return integ


def _patch_lock(monkeypatch, isvc, acquired):
    lock_conn = MagicMock()
    lock_cur = MagicMock()
    lock_cur.fetchone.return_value = [acquired]
    lock_conn.cursor.return_value.__enter__.return_value = lock_cur
    monkeypatch.setattr(isvc.psycopg2, "connect", lambda *a, **k: lock_conn)


def test_run_integration_success_records_execution_and_records(monkeypatch):
    svc, isvc = _integration_service_with_mocks()
    iid = uuid.uuid4()
    name, ctype = "Test-Splunk", "splunk"
    svc.repository.get_by_id.return_value = _enabled_integration(name, ctype)
    svc.batch_repository.create_batch.return_value = "batch-1"
    _patch_lock(monkeypatch, isvc, acquired=True)

    connector = MagicMock()
    connector.fetch_data.return_value = iter([{"a": 1}, {"a": 2}, {"a": 3}])
    monkeypatch.setattr(
        isvc.ConnectorRegistry, "create_connector", lambda *a, **k: connector
    )

    result = svc.run_integration(iid)

    assert result["success"] is True
    assert result["record_count"] == 3

    exec_labels = {
        "integration_id": str(iid),
        "integration_name": name,
        "connector_type": ctype,
        "status": "success",
    }
    assert (
        _sample(
            m.integration_executions,
            "assetatlas_integration_executions_total",
            exec_labels,
        )
        == 1.0
    )
    assert (
        _sample(
            m.integration_records_processed,
            "assetatlas_integration_records_processed_total",
            {
                "integration_id": str(iid),
                "integration_name": name,
                "connector_type": ctype,
            },
        )
        == 3.0
    )


def test_run_integration_failure_records_failed(monkeypatch):
    svc, isvc = _integration_service_with_mocks()
    iid = uuid.uuid4()
    name, ctype = "Test-Fail", "custom_http"
    svc.repository.get_by_id.return_value = _enabled_integration(name, ctype)
    svc.batch_repository.create_batch.return_value = "batch-1"
    _patch_lock(monkeypatch, isvc, acquired=True)

    connector = MagicMock()
    connector.fetch_data.side_effect = RuntimeError("fetch boom")
    monkeypatch.setattr(
        isvc.ConnectorRegistry, "create_connector", lambda *a, **k: connector
    )

    result = svc.run_integration(iid)

    assert result["success"] is False
    assert (
        _sample(
            m.integration_executions,
            "assetatlas_integration_executions_total",
            {
                "integration_id": str(iid),
                "integration_name": name,
                "connector_type": ctype,
                "status": "failed",
            },
        )
        == 1.0
    )


def test_run_integration_already_running_emits_no_metric(monkeypatch):
    svc, isvc = _integration_service_with_mocks()
    iid = uuid.uuid4()
    name, ctype = "Test-Locked", "splunk"
    svc.repository.get_by_id.return_value = _enabled_integration(name, ctype)
    _patch_lock(monkeypatch, isvc, acquired=False)  # lock NOT acquired

    result = svc.run_integration(iid)

    assert result["error"] == "ALREADY_RUNNING"
    # The early return happens before the try/finally, so nothing is recorded
    # for this integration id under either status.
    for status in ("success", "failed"):
        assert (
            _sample(
                m.integration_executions,
                "assetatlas_integration_executions_total",
                {
                    "integration_id": str(iid),
                    "integration_name": name,
                    "connector_type": ctype,
                    "status": status,
                },
            )
            == 0.0
        )


# ---------------------------------------------------------------------------
# Pipeline stage timing  (track_stage_durations_from_stats)
# ---------------------------------------------------------------------------


def test_stage_durations_observed_per_stage():
    stage = "s99_stage_timing_unique"
    before_sum = _sample(
        m.pipeline_stage_duration,
        "assetatlas_pipeline_stage_duration_seconds_sum",
        {"stage": stage},
    )
    before_count = _sample(
        m.pipeline_stage_duration,
        "assetatlas_pipeline_stage_duration_seconds_count",
        {"stage": stage},
    )

    m.track_stage_durations_from_stats({"stages": {stage: {"duration": 2.5}}})

    assert (
        _sample(
            m.pipeline_stage_duration,
            "assetatlas_pipeline_stage_duration_seconds_sum",
            {"stage": stage},
        )
        == before_sum + 2.5
    )
    assert (
        _sample(
            m.pipeline_stage_duration,
            "assetatlas_pipeline_stage_duration_seconds_count",
            {"stage": stage},
        )
        == before_count + 1
    )


def test_stage_durations_skips_missing_and_non_numeric_durations():
    # Skipped / pre-error / malformed stages are ignored, and no empty label
    # child is created for them (bad value converted before .labels()).
    m.track_stage_durations_from_stats(
        {
            "stages": {
                "s_skipped_unique": {},
                "s_none_unique": {"duration": None},
                "s_bad_unique": {"duration": "oops"},
                "s_notdict_unique": 1.0,
            }
        }
    )
    for stage in (
        "s_skipped_unique",
        "s_none_unique",
        "s_bad_unique",
        "s_notdict_unique",
    ):
        assert (
            _sample(
                m.pipeline_stage_duration,
                "assetatlas_pipeline_stage_duration_seconds_count",
                {"stage": stage},
            )
            == 0.0
        )


def test_stage_durations_tolerates_missing_stages_key():
    # A stats dict without "stages" is a no-op, not a KeyError.
    m.track_stage_durations_from_stats({})


# ---------------------------------------------------------------------------
# HTTP in-flight gauge  (http_request_in_flight)
# ---------------------------------------------------------------------------


def test_http_request_in_flight_inc_dec_balances():
    before = _sample(m.http_request_in_flight, "assetatlas_http_request_in_flight")
    m.http_request_in_flight.inc()
    assert (
        _sample(m.http_request_in_flight, "assetatlas_http_request_in_flight")
        == before + 1
    )
    m.http_request_in_flight.dec()
    assert (
        _sample(m.http_request_in_flight, "assetatlas_http_request_in_flight") == before
    )
