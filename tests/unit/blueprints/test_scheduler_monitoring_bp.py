# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the scheduler monitoring blueprint (admin auth required for most).

Endpoints tested:
- GET  /api/scheduler/health         — health (no auth for minimal, admin for full)
- GET  /api/scheduler/metrics        — Prometheus metrics (admin)
- GET  /api/scheduler/history        — job execution history (admin)
- GET  /api/scheduler/statistics     — aggregated job stats (admin)
- GET  /api/scheduler/alerts         — scheduler alerts (admin)
- GET  /api/scheduler/jobs/<id>/health — per-job health (admin)
- POST /api/scheduler/refresh-stats  — refresh materialized view (admin)
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock


@pytest.fixture(autouse=True)
def _restore_scheduler_config(blueprint_app):
    """Save and restore SCHEDULER_SERVICE so config mutations don't leak."""
    original = blueprint_app.config.get("SCHEDULER_SERVICE")
    yield
    if original is not None:
        blueprint_app.config["SCHEDULER_SERVICE"] = original
    else:
        blueprint_app.config.pop("SCHEDULER_SERVICE", None)


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------

class TestSchedulerMonitoringAuth:
    """Most scheduler endpoints require admin JWT; /health is partially open."""

    @pytest.mark.parametrize("method,path", [
        ("GET",  "/api/scheduler/metrics"),
        ("GET",  "/api/scheduler/history"),
        ("GET",  "/api/scheduler/statistics"),
        ("GET",  "/api/scheduler/alerts"),
        ("GET",  "/api/scheduler/jobs/some-job/health"),
        ("POST", "/api/scheduler/refresh-stats"),
    ])
    def test_no_auth_returns_401(self, client, method, path):
        resp = getattr(client, method.lower())(path)
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path", [
        ("GET",  "/api/scheduler/metrics"),
        ("GET",  "/api/scheduler/history"),
        ("GET",  "/api/scheduler/statistics"),
        ("GET",  "/api/scheduler/alerts"),
        ("GET",  "/api/scheduler/jobs/some-job/health"),
        ("POST", "/api/scheduler/refresh-stats"),
    ])
    def test_user_role_returns_403(self, client, authed_headers, method, path):
        resp = getattr(client, method.lower())(path, headers=authed_headers("user"))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/scheduler/health — unauthenticated returns minimal info
# ---------------------------------------------------------------------------

