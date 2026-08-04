# SPDX-License-Identifier: AGPL-3.0-or-later
"""GET /api/monitoring/query and /query_range — admin-only Prometheus proxy."""
import pytest

from src.api.middleware.error_handler import APIError, ValidationError


SAMPLE_QUERY_DATA = {
    "resultType": "vector",
    "result": [{"metric": {"__name__": "up"}, "value": [123, "1"]}],
}


@pytest.fixture(autouse=True)
def _stub_service(client, blueprint_app):
    """Default the service mock to a successful response.

    Depends on `client` so it runs AFTER the client fixture resets all
    _SERVICE_ATTRS mocks between tests.
    """
    blueprint_app.monitoring_service.query.return_value = SAMPLE_QUERY_DATA
    blueprint_app.monitoring_service.query_range.return_value = {
        "resultType": "matrix",
        "result": [],
    }


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------

def test_query_requires_auth(client):
    resp = client.get("/api/monitoring/query?query=up")
    assert resp.status_code == 401


def test_query_range_requires_auth(client):
    resp = client.get("/api/monitoring/query_range?query=up&start=1&end=2&step=15s")
    assert resp.status_code == 401


def test_user_role_gets_403(client, auth_headers, _prime_regular_user):
    resp = client.get(
        "/api/monitoring/query?query=up",
        headers=auth_headers("user"),
    )
    assert resp.status_code == 403


