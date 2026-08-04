# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the alerts blueprint (auth required, no admin).

Endpoints tested:
- GET   /api/alerts           — List alerts with filters, summary, and timeline
- PATCH /api/alerts/<id>      — Update alert status (acknowledge/resolve)
"""

import pytest
from unittest.mock import MagicMock


# =============================================================================
# Auth requirement
# =============================================================================

class TestAlertsAuth:
    """All alert endpoints require JWT authentication."""

    def test_list_requires_auth(self, client):
        resp = client.get("/api/alerts")
        assert resp.status_code == 401

    def test_patch_requires_auth(self, client):
        resp = client.patch("/api/alerts/alert-1", json={"status": "acknowledged"})
        assert resp.status_code == 401


# =============================================================================
# GET /api/alerts
# =============================================================================

class TestListAlerts:
    """GET /api/alerts -- list alerts with filters."""

    def test_list_returns_200(self, client, auth_headers, blueprint_app):
        blueprint_app.alerts_service.get_alerts_response.return_value = {
            "alerts": [],
            "summary": {"total": 0},
            "timeline": [],
        }
        resp = client.get("/api/alerts", headers=auth_headers("user"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True

    def test_list_default_params(self, client, auth_headers, blueprint_app):
        """Without query params, defaults are applied."""
        blueprint_app.alerts_service.get_alerts_response.return_value = {"alerts": []}
        client.get("/api/alerts", headers=auth_headers("user"))
        call_kwargs = blueprint_app.alerts_service.get_alerts_response.call_args[1]
        assert call_kwargs["time_range"] == "7d"
        assert call_kwargs["severity"] is None
        assert call_kwargs["alert_type"] is None
        assert call_kwargs["status"] is None
        assert call_kwargs["search"] is None
        assert call_kwargs["asset_id"] is None
        assert call_kwargs["page"] == 1
        assert call_kwargs["per_page"] == 20

    def test_list_with_filters(self, client, auth_headers, blueprint_app):
        """Query params are forwarded to the service."""
        blueprint_app.alerts_service.get_alerts_response.return_value = {"alerts": []}
        client.get(
            "/api/alerts?time_range=24h&severity=critical&type=security"
            "&status=active&search=disk&page=2&per_page=10",
            headers=auth_headers("user"),
        )
        call_kwargs = blueprint_app.alerts_service.get_alerts_response.call_args[1]
        assert call_kwargs["time_range"] == "24h"
        assert call_kwargs["severity"] == "critical"
        assert call_kwargs["alert_type"] == "security"
        assert call_kwargs["status"] == "active"
        assert call_kwargs["search"] == "disk"
        assert call_kwargs["page"] == 2
        assert call_kwargs["per_page"] == 10

    def test_list_empty_string_params_become_none(self, client, auth_headers, blueprint_app):
        """Empty string query params are normalised to None."""
        blueprint_app.alerts_service.get_alerts_response.return_value = {"alerts": []}
        client.get(
            "/api/alerts?severity=&status=&search=&asset_id=",
            headers=auth_headers("user"),
        )
        call_kwargs = blueprint_app.alerts_service.get_alerts_response.call_args[1]
        assert call_kwargs["severity"] is None
        assert call_kwargs["status"] is None
        assert call_kwargs["search"] is None
        # An empty asset_id is "no filter", not a malformed id — it must NOT 400.
        assert call_kwargs["asset_id"] is None


    def test_list_forwards_canonical_asset_id(self, client, auth_headers, blueprint_app):
        """A canonical asset_id reaches the service as the exact filter (#903)."""
        blueprint_app.alerts_service.get_alerts_response.return_value = {"alerts": []}
        aid = "948e3f69-818b-4649-8f77-362048686475"
        client.get(f"/api/alerts?asset_id={aid}", headers=auth_headers("user"))
        call_kwargs = blueprint_app.alerts_service.get_alerts_response.call_args[1]
        assert call_kwargs["asset_id"] == aid

    def test_list_canonicalises_asset_id_spelling(self, client, auth_headers, blueprint_app):
        """A non-canonical (uppercase) spelling is canonicalised before it reaches
        the service, so the repo's ::uuid cast and count_active_alerts_for_asset
        agree on one spelling of the id."""
        blueprint_app.alerts_service.get_alerts_response.return_value = {"alerts": []}
        client.get(
            "/api/alerts?asset_id=948E3F69-818B-4649-8F77-362048686475",
            headers=auth_headers("user"),
        )
        call_kwargs = blueprint_app.alerts_service.get_alerts_response.call_args[1]
        assert call_kwargs["asset_id"] == "948e3f69-818b-4649-8f77-362048686475"

    def test_list_malformed_asset_id_returns_400_before_service(
        self, client, auth_headers, blueprint_app
    ):
        """A malformed asset_id is a 400 (ensure_uuid), never the 500 the repo's
        raw ::uuid cast would throw (#973) — and the service is never reached."""
        resp = client.get(
            "/api/alerts?asset_id=not-a-uuid", headers=auth_headers("user")
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "validation_error"
        blueprint_app.alerts_service.get_alerts_response.assert_not_called()


# =============================================================================
# PATCH /api/alerts/<alert_id>
# =============================================================================

class TestPatchAlert:
    """PATCH /api/alerts/<id> -- update alert status."""

    def test_acknowledge_returns_200(self, client, auth_headers, blueprint_app):
        blueprint_app.alerts_service.update_alert_status.return_value = {
            "id": "alert-1",
            "status": "acknowledged",
        }
        resp = client.patch(
            "/api/alerts/alert-1",
            json={"status": "acknowledged"},
            headers=auth_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["status"] == "acknowledged"

    def test_resolve_returns_200(self, client, auth_headers, blueprint_app):
        blueprint_app.alerts_service.update_alert_status.return_value = {
            "id": "alert-1",
            "status": "resolved",
        }
        resp = client.patch(
            "/api/alerts/alert-1",
            json={"status": "resolved"},
            headers=auth_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["status"] == "resolved"

    def test_invalid_status_returns_400(self, client, auth_headers):
        resp = client.patch(
            "/api/alerts/alert-1",
            json={"status": "deleted"},
            headers=auth_headers("user"),
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["error"] == "validation_error"

    def test_missing_body_returns_400(self, client, auth_headers):
        resp = client.patch(
            "/api/alerts/alert-1",
            headers=auth_headers("user"),
            content_type="application/json",
            data="null",
        )
        assert resp.status_code == 400

    def test_missing_status_field_returns_400(self, client, auth_headers):
        resp = client.patch(
            "/api/alerts/alert-1",
            json={"note": "something"},
            headers=auth_headers("user"),
        )
        assert resp.status_code == 400

    def test_alert_not_found_returns_400(self, client, auth_headers, blueprint_app):
        """When service returns None the blueprint raises ValidationError (400)."""
        blueprint_app.alerts_service.update_alert_status.return_value = None
        resp = client.patch(
            "/api/alerts/nonexistent",
            json={"status": "acknowledged"},
            headers=auth_headers("user"),
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert "not found" in body["message"].lower()

    def test_patch_calls_service_with_correct_args(self, client, auth_headers, blueprint_app):
        blueprint_app.alerts_service.update_alert_status.return_value = {"id": "a1"}
        client.patch(
            "/api/alerts/a1",
            json={"status": "resolved"},
            headers=auth_headers("user"),
        )
        blueprint_app.alerts_service.update_alert_status.assert_called_once_with(
            "a1", "resolved"
        )


# =============================================================================
# GET /api/alerts/<alert_id>/history
# =============================================================================

class TestAlertHistory:
    """GET /api/alerts/<id>/history -- per-run firing history (#905)."""

    def test_history_requires_auth(self, client):
        assert client.get("/api/alerts/a1/history").status_code == 401

    def test_history_returns_200(self, client, auth_headers, blueprint_app):
        blueprint_app.alerts_service.get_alert_history.return_value = {
            "alert_id": "a1", "history": []}
        resp = client.get("/api/alerts/a1/history", headers=auth_headers("user"))
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_history_defaults_days_to_90(self, client, auth_headers, blueprint_app):
        blueprint_app.alerts_service.get_alert_history.return_value = {
            "alert_id": "a1", "history": []}
        client.get("/api/alerts/a1/history", headers=auth_headers("user"))
        blueprint_app.alerts_service.get_alert_history.assert_called_once_with("a1", days=90)

    def test_history_forwards_days(self, client, auth_headers, blueprint_app):
        blueprint_app.alerts_service.get_alert_history.return_value = {
            "alert_id": "a1", "history": []}
        client.get("/api/alerts/a1/history?days=30", headers=auth_headers("user"))
        blueprint_app.alerts_service.get_alert_history.assert_called_once_with("a1", days=30)
