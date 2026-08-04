# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the schedules blueprint (admin-only).

All endpoints live under /api/integrations/<id>/schedule and require admin role.

Endpoints tested:
- GET  /api/integrations/<id>/schedule            — get schedule config
- PUT  /api/integrations/<id>/schedule            — update schedule
- PATCH /api/integrations/<id>/schedule/enable    — enable schedule
- PATCH /api/integrations/<id>/schedule/disable   — disable schedule
- POST /api/integrations/<id>/schedule/trigger    — trigger immediate run
- GET  /api/integrations/<id>/schedule/next-runs  — get next run times
- GET  /api/integrations/scheduler/status         — scheduler status
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

INTEGRATION_ID = "11111111-1111-1111-1111-111111111111"


def _make_integration(**overrides):
    """Create a MagicMock that behaves like an integration domain object."""
    integration = MagicMock()
    integration.id = overrides.get("id", INTEGRATION_ID)
    integration.name = overrides.get("name", "Test Jamf")
    integration.schedule_enabled = overrides.get("schedule_enabled", True)
    integration.schedule_cron = overrides.get("schedule_cron", "0 */6 * * *")
    integration.schedule_timezone = overrides.get("schedule_timezone", "UTC")
    integration.next_run_at = overrides.get("next_run_at", datetime(2025, 6, 1, 6, 0))
    integration.is_running = overrides.get("is_running", False)
    return integration


# =============================================================================
# Auth / RBAC
# =============================================================================

class TestSchedulesAuth:
    """All schedule endpoints require JWT + admin role."""

    @pytest.mark.parametrize("method,path", [
        ("GET",   f"/api/integrations/{INTEGRATION_ID}/schedule"),
        ("PUT",   f"/api/integrations/{INTEGRATION_ID}/schedule"),
        ("PATCH", f"/api/integrations/{INTEGRATION_ID}/schedule/enable"),
        ("PATCH", f"/api/integrations/{INTEGRATION_ID}/schedule/disable"),
        ("POST",  f"/api/integrations/{INTEGRATION_ID}/schedule/trigger"),
        ("GET",   f"/api/integrations/{INTEGRATION_ID}/schedule/next-runs"),
        ("GET",   "/api/integrations/scheduler/status"),
    ])
    def test_no_auth_returns_401(self, client, method, path):
        resp = getattr(client, method.lower())(path)
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path", [
        ("GET",   f"/api/integrations/{INTEGRATION_ID}/schedule"),
        ("PUT",   f"/api/integrations/{INTEGRATION_ID}/schedule"),
        ("PATCH", f"/api/integrations/{INTEGRATION_ID}/schedule/enable"),
        ("PATCH", f"/api/integrations/{INTEGRATION_ID}/schedule/disable"),
        ("POST",  f"/api/integrations/{INTEGRATION_ID}/schedule/trigger"),
        ("GET",   f"/api/integrations/{INTEGRATION_ID}/schedule/next-runs"),
        ("GET",   "/api/integrations/scheduler/status"),
    ])
    def test_non_admin_returns_403(self, client, auth_headers, _prime_regular_user, method, path):
        resp = getattr(client, method.lower())(path, headers=auth_headers("user"))
        assert resp.status_code == 403


# =============================================================================
# Get schedule (GET /api/integrations/<id>/schedule)
# =============================================================================

