# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the get_scheduler_status MCP tool."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.mcp.tools.scheduler import get_scheduler_status_impl


def _stat_row(**overrides):
    base = {
        "job_id": "job-a",
        "job_name": "Integration Sync",
        "total_executions": 10,
        "successful_executions": 9,
        "failed_executions": 1,
        "avg_duration_seconds": 12.5,
        "success_rate": 90.0,
        "last_execution_at": "2026-04-22T09:00:00+00:00",
        "last_success_at": "2026-04-22T09:00:00+00:00",
        "last_failure_at": "2026-04-20T09:00:00+00:00",
    }
    base.update(overrides)
    return base


def test_health_happy_path():
    svc = MagicMock()
    svc.get_health_status.return_value = {
        "status": "healthy",
        "running": True,
        "metrics": {"jobs_scheduled": 5},
        "uptime_seconds": 3600,
    }
    db = MagicMock()

    out = get_scheduler_status_impl(svc, db, scope="health")

    assert "healthy" in out
    assert "5" in out
    assert "running" in out.lower()


def test_health_service_raises_returns_generic_error_no_leak():
    # §4: a tool must never leak raw exception text into the transcript. The
    # health renderer catches and returns a generic message; the original
    # exception detail ("boom") must NOT appear (it goes to the server log).
    svc = MagicMock()
    svc.get_health_status.side_effect = RuntimeError("boom: secret db dsn")
    db = MagicMock()

    out = get_scheduler_status_impl(svc, db, scope="health")

    assert out.startswith("Scheduler health unavailable")
    assert "boom" not in out
    assert "dsn" not in out


def test_statistics_renders_rows():
    svc = MagicMock()
    db = MagicMock()
    rows = [
        _stat_row(job_id="job-a", job_name="Integration Sync"),
        _stat_row(job_id="job-b", job_name="Cleanup Job"),
    ]
    db.execute_query.return_value = rows

    out = get_scheduler_status_impl(svc, db, scope="statistics")

    assert "Integration Sync" in out
    assert "Cleanup Job" in out
    assert "| job_id |" in out


def test_statistics_job_id_filter_passes_param():
    svc = MagicMock()
    db = MagicMock()
    db.execute_query.return_value = []

    get_scheduler_status_impl(svc, db, scope="statistics", job_id="foo")

    call = db.execute_query.call_args_list[0]
    sql = call.args[0]
    params = call.args[1]
    assert "job_id = %(jid)s" in sql
    assert params["jid"] == "foo"


def test_alerts_with_stuck_and_failed():
    svc = MagicMock()
    db = MagicMock()
    stuck = [{
        "job_id": "job-a",
        "job_name": "Stuck Sync",
        "execution_id": "exec-1",
        "started_at": "2026-04-22T05:00:00+00:00",
        "status": "running",
        "seconds_running": 14400.0,
    }]
    failed = [{
        "job_id": "job-b",
        "job_name": "Broken Cleanup",
        "started_at": "2026-04-22T08:00:00+00:00",
        "error_message": "connection refused",
    }]
    db.execute_query.side_effect = [stuck, failed]

    out = get_scheduler_status_impl(svc, db, scope="alerts")

    assert "Stuck jobs" in out
    assert "Stuck Sync" in out
    assert "Recent failures" in out
    assert "Broken Cleanup" in out
    assert "connection refused" in out


def test_alerts_empty_returns_no_alerts_message():
    svc = MagicMock()
    db = MagicMock()
    db.execute_query.side_effect = [[], []]

    out = get_scheduler_status_impl(svc, db, scope="alerts")

    assert "No scheduler alerts" in out


def test_scope_all_includes_all_sections():
    svc = MagicMock()
    svc.get_health_status.return_value = {
        "status": "healthy",
        "running": True,
        "metrics": {"jobs_scheduled": 3},
    }
    db = MagicMock()
    db.execute_query.side_effect = [
        [_stat_row(job_id="job-a", job_name="Integration Sync")],  # statistics
        [],  # stuck
        [],  # failed
    ]

    out = get_scheduler_status_impl(svc, db, scope="all")

    assert "# Scheduler health" in out
    assert "# Statistics" in out
    assert "# Alerts" in out
    assert "healthy" in out
    assert "Integration Sync" in out


def test_invalid_scope_returns_error():
    svc = MagicMock()
    db = MagicMock()

    out = get_scheduler_status_impl(svc, db, scope="bogus")

    assert "Unknown scope 'bogus'" in out
    assert "all" in out
    assert "health" in out
    assert "statistics" in out
    assert "alerts" in out
    # New scopes are advertised in the error so the model can self-correct.
    assert "history" in out
    assert "job_health" in out


# --- history scope ----------------------------------------------------------

