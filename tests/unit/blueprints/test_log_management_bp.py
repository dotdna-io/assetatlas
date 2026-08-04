# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the log management blueprint (admin auth required).

Endpoints tested:
- GET  /api/logs/retention/settings  — get retention settings
- PUT  /api/logs/retention/settings  — update retention settings
- POST /api/logs/retention/cleanup   — trigger manual cleanup
- GET  /api/logs/retention/status    — get cleanup service status
"""

import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Auth gate — all endpoints require admin
# ---------------------------------------------------------------------------

class TestLogManagementAuth:
    """All log management endpoints require admin JWT."""

    @pytest.mark.parametrize("method,path", [
        ("GET",  "/api/logs/retention/settings"),
        ("PUT",  "/api/logs/retention/settings"),
        ("POST", "/api/logs/retention/cleanup"),
        ("GET",  "/api/logs/retention/status"),
    ])
    def test_no_auth_returns_401(self, client, method, path):
        resp = getattr(client, method.lower())(path)
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path", [
        ("GET",  "/api/logs/retention/settings"),
        ("PUT",  "/api/logs/retention/settings"),
        ("POST", "/api/logs/retention/cleanup"),
        ("GET",  "/api/logs/retention/status"),
    ])
    def test_user_role_returns_403(self, client, authed_headers, method, path):
        resp = getattr(client, method.lower())(path, headers=authed_headers("user"))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/logs/retention/settings
# ---------------------------------------------------------------------------

class TestGetRetentionSettings:
    """GET /api/logs/retention/settings — returns current retention config."""

    def test_returns_settings(self, client, authed_headers, blueprint_app):
        expected = {
            "retention_days": {"access": 30, "app": 30, "error": 90},
            "compression_after_days": 7,
            "cleanup_schedule": "0 2 * * *",
            "enabled": True,
        }
        blueprint_app.log_cleanup_service.get_settings.return_value = expected

        resp = client.get(
            "/api/logs/retention/settings", headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["settings"] == expected

    def test_service_called(self, client, authed_headers, blueprint_app):
        blueprint_app.log_cleanup_service.get_settings.return_value = {}

        client.get(
            "/api/logs/retention/settings", headers=authed_headers("admin"),
        )
        blueprint_app.log_cleanup_service.get_settings.assert_called_once()


# ---------------------------------------------------------------------------
# PUT /api/logs/retention/settings
# ---------------------------------------------------------------------------

class TestUpdateRetentionSettings:
    """PUT /api/logs/retention/settings — update retention config."""

    def test_update_full_settings(self, client, authed_headers, blueprint_app):
        blueprint_app.log_cleanup_service.update_settings.return_value = True
        blueprint_app.log_cleanup_service.get_settings.return_value = {
            "retention_days": {"access": 60},
            "enabled": True,
        }

        payload = {
            "retention_days": {"access": 60},
            "compression_after_days": 14,
            "cleanup_schedule": "0 3 * * *",
            "enabled": True,
        }
        resp = client.put(
            "/api/logs/retention/settings",
            json=payload,
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["message"] == "Log retention settings updated successfully"
        assert "settings" in body["data"]

    def test_empty_body_returns_error(self, client, authed_headers):
        resp = client.put(
            "/api/logs/retention/settings",
            data="null",
            content_type="application/json",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_invalid_retention_days_type(self, client, authed_headers):
        resp = client.put(
            "/api/logs/retention/settings",
            json={"retention_days": "not-a-dict"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_unknown_log_type_rejected(self, client, authed_headers):
        resp = client.put(
            "/api/logs/retention/settings",
            json={"retention_days": {"invalid_type": 30}},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_retention_days_out_of_range(self, client, authed_headers):
        resp = client.put(
            "/api/logs/retention/settings",
            json={"retention_days": {"access": 0}},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_retention_days_too_high(self, client, authed_headers):
        resp = client.put(
            "/api/logs/retention/settings",
            json={"retention_days": {"access": 9999}},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_compression_days_out_of_range(self, client, authed_headers):
        resp = client.put(
            "/api/logs/retention/settings",
            json={"compression_after_days": 0},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_compression_days_too_high(self, client, authed_headers):
        resp = client.put(
            "/api/logs/retention/settings",
            json={"compression_after_days": 999},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_invalid_cron_expression(self, client, authed_headers):
        resp = client.put(
            "/api/logs/retention/settings",
            json={"cleanup_schedule": "not a cron"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_enabled_must_be_bool(self, client, authed_headers):
        resp = client.put(
            "/api/logs/retention/settings",
            json={"enabled": "yes"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_update_failure_returns_error(self, client, authed_headers, blueprint_app):
        blueprint_app.log_cleanup_service.update_settings.return_value = False

        resp = client.put(
            "/api/logs/retention/settings",
            json={"enabled": True},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_valid_single_field_update(self, client, authed_headers, blueprint_app):
        blueprint_app.log_cleanup_service.update_settings.return_value = True
        blueprint_app.log_cleanup_service.get_settings.return_value = {"enabled": False}

        resp = client.put(
            "/api/logs/retention/settings",
            json={"enabled": False},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/logs/retention/cleanup
# ---------------------------------------------------------------------------

class TestTriggerCleanup:
    """POST /api/logs/retention/cleanup — manual log cleanup."""

    def test_trigger_cleanup(self, client, authed_headers, blueprint_app):
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {
            "files_deleted": 5,
            "bytes_freed": 1024000,
        }
        blueprint_app.log_cleanup_service.run_cleanup.return_value = mock_result

        resp = client.post(
            "/api/logs/retention/cleanup",
            json={},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["message"] == "Log cleanup completed"
        assert body["data"]["result"]["files_deleted"] == 5

    def test_dry_run(self, client, authed_headers, blueprint_app):
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"files_to_delete": 3}
        blueprint_app.log_cleanup_service.run_cleanup.return_value = mock_result

        resp = client.post(
            "/api/logs/retention/cleanup",
            json={"dry_run": True},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["message"] == "Dry run completed"
        blueprint_app.log_cleanup_service.run_cleanup.assert_called_once_with(
            dry_run=True,
        )

    def test_invalid_dry_run_type(self, client, authed_headers):
        resp = client.post(
            "/api/logs/retention/cleanup",
            json={"dry_run": "yes"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_no_body_defaults_to_real_run(self, client, authed_headers, blueprint_app):
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {}
        blueprint_app.log_cleanup_service.run_cleanup.return_value = mock_result

        resp = client.post(
            "/api/logs/retention/cleanup",
            json={},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        blueprint_app.log_cleanup_service.run_cleanup.assert_called_once_with(
            dry_run=False,
        )


# ---------------------------------------------------------------------------
# GET /api/logs/retention/status
# ---------------------------------------------------------------------------

class TestCleanupStatus:
    """GET /api/logs/retention/status — cleanup service status."""

    def test_returns_status(self, client, authed_headers, blueprint_app):
        blueprint_app.log_cleanup_service.get_status.return_value = {
            "disk_usage_bytes": 5000000,
            "oldest_file": "2025-01-01",
            "last_cleanup": "2025-06-01",
        }
        # scheduler_service.get_job returns None → next_scheduled_run = None
        blueprint_app.scheduler_service.get_job.return_value = None

        resp = client.get(
            "/api/logs/retention/status", headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["disk_usage_bytes"] == 5000000
        assert body["data"]["next_scheduled_run"] is None

    def test_scheduler_info_included(self, client, authed_headers, blueprint_app):
        """When scheduler_service is present, next_scheduled_run is populated."""
        blueprint_app.log_cleanup_service.get_status.return_value = {}

        mock_job = MagicMock()
        mock_job.next_run_time = None
        blueprint_app.scheduler_service.get_job.return_value = mock_job

        resp = client.get(
            "/api/logs/retention/status", headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["next_scheduled_run"] is None