def test_admin_can_call_query(client, auth_headers):
    resp = client.get(
        "/api/monitoring/query?query=up",
        headers=auth_headers("admin"),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    data = body.get("data", body)
    assert data == SAMPLE_QUERY_DATA


def test_super_admin_can_call_query(client, auth_headers):
    resp = client.get(
        "/api/monitoring/query?query=up",
        headers=auth_headers("super_admin"),
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Required params
# ---------------------------------------------------------------------------

def test_query_missing_query_param_is_400(client, auth_headers):
    resp = client.get(
        "/api/monitoring/query",
        headers=auth_headers("admin"),
    )
    assert resp.status_code == 400


@pytest.mark.parametrize("missing", ["query", "start", "end", "step"])
def test_query_range_missing_param_is_400(client, auth_headers, missing):
    base = {"query": "up", "start": "1", "end": "2", "step": "15s"}
    base.pop(missing)
    qs = "&".join(f"{k}={v}" for k, v in base.items())
    url = f"/api/monitoring/query_range?{qs}" if qs else "/api/monitoring/query_range"
    resp = client.get(url, headers=auth_headers("admin"))
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Service errors propagate to correct status codes
# ---------------------------------------------------------------------------

def test_query_prometheus_validation_error_is_400(client, auth_headers, blueprint_app):
    blueprint_app.monitoring_service.query.side_effect = ValidationError(
        "Prometheus error: parse error"
    )
    resp = client.get(
        "/api/monitoring/query?query=bogus(",
        headers=auth_headers("admin"),
    )
    assert resp.status_code == 400


def test_query_prometheus_unreachable_is_503(client, auth_headers, blueprint_app):
    class _ServiceUnavailableError(APIError):
        status_code = 503
        error_type = "service_unavailable"

    blueprint_app.monitoring_service.query.side_effect = _ServiceUnavailableError(
        "Prometheus is unreachable"
    )
    resp = client.get(
        "/api/monitoring/query?query=up",
        headers=auth_headers("admin"),
    )
    assert resp.status_code == 503


def test_admin_can_call_query_range(client, auth_headers):
    resp = client.get(
        "/api/monitoring/query_range?query=up&start=1&end=2&step=15s",
        headers=auth_headers("admin"),
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Runtime dashboards toggle (`monitoring.dashboards` setting)
# ---------------------------------------------------------------------------

def test_query_404_when_dashboards_disabled(client, auth_headers, blueprint_app, monkeypatch):
    # The proxy is always registered now; when the runtime setting resolves to
    # 'disabled' the routes 404 (the pre-toggle "feature not enabled" contract).
    # monkeypatch (not direct assignment) so the session-scoped blueprint_app's
    # settings_manager.get is restored after the test — otherwise the stub leaks
    # into later tests that read it without the client fixture's reset.
    monkeypatch.setattr(blueprint_app.settings_manager, "get", lambda *a, **k: "disabled")
    resp = client.get(
        "/api/monitoring/query?query=up",
        headers=auth_headers("admin"),
    )
    assert resp.status_code == 404


def test_query_range_404_when_dashboards_disabled(client, auth_headers, blueprint_app, monkeypatch):
    monkeypatch.setattr(blueprint_app.settings_manager, "get", lambda *a, **k: "disabled")
    resp = client.get(
        "/api/monitoring/query_range?query=up&start=1&end=2&step=15s",
        headers=auth_headers("admin"),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /status — reachability probe (NOT gated on the toggle)
# ---------------------------------------------------------------------------

def test_status_requires_auth(client):
    assert client.get("/api/monitoring/status").status_code == 401


def test_status_reports_enabled_and_reachability(client, auth_headers, blueprint_app):
    blueprint_app.monitoring_service.ping.return_value = (True, None)
    resp = client.get("/api/monitoring/status", headers=auth_headers("admin"))
    assert resp.status_code == 200
    data = resp.get_json().get("data", {})
    assert data["enabled"] is True
    assert data["prometheus_reachable"] is True


def test_status_answers_even_when_dashboards_disabled(client, auth_headers, blueprint_app, monkeypatch):
    # /status must work while dashboards are off so the Settings toggle can probe
    # reachability before enabling.
    monkeypatch.setattr(blueprint_app.settings_manager, "get", lambda *a, **k: "disabled")
    blueprint_app.monitoring_service.ping.return_value = (False, "Prometheus is unreachable")
    resp = client.get("/api/monitoring/status", headers=auth_headers("admin"))
    assert resp.status_code == 200
    data = resp.get_json().get("data", {})
    assert data["enabled"] is False
    assert data["prometheus_reachable"] is False
    assert data["detail"] == "Prometheus is unreachable"


# ---------------------------------------------------------------------------
# GET /storage — TSDB usage + retention (NOT gated on the toggle, admin-only)
# ---------------------------------------------------------------------------

_STORAGE = {
    "reachable": True,
    "detail": None,
    "applied_retention": "15d",
    "applied_retention_seconds": 1296000,
    "disk_used_bytes": 1200000000,
    "head_series": 21456,
    "samples_per_second": 3100.0,
    "history_seconds": 1278000,
}


def test_storage_requires_auth(client):
    assert client.get("/api/monitoring/storage").status_code == 401


def test_storage_user_role_gets_403(client, auth_headers, _prime_regular_user):
    resp = client.get("/api/monitoring/storage", headers=auth_headers("user"))
    assert resp.status_code == 403


def test_storage_pending_true_when_desired_differs(client, auth_headers, blueprint_app, monkeypatch):
    blueprint_app.monitoring_service.storage_stats.return_value = dict(_STORAGE)
    monkeypatch.setattr(blueprint_app.settings_manager, "get", lambda *a, **k: "30d")
    resp = client.get("/api/monitoring/storage", headers=auth_headers("admin"))
    assert resp.status_code == 200
    data = resp.get_json().get("data", {})
    assert data["desired_retention"] == "30d"
    assert data["pending"] is True
    assert data["disk_used_bytes"] == 1200000000


def test_storage_pending_false_when_desired_empty(client, auth_headers, blueprint_app, monkeypatch):
    blueprint_app.monitoring_service.storage_stats.return_value = dict(_STORAGE)
    monkeypatch.setattr(blueprint_app.settings_manager, "get", lambda *a, **k: "")
    resp = client.get("/api/monitoring/storage", headers=auth_headers("admin"))
    data = resp.get_json().get("data", {})
    assert data["pending"] is False


def test_storage_pending_false_when_desired_equals_applied_normalized(client, auth_headers, blueprint_app, monkeypatch):
    # Prometheus reports applied as compound "4w2d" (== 30d); desired "30d".
    stats = dict(_STORAGE, applied_retention="4w2d", applied_retention_seconds=2592000)
    blueprint_app.monitoring_service.storage_stats.return_value = stats
    monkeypatch.setattr(blueprint_app.settings_manager, "get", lambda *a, **k: "30d")
    resp = client.get("/api/monitoring/storage", headers=auth_headers("admin"))
    data = resp.get_json().get("data", {})
    assert data["pending"] is False


def test_storage_pending_false_when_unreachable(client, auth_headers, blueprint_app, monkeypatch):
    stats = dict(_STORAGE, reachable=False, applied_retention=None, applied_retention_seconds=None)
    blueprint_app.monitoring_service.storage_stats.return_value = stats
    monkeypatch.setattr(blueprint_app.settings_manager, "get", lambda *a, **k: "30d")
    resp = client.get("/api/monitoring/storage", headers=auth_headers("admin"))
    data = resp.get_json().get("data", {})
    assert data["reachable"] is False
    assert data["pending"] is False