def test_history_renders_executions():
    svc = MagicMock()
    db = MagicMock()
    db.execute_query.return_value = [{
        "job_id": "job-a", "job_name": "Integration Sync", "status": "success",
        "started_at": "2026-04-22T09:00:00+00:00",
        "completed_at": "2026-04-22T09:01:00+00:00", "duration_seconds": 60.0,
        "records_processed": 12, "error_message": None, "attempt_number": 1,
    }]
    out = get_scheduler_status_impl(svc, db, scope="history")
    assert "Integration Sync" in out
    assert "success" in out
    sql = db.execute_query.call_args.args[0]
    assert "FROM job_executions" in sql


def test_history_empty():
    svc = MagicMock()
    db = MagicMock()
    db.execute_query.return_value = []
    out = get_scheduler_status_impl(svc, db, scope="history")
    assert "No job executions recorded." in out


def test_history_overflow_signal_from_window_count():
    # total_count (COUNT(*) OVER()) > rows returned ⇒ explicit overflow line +
    # the *true* total, never silent truncation (§4).
    svc = MagicMock()
    db = MagicMock()
    db.execute_query.return_value = [
        {"job_id": "j", "job_name": "n", "status": "success",
         "started_at": "x", "completed_at": "y", "duration_seconds": 1.0,
         "records_processed": 1, "error_message": None, "attempt_number": 1,
         "total_count": 500},
    ]
    out = get_scheduler_status_impl(svc, db, scope="history", limit=1)
    assert "of 500" in out
    assert "more not shown" in out


# --- job_health scope -------------------------------------------------------

def test_job_health_without_job_id_lists_jobs():
    svc = MagicMock()
    db = MagicMock()
    db.execute_query.return_value = [
        {"job_id": "job-a", "job_name": "Integration Sync"},
    ]
    out = get_scheduler_status_impl(svc, db, scope="job_health")
    assert "needs a `job_id`" in out
    assert "job-a" in out


def test_job_health_with_job_id_renders_health():
    svc = MagicMock()
    db = MagicMock()
    # 1st call: get_job_health_status(); 2nd: recent executions
    db.execute_query.side_effect = [
        [{"health_status": "warning", "recent_failures": 3,
          "success_rate": 70.0, "avg_duration": 5.0,
          "last_success": "2026-04-22T09:00:00+00:00",
          "last_failure": "2026-04-22T10:00:00+00:00", "is_stuck": False}],
        [{"status": "failed", "started_at": "2026-04-22T10:00:00+00:00",
          "duration_seconds": 2.0, "error_message": "kaboom"}],
    ]
    out = get_scheduler_status_impl(svc, db, scope="job_health", job_id="job-a")
    assert "Job health: job-a" in out
    assert "warning" in out
    assert "recent failures (24h): 3" in out
    assert "Recent executions" in out
    # The job-health call delegates to the canonical SQL function.
    first_sql = db.execute_query.call_args_list[0].args[0]
    assert "get_job_health_status" in first_sql


# --- raised caps + parameterized windows ------------------------------------

def test_limit_clamped_to_max():
    svc = MagicMock()
    db = MagicMock()
    db.execute_query.return_value = []
    get_scheduler_status_impl(svc, db, scope="history", limit=999999)
    params = db.execute_query.call_args.args[1]
    assert params["lim"] == 1000  # _MAX_LIMIT, not the requested 999999


def test_alert_windows_are_parameterized():
    svc = MagicMock()
    db = MagicMock()
    db.execute_query.side_effect = [[], []]
    out = get_scheduler_status_impl(svc, db, scope="alerts",
                                    stuck_hours=4, failure_hours=12)
    # Window values are bound params (not hardcoded 1h/24h) and reflected in
    # the empty-state message.
    stuck_params = db.execute_query.call_args_list[0].args[1]
    failed_params = db.execute_query.call_args_list[1].args[1]
    assert stuck_params["stuck_hours"] == 4
    assert failed_params["fail_hours"] == 12
    assert "stuck > 4h" in out
    assert "failures < 12h" in out


def test_invalid_window_is_loud_error():
    svc = MagicMock()
    db = MagicMock()
    out = get_scheduler_status_impl(svc, db, scope="alerts", stuck_hours=0)
    assert "Invalid stuck_hours" in out
    db.execute_query.assert_not_called()


def test_window_hours_string_coerced_to_float_for_sql():
    # An LLM that stringifies the number ("4") must not produce `'4' * INTERVAL`
    # (no text*interval operator). The validated value is bound as a float.
    svc = MagicMock()
    db = MagicMock()
    db.execute_query.side_effect = [[], []]
    get_scheduler_status_impl(svc, db, scope="alerts",
                              stuck_hours="4", failure_hours="12")
    stuck_params = db.execute_query.call_args_list[0].args[1]
    failed_params = db.execute_query.call_args_list[1].args[1]
    assert stuck_params["stuck_hours"] == 4.0
    assert isinstance(stuck_params["stuck_hours"], float)
    assert failed_params["fail_hours"] == 12.0
    assert isinstance(failed_params["fail_hours"], float)
