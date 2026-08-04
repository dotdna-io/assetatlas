# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the reports blueprint (auth required).

Endpoints tested:
- GET    /api/reports              — List all system-wide reports
- GET    /api/reports/default      — Get default report
- GET    /api/reports/<id>         — Get specific report
- POST   /api/reports              — Create new report
- PUT    /api/reports/<id>         — Update existing report
- DELETE /api/reports/<id>         — Delete report (except default)
- POST   /api/reports/<id>/clone   — Clone a report
"""

import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _report_dict(**overrides):
    """Minimal report dict matching model_dump output."""
    base = {
        "id": "rpt-001",
        "title": "Asset Overview",
        "description": "Main report",
        "charts": [],
        "is_default": False,
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
        "created_by": "test-user-id",
        "updated_by": "test-user-id",
    }
    base.update(overrides)
    return base


def _mock_report(**overrides):
    """Return a MagicMock that behaves like a Pydantic model with model_dump."""
    data = _report_dict(**overrides)
    mock = MagicMock()
    mock.model_dump.return_value = data
    return mock


def _mock_report_list(items=None, total=0):
    """Return a MagicMock for paginated report list."""
    if items is None:
        items = []
    mock = MagicMock()
    mock.model_dump.return_value = {
        "reports": items,
        "total": total,
        "page": 1,
        "limit": 50,
        "has_more": False,
    }
    return mock


# =============================================================================
# Auth requirement
# =============================================================================

class TestReportsAuth:
    """All report endpoints require JWT authentication."""

    def test_list_requires_auth(self, client):
        resp = client.get("/api/reports")
        assert resp.status_code == 401

    def test_get_default_requires_auth(self, client):
        resp = client.get("/api/reports/default")
        assert resp.status_code == 401

    def test_get_by_id_requires_auth(self, client):
        resp = client.get("/api/reports/rpt-001")
        assert resp.status_code == 401

    def test_create_requires_auth(self, client):
        resp = client.post("/api/reports", json={"title": "t", "charts": []})
        assert resp.status_code == 401

    def test_update_requires_auth(self, client):
        resp = client.put("/api/reports/rpt-001", json={"title": "new"})
        assert resp.status_code == 401

    def test_delete_requires_auth(self, client):
        resp = client.delete("/api/reports/rpt-001")
        assert resp.status_code == 401


# =============================================================================
# GET /api/reports
# =============================================================================

class TestListReports:
    """GET /api/reports -- paginated report list."""

    def test_list_returns_200(self, client, auth_headers, blueprint_app):
        blueprint_app.reports_service.list_reports.return_value = _mock_report_list(
            items=[], total=0
        )
        resp = client.get("/api/reports", headers=auth_headers("user"))
        assert resp.status_code == 200

    def test_list_returns_wrapped_data(self, client, auth_headers, blueprint_app):
        items = [_report_dict(id="rpt-1"), _report_dict(id="rpt-2")]
        blueprint_app.reports_service.list_reports.return_value = _mock_report_list(
            items=items, total=2
        )
        resp = client.get("/api/reports", headers=auth_headers("user"))
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["total"] == 2
        assert len(body["data"]["reports"]) == 2

    def test_list_passes_query_params(self, client, auth_headers, blueprint_app):
        blueprint_app.reports_service.list_reports.return_value = _mock_report_list()
        client.get("/api/reports?page=2&limit=10&search=test", headers=auth_headers("user"))
        call_args = blueprint_app.reports_service.list_reports.call_args
        query_params = call_args[0][0]
        assert query_params.page == 2
        assert query_params.limit == 10
        assert query_params.search == "test"

    def test_list_includes_charts_array(self, client, auth_headers, blueprint_app):
        charts = [{"chartId": "c1", "name": "Bar", "type": "vertical-bar-chart", "size": "half"}]
        items = [_report_dict(id="rpt-1", charts=charts)]
        blueprint_app.reports_service.list_reports.return_value = _mock_report_list(
            items=items, total=1
        )
        resp = client.get("/api/reports", headers=auth_headers("user"))
        body = resp.get_json()
        assert body["data"]["reports"][0]["charts"] == charts


# =============================================================================
# GET /api/reports/default
# =============================================================================

class TestGetDefaultReport:
    """GET /api/reports/default -- the default report."""

    def test_returns_default_report(self, client, auth_headers, blueprint_app):
        blueprint_app.reports_service.get_default_report.return_value = _mock_report(
            is_default=True
        )
        resp = client.get("/api/reports/default", headers=auth_headers("user"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["is_default"] is True

    def test_missing_default_returns_404(self, client, auth_headers, blueprint_app):
        blueprint_app.reports_service.get_default_report.return_value = None
        resp = client.get("/api/reports/default", headers=auth_headers("user"))
        assert resp.status_code == 404
        body = resp.get_json()
        assert body["error"] == "not_found"


# =============================================================================
# GET /api/reports/<report_id>
# =============================================================================

class TestGetReport:
    """GET /api/reports/<id> -- single report details."""

    def test_returns_report(self, client, auth_headers, blueprint_app):
        blueprint_app.reports_service.get_report.return_value = _mock_report(id="rpt-1")
        resp = client.get("/api/reports/rpt-1", headers=auth_headers("user"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["id"] == "rpt-1"

    def test_not_found_returns_404(self, client, auth_headers, blueprint_app):
        blueprint_app.reports_service.get_report.return_value = None
        resp = client.get("/api/reports/nonexistent", headers=auth_headers("user"))
        assert resp.status_code == 404


# =============================================================================
# POST /api/reports
# =============================================================================

class TestCreateReport:
    """POST /api/reports -- create a new report."""

    def test_create_returns_200(self, client, auth_headers, blueprint_app):
        blueprint_app.reports_service.create_report.return_value = _mock_report(
            id="rpt-new", title="New Report"
        )
        payload = {"title": "New Report", "charts": []}
        resp = client.post("/api/reports", json=payload, headers=auth_headers("user"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["id"] == "rpt-new"

    def test_create_passes_validated_data_and_user(self, client, auth_headers, blueprint_app):
        blueprint_app.reports_service.create_report.return_value = _mock_report()
        payload = {"title": "Test", "description": "desc", "charts": []}
        client.post("/api/reports", json=payload, headers=auth_headers("user"))
        call_args = blueprint_app.reports_service.create_report.call_args
        validated_data = call_args[0][0]
        assert validated_data.title == "Test"
        assert validated_data.description == "desc"
        assert call_args[1]["created_by"] == "test-user-id"

    def test_create_missing_title_returns_400(self, client, auth_headers):
        payload = {"charts": []}
        resp = client.post("/api/reports", json=payload, headers=auth_headers("user"))
        assert resp.status_code == 400

    def test_create_missing_charts_returns_400(self, client, auth_headers):
        payload = {"title": "T"}
        resp = client.post("/api/reports", json=payload, headers=auth_headers("user"))
        assert resp.status_code == 400

    def test_create_no_json_body_returns_error(self, client, auth_headers):
        resp = client.post(
            "/api/reports",
            headers=auth_headers("user"),
            content_type="application/json",
            data="null",
        )
        # validate_request raises ValidationError when get_json() returns None
        assert resp.status_code == 400


# =============================================================================
# PUT /api/reports/<report_id>
# =============================================================================

class TestUpdateReport:
    """PUT /api/reports/<id> -- update a report."""

    def test_update_returns_200(self, client, auth_headers, blueprint_app):
        blueprint_app.reports_service.update_report.return_value = _mock_report(
            title="Updated"
        )
        payload = {"title": "Updated"}
        resp = client.put("/api/reports/rpt-1", json=payload, headers=auth_headers("user"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["title"] == "Updated"

    def test_update_not_found_returns_404(self, client, auth_headers, blueprint_app):
        blueprint_app.reports_service.update_report.return_value = None
        resp = client.put(
            "/api/reports/nonexistent", json={"title": "X"}, headers=auth_headers("user")
        )
        assert resp.status_code == 404

    def test_update_passes_user_id(self, client, auth_headers, blueprint_app):
        blueprint_app.reports_service.update_report.return_value = _mock_report()
        client.put("/api/reports/rpt-1", json={"title": "X"}, headers=auth_headers("user"))
        call_args = blueprint_app.reports_service.update_report.call_args
        assert call_args[1]["updated_by"] == "test-user-id"


# =============================================================================
# DELETE /api/reports/<report_id>
# =============================================================================

class TestDeleteReport:
    """DELETE /api/reports/<id> -- delete a report."""

    def test_delete_returns_success(self, client, auth_headers, blueprint_app):
        blueprint_app.reports_service.delete_report.return_value = True
        resp = client.delete("/api/reports/rpt-1", headers=auth_headers("user"))
        assert resp.status_code == 200
        body = resp.get_json()
        # The endpoint returns {'success': True, 'message': ...} which
        # response_wrapper treats as "already wrapped" (has 'success' key).
        assert body["success"] is True
        assert "deleted" in body["message"].lower()

    def test_delete_not_found_returns_404(self, client, auth_headers, blueprint_app):
        blueprint_app.reports_service.delete_report.return_value = False
        resp = client.delete("/api/reports/nonexistent", headers=auth_headers("user"))
        assert resp.status_code == 404

    def test_delete_default_report_returns_400(self, client, auth_headers, blueprint_app):
        blueprint_app.reports_service.delete_report.side_effect = ValueError(
            "Cannot delete default report"
        )
        resp = client.delete("/api/reports/rpt-default", headers=auth_headers("user"))
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["error"] == "validation_error"
        assert "default" in body["message"].lower()


# =============================================================================
# POST /api/reports/<report_id>/clone
# =============================================================================

class TestCloneReport:
    """POST /api/reports/<id>/clone -- clone a report."""

    def test_clone_requires_auth(self, client):
        resp = client.post("/api/reports/rpt-001/clone")
        assert resp.status_code == 401

    def test_clone_returns_200(self, client, auth_headers, blueprint_app):
        blueprint_app.reports_service.clone_report.return_value = _mock_report(
            id="rpt-cloned", title="Copy of Asset Overview"
        )
        resp = client.post("/api/reports/rpt-001/clone", headers=auth_headers("user"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["id"] == "rpt-cloned"
        assert body["data"]["title"] == "Copy of Asset Overview"

    def test_clone_passes_report_id_and_user(self, client, auth_headers, blueprint_app):
        blueprint_app.reports_service.clone_report.return_value = _mock_report()
        client.post("/api/reports/rpt-001/clone", headers=auth_headers("user"))
        call_args = blueprint_app.reports_service.clone_report.call_args
        assert call_args[0][0] == "rpt-001"
        assert call_args[1]["created_by"] == "test-user-id"

    def test_clone_not_found_returns_404(self, client, auth_headers, blueprint_app):
        blueprint_app.reports_service.clone_report.return_value = None
        resp = client.post("/api/reports/nonexistent/clone", headers=auth_headers("user"))
        assert resp.status_code == 404
