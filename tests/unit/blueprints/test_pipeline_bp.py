# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the pipeline blueprint (auth required; admin except where noted).

Endpoints tested:
- POST /api/pipeline/trigger              — Trigger a pipeline run (admin)
- GET  /api/pipeline/status               — Get current pipeline status (admin)
- GET  /api/pipeline/runs                 — Paginated run history (admin)
- GET  /api/pipeline/runs/<run_id>        — Single run details (admin)
- GET  /api/pipeline/runs/<run_id>/phases — 4-phase view (admin)
- GET  /api/pipeline/data-version         — Cache-coherency signal (ANY role)
"""

import uuid
import pytest
from unittest.mock import MagicMock


# =============================================================================
# Auth / RBAC
# =============================================================================

class TestPipelineAuth:
    """All pipeline endpoints require JWT + admin role."""

    def test_trigger_requires_auth(self, client):
        resp = client.post("/api/pipeline/trigger")
        assert resp.status_code == 401

    def test_status_requires_auth(self, client):
        resp = client.get("/api/pipeline/status")
        assert resp.status_code == 401

    def test_runs_requires_auth(self, client):
        resp = client.get("/api/pipeline/runs")
        assert resp.status_code == 401

    def test_trigger_forbidden_for_user(self, client, auth_headers, _prime_regular_user):
        resp = client.post("/api/pipeline/trigger", headers=auth_headers("user"))
        assert resp.status_code == 403

    def test_status_forbidden_for_user(self, client, auth_headers, _prime_regular_user):
        resp = client.get("/api/pipeline/status", headers=auth_headers("user"))
        assert resp.status_code == 403

    def test_runs_forbidden_for_user(self, client, auth_headers, _prime_regular_user):
        resp = client.get("/api/pipeline/runs", headers=auth_headers("user"))
        assert resp.status_code == 403


# =============================================================================
# POST /api/pipeline/trigger
# =============================================================================

class TestTriggerPipeline:
    """POST /api/pipeline/trigger -- admin triggers a pipeline run."""

    def test_trigger_success_returns_202(self, client, auth_headers, blueprint_app):
        blueprint_app.pipeline_service.trigger_run.return_value = {
            "success": True,
            "run_id": "run-123",
        }
        resp = client.post("/api/pipeline/trigger", headers=auth_headers("admin"))
        assert resp.status_code == 202
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["run_id"] == "run-123"

    def test_trigger_conflict_returns_409(self, client, auth_headers, blueprint_app):
        blueprint_app.pipeline_service.trigger_run.return_value = {
            "success": False,
            "error": "Pipeline already running",
            "run_id": "run-existing",
        }
        resp = client.post("/api/pipeline/trigger", headers=auth_headers("admin"))
        assert resp.status_code == 409
        body = resp.get_json()
        assert body["success"] is False

    def test_trigger_called_with_manual(self, client, auth_headers, blueprint_app):
        blueprint_app.pipeline_service.trigger_run.return_value = {
            "success": True, "run_id": "x",
        }
        client.post("/api/pipeline/trigger", headers=auth_headers("admin"))
        blueprint_app.pipeline_service.trigger_run.assert_called_once_with(
            triggered_by="manual"
        )


# =============================================================================
# GET /api/pipeline/status
# =============================================================================

class TestPipelineStatus:
    """GET /api/pipeline/status -- current pipeline status."""

    def test_status_returns_200(self, client, auth_headers, blueprint_app):
        blueprint_app.pipeline_service.get_current_status.return_value = {
            "status": "idle",
            "last_run_at": "2025-06-01T00:00:00Z",
        }
        # _get_pipeline_next_run calls _get_setting which is a MagicMock.
        # Make it return False for 'pipeline_schedule_enabled' so the helper
        # short-circuits and returns None (no next_run).
        blueprint_app.pipeline_service._get_setting.return_value = False
        resp = client.get("/api/pipeline/status", headers=auth_headers("admin"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["status"] == "idle"


# =============================================================================
# GET /api/pipeline/runs
# =============================================================================

class TestPipelineRuns:
    """GET /api/pipeline/runs -- paginated run history."""

    def test_runs_returns_200(self, client, auth_headers, blueprint_app):
        blueprint_app.pipeline_service.get_runs.return_value = {
            "runs": [],
            "total": 0,
            "page": 1,
            "per_page": 20,
        }
        resp = client.get("/api/pipeline/runs", headers=auth_headers("admin"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["runs"] == []

    def test_runs_pagination_params(self, client, auth_headers, blueprint_app):
        blueprint_app.pipeline_service.get_runs.return_value = {"runs": [], "total": 0}
        client.get("/api/pipeline/runs?page=3&per_page=10", headers=auth_headers("admin"))
        call_kwargs = blueprint_app.pipeline_service.get_runs.call_args[1]
        assert call_kwargs["page"] == 3
        assert call_kwargs["per_page"] == 10

    def test_runs_per_page_capped_at_100(self, client, auth_headers, blueprint_app):
        blueprint_app.pipeline_service.get_runs.return_value = {"runs": [], "total": 0}
        client.get("/api/pipeline/runs?per_page=500", headers=auth_headers("admin"))
        call_kwargs = blueprint_app.pipeline_service.get_runs.call_args[1]
        assert call_kwargs["per_page"] == 100


# =============================================================================
# GET /api/pipeline/runs/<run_id>
# =============================================================================

class TestPipelineRunDetail:
    """GET /api/pipeline/runs/<run_id> -- single run details."""

    def test_get_run_returns_200(self, client, auth_headers, blueprint_app):
        run_id = str(uuid.uuid4())
        blueprint_app.pipeline_service.get_run.return_value = {
            "run_id": run_id,
            "status": "completed",
        }
        resp = client.get(f"/api/pipeline/runs/{run_id}", headers=auth_headers("admin"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["run_id"] == run_id

    def test_get_run_not_found_returns_404(self, client, auth_headers, blueprint_app):
        run_id = str(uuid.uuid4())
        blueprint_app.pipeline_service.get_run.return_value = None
        resp = client.get(f"/api/pipeline/runs/{run_id}", headers=auth_headers("admin"))
        assert resp.status_code == 404

    def test_invalid_uuid_returns_404(self, client, auth_headers):
        resp = client.get("/api/pipeline/runs/not-a-uuid", headers=auth_headers("admin"))
        assert resp.status_code == 404


# =============================================================================
# GET /api/pipeline/runs/<run_id>/phases
# =============================================================================

class TestPipelineRunPhases:
    """GET /api/pipeline/runs/<run_id>/phases -- 4-phase operator view."""

    def test_phases_requires_auth(self, client):
        run_id = str(uuid.uuid4())
        resp = client.get(f"/api/pipeline/runs/{run_id}/phases")
        assert resp.status_code == 401

    def test_phases_forbidden_for_user(self, client, auth_headers, _prime_regular_user):
        run_id = str(uuid.uuid4())
        resp = client.get(
            f"/api/pipeline/runs/{run_id}/phases",
            headers=auth_headers("user"),
        )
        assert resp.status_code == 403

    def test_phases_returns_200_with_payload(self, client, auth_headers, blueprint_app):
        run_id = str(uuid.uuid4())
        blueprint_app.pipeline_phase_service.get_phases_for_run.return_value = {
            "run_id": run_id,
            "status": "completed",
            "phases": [
                {"key": "ingest_identify", "label": "Ingest & Identify",
                 "duration_seconds": 1.0, "status": "success",
                 "sub_stages": [], "has_slow_stages": False},
            ],
        }
        resp = client.get(
            f"/api/pipeline/runs/{run_id}/phases",
            headers=auth_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["run_id"] == run_id
        assert body["data"]["phases"][0]["key"] == "ingest_identify"

    def test_phases_unknown_run_returns_404(self, client, auth_headers, blueprint_app):
        run_id = str(uuid.uuid4())
        blueprint_app.pipeline_phase_service.get_phases_for_run.return_value = None
        resp = client.get(
            f"/api/pipeline/runs/{run_id}/phases",
            headers=auth_headers("admin"),
        )
        assert resp.status_code == 404

    def test_phases_invalid_uuid_returns_404(self, client, auth_headers):
        resp = client.get(
            "/api/pipeline/runs/not-a-uuid/phases",
            headers=auth_headers("admin"),
        )
        assert resp.status_code == 404


# =============================================================================
# GET /api/pipeline/data-version
# =============================================================================

class TestPipelineDataVersion:
    """GET /api/pipeline/data-version -- the app-wide cache-coherency signal.

    The one pipeline route that is NOT admin-gated, and deliberately so: every
    open browser tab polls it to learn that a run rebuilt the asset table, and
    the operators who live in Explorer hold the plain `user` role. Adding
    @require_admin() here would 403 every poll for exactly the people the
    endpoint exists to serve, leaving them on pre-run data.
    """

    VERSION = {
        "run_id": "3f0c7e5e-0000-4000-8000-000000000001",
        "completed_at": "2026-07-29T10:30:00+00:00",
        "status": "completed",
    }

    def test_requires_auth(self, client):
        resp = client.get("/api/pipeline/data-version")
        assert resp.status_code == 401

    def test_regular_user_gets_200_not_403(
        self, client, auth_headers, blueprint_app, _prime_regular_user,
    ):
        blueprint_app.pipeline_service.get_data_version.return_value = self.VERSION
        resp = client.get("/api/pipeline/data-version", headers=auth_headers("user"))
        assert resp.status_code == 200, (
            "a non-admin must be able to poll the data-version signal; every "
            "Explorer user needs it to notice a pipeline run"
        )
        assert resp.get_json()["data"]["run_id"] == self.VERSION["run_id"]

    def test_returns_the_version_payload(self, client, auth_headers, blueprint_app):
        blueprint_app.pipeline_service.get_data_version.return_value = self.VERSION
        resp = client.get("/api/pipeline/data-version", headers=auth_headers("admin"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"] == self.VERSION

    def test_fresh_install_returns_all_nulls(self, client, auth_headers, blueprint_app):
        """No run has ever finished — an empty answer, not a 500."""
        blueprint_app.pipeline_service.get_data_version.return_value = {
            "run_id": None, "completed_at": None, "status": None,
        }
        resp = client.get("/api/pipeline/data-version", headers=auth_headers("admin"))
        assert resp.status_code == 200
        assert resp.get_json()["data"]["run_id"] is None

    def test_reports_a_failed_run(self, client, auth_headers, blueprint_app):
        blueprint_app.pipeline_service.get_data_version.return_value = {
            "run_id": "3f0c7e5e-0000-4000-8000-000000000002",
            "completed_at": "2026-07-29T11:00:00+00:00",
            "status": "failed",
        }
        resp = client.get("/api/pipeline/data-version", headers=auth_headers("admin"))
        assert resp.get_json()["data"]["status"] == "failed"