class TestSchedulerHealth:
    """GET /api/scheduler/health — health check with optional auth."""

    def test_no_auth_returns_minimal_status(self, client, blueprint_app):
        """Unauthenticated requests get minimal status/running fields."""
        blueprint_app.config["SCHEDULER_SERVICE"] = MagicMock()
        blueprint_app.config["SCHEDULER_SERVICE"].get_health_status.return_value = {
            "status": "healthy",
            "running": True,
            "metrics": {"jobs_scheduled": 5},
        }

        resp = client.get("/api/scheduler/health")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "healthy"
        assert body["running"] is True
        # Minimal: should NOT include metrics when unauthenticated
        assert "metrics" not in body

    def test_no_scheduler_service_returns_503(self, client, blueprint_app):
        """Missing scheduler service returns 503."""
        blueprint_app.config.pop("SCHEDULER_SERVICE", None)

        resp = client.get("/api/scheduler/health")
        assert resp.status_code == 503
        body = resp.get_json()
        assert body["status"] == "error"

    def test_authenticated_returns_full_health(self, client, authed_headers, blueprint_app):
        """Admin user gets full health details."""
        health_data = {
            "status": "healthy",
            "running": True,
            "metrics": {"jobs_scheduled": 5, "jobs_executed": 100},
            "circuit_breaker": {"open": False},
        }
        blueprint_app.config["SCHEDULER_SERVICE"] = MagicMock()
        blueprint_app.config["SCHEDULER_SERVICE"].get_health_status.return_value = (
            health_data
        )

        resp = client.get(
            "/api/scheduler/health", headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "healthy"
        assert body["metrics"]["jobs_scheduled"] == 5

    def test_degraded_status_returns_200(self, client, authed_headers, blueprint_app):
        blueprint_app.config["SCHEDULER_SERVICE"] = MagicMock()
        blueprint_app.config["SCHEDULER_SERVICE"].get_health_status.return_value = {
            "status": "degraded",
            "running": True,
        }

        resp = client.get(
            "/api/scheduler/health", headers=authed_headers("admin"),
        )
        assert resp.status_code == 200

    def test_error_status_returns_503(self, client, authed_headers, blueprint_app):
        blueprint_app.config["SCHEDULER_SERVICE"] = MagicMock()
        blueprint_app.config["SCHEDULER_SERVICE"].get_health_status.return_value = {
            "status": "error",
            "running": False,
            "error": "Scheduler crashed",
        }

        resp = client.get(
            "/api/scheduler/health", headers=authed_headers("admin"),
        )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/scheduler/metrics — Prometheus text format
# ---------------------------------------------------------------------------

class TestSchedulerMetrics:
    """GET /api/scheduler/metrics — Prometheus-compatible metrics."""

    def test_returns_prometheus_format(self, client, authed_headers, blueprint_app):
        blueprint_app.config["SCHEDULER_SERVICE"] = MagicMock()
        blueprint_app.config["SCHEDULER_SERVICE"].get_health_status.return_value = {
            "status": "healthy",
            "running": True,
            "metrics": {
                "jobs_scheduled": 5,
                "jobs_executed": 100,
                "jobs_failed": 2,
                "jobs_timeout": 1,
                "jobs_missed": 0,
                "failure_rate": 2.0,
                "avg_execution_time": 15.5,
            },
            "circuit_breaker": {"open": False},
        }

        resp = client.get(
            "/api/scheduler/metrics", headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        assert "text/plain" in resp.content_type

        text = resp.data.decode()
        assert "scheduler_up 1" in text
        assert "scheduler_jobs_scheduled 5" in text
        assert "scheduler_jobs_executed_total 100" in text
        assert "scheduler_jobs_failed_total 2" in text
        assert "scheduler_circuit_breaker_open 0" in text

    def test_no_scheduler_returns_503(self, client, authed_headers, blueprint_app):
        blueprint_app.config.pop("SCHEDULER_SERVICE", None)

        resp = client.get(
            "/api/scheduler/metrics", headers=authed_headers("admin"),
        )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/scheduler/history — job execution history (hits DB via monitor)
# ---------------------------------------------------------------------------

class TestJobHistory:
    """GET /api/scheduler/history — job execution history."""

    def test_returns_history(self, client, authed_headers, blueprint_app):
        mock_history = [
            {
                "id": "exec-1",
                "job_id": "pipeline_run",
                "status": "success",
                "started_at": "2025-06-01T10:00:00",
            },
        ]
        with patch(
            "src.api.blueprints.scheduler_monitoring.monitor.get_job_execution_history",
            return_value=mock_history,
        ):
            resp = client.get(
                "/api/scheduler/history", headers=authed_headers("admin"),
            )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["count"] == 1
        assert body["executions"][0]["job_id"] == "pipeline_run"

    def test_filter_by_job_id(self, client, authed_headers, blueprint_app):
        with patch(
            "src.api.blueprints.scheduler_monitoring.monitor.get_job_execution_history",
            return_value=[],
        ) as mock_hist:
            client.get(
                "/api/scheduler/history?job_id=pipeline_run&limit=50",
                headers=authed_headers("admin"),
            )
            mock_hist.assert_called_once_with("pipeline_run", 50)

    def test_limit_clamped_to_1000(self, client, authed_headers, blueprint_app):
        with patch(
            "src.api.blueprints.scheduler_monitoring.monitor.get_job_execution_history",
            return_value=[],
        ) as mock_hist:
            client.get(
                "/api/scheduler/history?limit=9999",
                headers=authed_headers("admin"),
            )
            mock_hist.assert_called_once_with(None, 1000)

    def test_default_limit_is_100(self, client, authed_headers, blueprint_app):
        with patch(
            "src.api.blueprints.scheduler_monitoring.monitor.get_job_execution_history",
            return_value=[],
        ) as mock_hist:
            client.get(
                "/api/scheduler/history", headers=authed_headers("admin"),
            )
            mock_hist.assert_called_once_with(None, 100)

    def test_db_error_returns_500(self, client, authed_headers, blueprint_app):
        with patch(
            "src.api.blueprints.scheduler_monitoring.monitor.get_job_execution_history",
            side_effect=RuntimeError("connection lost"),
        ):
            resp = client.get(
                "/api/scheduler/history", headers=authed_headers("admin"),
            )
        assert resp.status_code == 500
        body = resp.get_json()
        assert "error" in body


# ---------------------------------------------------------------------------
# GET /api/scheduler/statistics
# ---------------------------------------------------------------------------

class TestJobStatistics:
    """GET /api/scheduler/statistics — aggregated job stats."""

    def test_returns_statistics(self, client, authed_headers, blueprint_app):
        mock_stats = {
            "statistics": [
                {
                    "job_id": "pipeline_run",
                    "total_executions": 50,
                    "success_rate": 96.0,
                },
            ],
        }
        with patch(
            "src.api.blueprints.scheduler_monitoring.monitor.get_job_statistics",
            return_value=mock_stats,
        ):
            resp = client.get(
                "/api/scheduler/statistics", headers=authed_headers("admin"),
            )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["statistics"][0]["job_id"] == "pipeline_run"

    def test_filter_by_job_id(self, client, authed_headers, blueprint_app):
        with patch(
            "src.api.blueprints.scheduler_monitoring.monitor.get_job_statistics",
            return_value={},
        ) as mock_stats:
            client.get(
                "/api/scheduler/statistics?job_id=cleanup",
                headers=authed_headers("admin"),
            )
            mock_stats.assert_called_once_with("cleanup")

    def test_db_error_returns_500(self, client, authed_headers, blueprint_app):
        with patch(
            "src.api.blueprints.scheduler_monitoring.monitor.get_job_statistics",
            side_effect=RuntimeError("db down"),
        ):
            resp = client.get(
                "/api/scheduler/statistics", headers=authed_headers("admin"),
            )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/scheduler/alerts
# ---------------------------------------------------------------------------

class TestSchedulerAlerts:
    """GET /api/scheduler/alerts — stuck jobs, failures, and warnings."""

    def test_returns_alerts_structure(self, client, authed_headers, blueprint_app):
        blueprint_app.config["SCHEDULER_SERVICE"] = MagicMock()
        blueprint_app.config["SCHEDULER_SERVICE"].get_health_status.return_value = {
            "status": "healthy",
        }

        with patch(
            "src.api.blueprints.scheduler_monitoring.monitor.get_stuck_jobs",
            return_value=[],
        ), patch(
            "src.api.blueprints.scheduler_monitoring.monitor.get_failed_jobs",
            return_value=[],
        ):
            resp = client.get(
                "/api/scheduler/alerts", headers=authed_headers("admin"),
            )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "timestamp" in body
        assert body["stuck_jobs"] == []
        assert body["failed_jobs"] == []
        assert body["alert_count"] == 0
        assert body["has_critical"] is False

    def test_stuck_jobs_generate_critical_warning(
        self, client, authed_headers, blueprint_app,
    ):
        stuck = [
            {
                "job_id": "pipeline_run",
                "job_name": "Pipeline",
                "seconds_running": 7200.0,
            },
        ]
        blueprint_app.config["SCHEDULER_SERVICE"] = MagicMock()
        blueprint_app.config["SCHEDULER_SERVICE"].get_health_status.return_value = {
            "status": "healthy",
        }

        with patch(
            "src.api.blueprints.scheduler_monitoring.monitor.get_stuck_jobs",
            return_value=stuck,
        ), patch(
            "src.api.blueprints.scheduler_monitoring.monitor.get_failed_jobs",
            return_value=[],
        ):
            resp = client.get(
                "/api/scheduler/alerts", headers=authed_headers("admin"),
            )
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body["stuck_jobs"]) == 1
        assert body["has_critical"] is True

    def test_repeated_failures_generate_warning(
        self, client, authed_headers, blueprint_app,
    ):
        # 3+ failures from same job triggers a warning
        failures = [
            {"job_id": "cleanup", "error_message": f"fail {i}"}
            for i in range(4)
        ]
        blueprint_app.config["SCHEDULER_SERVICE"] = MagicMock()
        blueprint_app.config["SCHEDULER_SERVICE"].get_health_status.return_value = {
            "status": "healthy",
        }

        with patch(
            "src.api.blueprints.scheduler_monitoring.monitor.get_stuck_jobs",
            return_value=[],
        ), patch(
            "src.api.blueprints.scheduler_monitoring.monitor.get_failed_jobs",
            return_value=failures,
        ):
            resp = client.get(
                "/api/scheduler/alerts", headers=authed_headers("admin"),
            )
        body = resp.get_json()
        warnings = [w for w in body["warnings"] if "cleanup" in w.get("message", "")]
        assert len(warnings) == 1
        assert warnings[0]["failure_count"] == 4


# ---------------------------------------------------------------------------
# POST /api/scheduler/refresh-stats
# ---------------------------------------------------------------------------

class TestRefreshStatistics:
    """POST /api/scheduler/refresh-stats — refresh materialized view."""

    def test_refresh_success(self, client, authed_headers, blueprint_app):
        with patch(
            "src.api.blueprints.scheduler_monitoring.get_db_connection",
        ) as mock_get_conn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value.__enter__ = MagicMock(
                return_value=mock_cursor,
            )
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            mock_get_conn.return_value = mock_conn

            resp = client.post(
                "/api/scheduler/refresh-stats", headers=authed_headers("admin"),
            )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["message"] == "Statistics refreshed successfully"

    def test_refresh_db_error_returns_500(self, client, authed_headers, blueprint_app):
        with patch(
            "src.api.blueprints.scheduler_monitoring.get_db_connection",
            side_effect=RuntimeError("connection pool exhausted"),
        ):
            resp = client.post(
                "/api/scheduler/refresh-stats", headers=authed_headers("admin"),
            )
        assert resp.status_code == 500
        body = resp.get_json()
        assert "error" in body