class TestGetSchedule:
    """GET /api/integrations/<id>/schedule"""

    @patch("src.api.blueprints.integrations.schedules.get_cron_description")
    @patch("src.api.blueprints.integrations.schedules.validate_cron_with_details")
    def test_get_schedule_success(self, mock_validate, mock_desc, client, auth_headers, blueprint_app):
        mock_desc.return_value = "Every 6 hours"
        mock_validate.return_value = {
            "valid": True,
            "next_5_runs": ["2025-06-01T06:00:00", "2025-06-01T12:00:00"],
        }
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/schedule",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["schedule"]["enabled"] is True
        assert body["schedule"]["cron"] == "0 */6 * * *"
        assert body["schedule"]["description"] == "Every 6 hours"

    def test_get_schedule_not_found(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_integration.return_value = None

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/schedule",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404

    def test_get_schedule_no_cron(self, client, auth_headers, blueprint_app):
        """Integration without a cron expression should not have description."""
        integration = _make_integration(schedule_cron=None)
        blueprint_app.integration_service.get_integration.return_value = integration

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/schedule",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert "description" not in body["schedule"]


# =============================================================================
# Update schedule (PUT /api/integrations/<id>/schedule)
# =============================================================================

class TestUpdateSchedule:
    """PUT /api/integrations/<id>/schedule"""

    @patch("src.api.blueprints.integrations.schedules.validate_cron_with_details")
    def test_update_schedule_success(self, mock_validate, client, auth_headers, blueprint_app):
        mock_validate.return_value = {
            "valid": True,
            "description": "Every hour",
        }
        blueprint_app.scheduler_service.schedule_integration.return_value = {
            "success": True
        }
        integration = _make_integration(schedule_cron="0 * * * *")
        blueprint_app.integration_service.get_integration.return_value = integration

        resp = client.put(
            f"/api/integrations/{INTEGRATION_ID}/schedule",
            json={"cron": "0 * * * *", "timezone": "UTC", "enabled": True},
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True

    @patch("src.api.blueprints.integrations.schedules.validate_cron_with_details")
    def test_update_invalid_cron(self, mock_validate, client, auth_headers):
        mock_validate.return_value = {
            "valid": False,
            "message": "Invalid cron expression",
        }

        resp = client.put(
            f"/api/integrations/{INTEGRATION_ID}/schedule",
            json={"cron": "bad cron", "timezone": "UTC"},
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 400

    def test_update_missing_cron(self, client, auth_headers):
        resp = client.put(
            f"/api/integrations/{INTEGRATION_ID}/schedule",
            json={"timezone": "UTC"},
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 400

    @patch("src.api.blueprints.integrations.schedules.validate_cron_with_details")
    def test_update_disabled_schedule(self, mock_validate, client, auth_headers, blueprint_app):
        """When enabled=false, the endpoint unschedules instead."""
        mock_validate.return_value = {"valid": True, "description": "Every hour"}
        blueprint_app.scheduler_service.unschedule_integration.return_value = {
            "success": True
        }
        integration = _make_integration(schedule_enabled=False, schedule_cron="0 * * * *")
        blueprint_app.integration_service.get_integration.return_value = integration

        resp = client.put(
            f"/api/integrations/{INTEGRATION_ID}/schedule",
            json={"cron": "0 * * * *", "enabled": False},
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        blueprint_app.scheduler_service.unschedule_integration.assert_called_once()


# =============================================================================
# Enable schedule (PATCH /api/integrations/<id>/schedule/enable)
# =============================================================================

class TestEnableSchedule:
    """PATCH /api/integrations/<id>/schedule/enable"""

    def test_enable_success(self, client, auth_headers, blueprint_app):
        integration = _make_integration(schedule_cron="0 * * * *")
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.scheduler_service.schedule_integration.return_value = {
            "success": True, "next_run_at": "2025-06-01T07:00:00"
        }

        resp = client.patch(
            f"/api/integrations/{INTEGRATION_ID}/schedule/enable",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_enable_no_cron_configured(self, client, auth_headers, blueprint_app):
        integration = _make_integration(schedule_cron=None)
        blueprint_app.integration_service.get_integration.return_value = integration

        resp = client.patch(
            f"/api/integrations/{INTEGRATION_ID}/schedule/enable",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 400

    def test_enable_not_found(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_integration.return_value = None

        resp = client.patch(
            f"/api/integrations/{INTEGRATION_ID}/schedule/enable",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404


# =============================================================================
# Disable schedule (PATCH /api/integrations/<id>/schedule/disable)
# =============================================================================

class TestDisableSchedule:
    """PATCH /api/integrations/<id>/schedule/disable"""

    def test_disable_success(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.scheduler_service.unschedule_integration.return_value = {
            "success": True
        }

        resp = client.patch(
            f"/api/integrations/{INTEGRATION_ID}/schedule/disable",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_disable_not_found(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_integration.return_value = None

        resp = client.patch(
            f"/api/integrations/{INTEGRATION_ID}/schedule/disable",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404


# =============================================================================
# Trigger (POST /api/integrations/<id>/schedule/trigger)
# =============================================================================

class TestTrigger:
    """POST /api/integrations/<id>/schedule/trigger"""

    def test_trigger_success(self, client, auth_headers, blueprint_app):
        blueprint_app.scheduler_service.trigger_integration.return_value = {
            "success": True, "message": "Triggered"
        }

        resp = client.post(
            f"/api/integrations/{INTEGRATION_ID}/schedule/trigger",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_trigger_already_running(self, client, auth_headers, blueprint_app):
        blueprint_app.scheduler_service.trigger_integration.return_value = {
            "success": False, "error": "Integration is already running"
        }

        resp = client.post(
            f"/api/integrations/{INTEGRATION_ID}/schedule/trigger",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 400


# =============================================================================
# Next runs (GET /api/integrations/<id>/schedule/next-runs)
# =============================================================================

class TestNextRuns:
    """GET /api/integrations/<id>/schedule/next-runs"""

    @patch("src.api.blueprints.integrations.schedules.calculate_next_runs")
    def test_next_runs_success(self, mock_calc, client, auth_headers, blueprint_app):
        mock_calc.return_value = [
            datetime(2025, 6, 1, 6, 0),
            datetime(2025, 6, 1, 12, 0),
        ]
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/schedule/next-runs",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["count"] == 2

    def test_next_runs_no_schedule(self, client, auth_headers, blueprint_app):
        integration = _make_integration(schedule_cron=None)
        blueprint_app.integration_service.get_integration.return_value = integration

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/schedule/next-runs",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 400

    def test_next_runs_not_found(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_integration.return_value = None

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/schedule/next-runs",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404

    @patch("src.api.blueprints.integrations.schedules.calculate_next_runs")
    def test_next_runs_respects_count_param(self, mock_calc, client, auth_headers, blueprint_app):
        mock_calc.return_value = [datetime(2025, 6, 1, 6, 0)] * 10
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration

        client.get(
            f"/api/integrations/{INTEGRATION_ID}/schedule/next-runs?count=10",
            headers=auth_headers("admin"),
        )

        # count is capped at 20
        _, kwargs = mock_calc.call_args
        assert kwargs["count"] == 10


# =============================================================================
# Scheduler status (GET /api/integrations/scheduler/status)
# =============================================================================

class TestSchedulerStatus:
    """GET /api/integrations/scheduler/status"""

    def test_scheduler_status_success(self, client, auth_headers, blueprint_app):
        blueprint_app.scheduler_service.get_scheduler_status.return_value = {
            "running": True,
            "jobs": 3,
        }

        resp = client.get(
            "/api/integrations/scheduler/status",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["scheduler"]["running"] is True
