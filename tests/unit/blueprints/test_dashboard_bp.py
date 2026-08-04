# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the dashboard blueprint (auth required, no admin gates).

Endpoints tested:
- GET /api/dashboard/summary       — comprehensive dashboard summary
- GET /api/dashboard/inventory     — inventory panel data
- GET /api/dashboard/flowers       — flower-chart visualization data
- GET /api/dashboard/wheels-data   — v2 wheel visualization data
- GET /api/dashboard/data-sources  — data source health
- GET /api/dashboard/reliability   — reliability statistics
- GET /api/dashboard/alerts        — active alerts summary
"""

import pytest
from unittest.mock import MagicMock

from src.api.utils.caching import cache


@pytest.fixture(autouse=True)
def _clear_dashboard_cache(blueprint_app, _prime_user_cache):
    """Clear dashboard cache entries between tests.

    Dashboard endpoints use ``@cached_dashboard(timeout=60)`` which caches
    results in the SimpleCache backend.  Without clearing, subsequent tests
    get stale cached responses from prior tests.

    SimpleCache has no pattern-delete, so we clear the entire cache and then
    re-prime the JWT user cache so that auth lookups still work.  The
    ``_prime_user_cache`` dependency ensures the user cache is set first;
    we clear+re-prime before and after each test.
    """
    def _clear_and_reprime():
        cache.clear()
        cache.set(
            "jwt_user:test-user-id",
            {
                "id": "test-user-id",
                "username": "testuser",
                "role": "admin",
                "is_active": True,
                "email": "testuser@test.local",
                "full_name": "Test User",
            },
            timeout=300,
        )

    with blueprint_app.app_context():
        _clear_and_reprime()
    yield
    with blueprint_app.app_context():
        _clear_and_reprime()


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------

class TestDashboardAuthRequired:
    """All dashboard endpoints require a valid JWT."""

    @pytest.mark.parametrize("path", [
        "/api/dashboard/summary",
        "/api/dashboard/inventory",
        "/api/dashboard/flowers",
        "/api/dashboard/wheels-data",
        "/api/dashboard/data-sources",
        "/api/dashboard/reliability",
        "/api/dashboard/alerts",
        "/api/dashboard/posture",
    ])
    def test_no_auth_returns_401(self, client, path):
        resp = client.get(path)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/dashboard/summary
# ---------------------------------------------------------------------------

class TestDashboardSummary:
    """GET /api/dashboard/summary"""

    def test_summary_success(self, client, authed_headers, blueprint_app):
        blueprint_app.dashboard_service.get_summary.return_value = {
            "totalAssets": 1500,
            "categoryBreakdown": {"Server": 500, "Desktop": 1000},
            "dataQuality": {"avgConfidence": 0.85},
        }

        resp = client.get(
            "/api/dashboard/summary", headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["totalAssets"] == 1500

    def test_summary_service_error(self, client, authed_headers, blueprint_app):
        blueprint_app.dashboard_service.get_summary.side_effect = RuntimeError("DB down")

        resp = client.get(
            "/api/dashboard/summary", headers=authed_headers("user"),
        )
        assert resp.status_code == 500

    def test_summary_includes_source_coverage(self, client, authed_headers, blueprint_app):
        blueprint_app.dashboard_service.get_summary.return_value = {
            "totalAssets": 100,
            "sourceCoverage": [
                {"category": "Server", "source_count": 5},
                {"category": "Workstation", "source_count": 3},
            ],
            "completeness": 72.3,
            "lastUpdated": "2026-04-10T12:00:00+00:00",
        }

        resp = client.get(
            "/api/dashboard/summary", headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        data = body["data"]
        assert len(data["sourceCoverage"]) == 2
        assert data["sourceCoverage"][0]["source_count"] == 5
        assert data["completeness"] == 72.3
        assert data["lastUpdated"] == "2026-04-10T12:00:00+00:00"


# ---------------------------------------------------------------------------
# GET /api/dashboard/inventory
# ---------------------------------------------------------------------------

class TestDashboardInventory:
    """GET /api/dashboard/inventory"""

    def test_inventory_success(self, client, authed_headers, blueprint_app):
        blueprint_app.dashboard_service.get_inventory_summary.return_value = {
            "totalAssets": 1500,
            "changeToday": {"added": 10, "removed": 2},
            "categoryGauges": [],
        }

        resp = client.get(
            "/api/dashboard/inventory", headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["totalAssets"] == 1500


# ---------------------------------------------------------------------------
# GET /api/dashboard/flowers
# ---------------------------------------------------------------------------

class TestDashboardFlowers:
    """GET /api/dashboard/flowers — with optional filter params."""

    def test_flowers_no_filters(self, client, authed_headers, blueprint_app):
        blueprint_app.dashboard_service.get_flower_data.return_value = {
            "totalAssets": 1000,
            "categories": [],
            "operatingSystems": [],
        }

        resp = client.get(
            "/api/dashboard/flowers", headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        blueprint_app.dashboard_service.get_flower_data.assert_called_once_with(None)

    def test_flowers_with_category_filter(self, client, authed_headers, blueprint_app):
        blueprint_app.dashboard_service.get_flower_data.return_value = {
            "totalAssets": 500, "categories": [],
        }

        resp = client.get(
            "/api/dashboard/flowers?category=Server",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        blueprint_app.dashboard_service.get_flower_data.assert_called_once_with(
            {"category": "Server"},
        )

    def test_flowers_with_multiple_filters(self, client, authed_headers, blueprint_app):
        blueprint_app.dashboard_service.get_flower_data.return_value = {
            "totalAssets": 100,
        }

        resp = client.get(
            "/api/dashboard/flowers?category=Server&os=Windows&subcategory=Virtual",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        call_args = blueprint_app.dashboard_service.get_flower_data.call_args[0][0]
        assert call_args["category"] == "Server"
        assert call_args["os"] == "Windows"
        assert call_args["subcategory"] == "Virtual"

    def test_flowers_with_integration_filter(self, client, authed_headers, blueprint_app):
        blueprint_app.dashboard_service.get_flower_data.return_value = {"totalAssets": 50}

        resp = client.get(
            "/api/dashboard/flowers?integration_id=int-uuid",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        call_args = blueprint_app.dashboard_service.get_flower_data.call_args[0][0]
        assert call_args["integration_id"] == "int-uuid"


# ---------------------------------------------------------------------------
# GET /api/dashboard/wheels-data
# ---------------------------------------------------------------------------

class TestDashboardWheelsData:
    """GET /api/dashboard/wheels-data — v2 visualization."""

    def test_wheels_data_success(self, client, authed_headers, blueprint_app):
        blueprint_app.dashboard_service.get_flower_data_v2.return_value = {
            "totalAssets": 2000,
            "categories": [],
            "subcategories": [],
            "operatingSystems": [],
            "dataSources": [],
        }

        resp = client.get(
            "/api/dashboard/wheels-data", headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["totalAssets"] == 2000


# ---------------------------------------------------------------------------
# GET /api/dashboard/data-sources
# ---------------------------------------------------------------------------

class TestDashboardDataSources:
    """GET /api/dashboard/data-sources"""

    def test_data_sources_default_view(self, client, authed_headers, blueprint_app):
        blueprint_app.dashboard_service.get_data_sources.return_value = {
            "count": 3,
            "sources": [{"name": "Jamf", "status": "healthy"}],
        }

        resp = client.get(
            "/api/dashboard/data-sources", headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        blueprint_app.dashboard_service.get_data_sources.assert_called_once_with(
            view="individual",
        )

    def test_data_sources_grouped_view(self, client, authed_headers, blueprint_app):
        blueprint_app.dashboard_service.get_data_sources.return_value = {
            "count": 2, "sources": [],
        }

        resp = client.get(
            "/api/dashboard/data-sources?view=grouped",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        blueprint_app.dashboard_service.get_data_sources.assert_called_once_with(
            view="grouped",
        )

    def test_data_sources_invalid_view_falls_back(self, client, authed_headers, blueprint_app):
        """Invalid view param falls back to 'individual'."""
        blueprint_app.dashboard_service.get_data_sources.return_value = {
            "count": 0, "sources": [],
        }

        resp = client.get(
            "/api/dashboard/data-sources?view=invalid",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        blueprint_app.dashboard_service.get_data_sources.assert_called_once_with(
            view="individual",
        )


# ---------------------------------------------------------------------------
# GET /api/dashboard/reliability
# ---------------------------------------------------------------------------

class TestDashboardReliability:
    """GET /api/dashboard/reliability"""

    def test_reliability_success(self, client, authed_headers, blueprint_app):
        blueprint_app.dashboard_service.get_reliability.return_value = {
            "mostReliableSource": {"name": "Jamf", "confidence": 0.95},
            "leastReliableSource": {"name": "Manual", "confidence": 0.60},
            "mostReliableCategory": {"name": "Server", "confidence": 0.92},
            "leastReliableCategory": {"name": "Unknown", "confidence": 0.55},
        }

        resp = client.get(
            "/api/dashboard/reliability", headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["mostReliableSource"]["name"] == "Jamf"


# ---------------------------------------------------------------------------
# GET /api/dashboard/alerts
# ---------------------------------------------------------------------------

class TestDashboardAlerts:
    """GET /api/dashboard/alerts — active alerts summary."""

    def test_alerts_success(self, client, authed_headers, blueprint_app):
        blueprint_app.alerts_service.get_alerts_response.return_value = {
            "alerts": [
                {"id": "a1", "severity": "critical", "message": "High CPU"},
            ],
            "pagination": {"total_items": 1, "page": 1},
            "summary": {
                "by_severity": {
                    "critical": 1,
                    "high": 0,
                    "medium": 0,
                    "low": 0,
                },
            },
        }

        resp = client.get(
            "/api/dashboard/alerts", headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["count"] == 1
        assert body["data"]["summary"]["critical"] == 1
        assert len(body["data"]["alerts"]) == 1

    def test_alerts_empty(self, client, authed_headers, blueprint_app):
        blueprint_app.alerts_service.get_alerts_response.return_value = {
            "alerts": [],
            "pagination": {"total_items": 0, "page": 1},
            "summary": {"by_severity": {}},
        }

        resp = client.get(
            "/api/dashboard/alerts", headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["count"] == 0
        assert body["data"]["summary"]["critical"] == 0
        assert body["data"]["alerts"] == []

    def test_alerts_calls_service_correctly(self, client, authed_headers, blueprint_app):
        blueprint_app.alerts_service.get_alerts_response.return_value = {
            "alerts": [],
            "pagination": {"total_items": 0, "page": 1},
            "summary": {"by_severity": {}},
        }

        client.get("/api/dashboard/alerts", headers=authed_headers("user"))

        blueprint_app.alerts_service.get_alerts_response.assert_called_once_with(
            status="active", per_page=5, page=1,
        )

    def test_alerts_transform_categories(self, client, authed_headers, blueprint_app):
        """assets_by_category dict is reshaped to categories array."""
        blueprint_app.alerts_service.get_alerts_response.return_value = {
            "alerts": [
                {
                    "id": "a1",
                    "severity": "critical",
                    "title": "Test alert",
                    "assets_by_category": {"Server": 5, "Workstation": 2},
                    "created_at": "2026-04-10T10:00:00Z",
                },
            ],
            "pagination": {"total_items": 1, "page": 1},
            "summary": {"by_severity": {"critical": 1, "high": 0, "medium": 0, "low": 0}},
        }

        resp = client.get(
            "/api/dashboard/alerts", headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        alert = resp.get_json()["data"]["alerts"][0]
        assert "assets_by_category" not in alert
        assert "categories" in alert
        cats = alert["categories"]
        assert len(cats) == 2
        cat_dict = {c["category"]: c["count"] for c in cats}
        assert cat_dict["Server"] == 5
        assert cat_dict["Workstation"] == 2

    def test_alerts_missing_category_gets_empty_list(self, client, authed_headers, blueprint_app):
        """Alerts without assets_by_category get categories: []."""
        blueprint_app.alerts_service.get_alerts_response.return_value = {
            "alerts": [
                {"id": "a2", "severity": "low", "title": "No cats"},
            ],
            "pagination": {"total_items": 1, "page": 1},
            "summary": {"by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 1}},
        }

        resp = client.get(
            "/api/dashboard/alerts", headers=authed_headers("user"),
        )
        alert = resp.get_json()["data"]["alerts"][0]
        assert alert["categories"] == []


# ---------------------------------------------------------------------------
# GET /api/dashboard/posture
# ---------------------------------------------------------------------------

class TestDashboardPosture:
    """GET /api/dashboard/posture"""

    def test_posture_success(self, client, authed_headers, blueprint_app):
        payload = {
            "posture": {
                "server":        {"secured": 680, "monitored": 41, "discovered": 14},
                "workstation":   {"secured": 2800, "monitored": 620, "discovered": 350},
                "mobile":        {"secured": 0, "monitored": 0, "discovered": 1560},
                "network":       {"secured": 0, "monitored": 100, "discovered": 39},
                "iot":           {"secured": 0, "monitored": 200, "discovered": 149},
                "ot":            {"secured": 0, "monitored": 0, "discovered": 3},
                "miscellaneous": {"secured": 0, "monitored": 0, "discovered": 232},
            },
            "changes": {
                "secured":    {"delta": 8, "period": "week"},
                "monitored":  {"delta": 15, "period": "week"},
                "discovered": {"delta": -3, "period": "week"},
            },
        }
        blueprint_app.dashboard_service.get_posture_data.return_value = payload

        resp = client.get(
            "/api/dashboard/posture", headers=authed_headers("user"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"] == payload
        blueprint_app.dashboard_service.get_posture_data.assert_called_once_with()

    def test_posture_service_error(self, client, authed_headers, blueprint_app):
        blueprint_app.dashboard_service.get_posture_data.side_effect = RuntimeError("DB down")

        resp = client.get(
            "/api/dashboard/posture", headers=authed_headers("user"),
        )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/dashboard/asset-timeline
# ---------------------------------------------------------------------------

class TestDashboardAssetTimeline:
    """GET /api/dashboard/asset-timeline — homepage Added & removed card.

    Must be visible to plain users (the homepage renders for every role)
    and clamp the days window like the per-integration timeline does.
    """

    def test_timeline_success_for_regular_user(self, client, authed_headers, blueprint_app):
        payload = {
            "range": "30d",
            "granularity": "daily",
            "data": [{"label": "Jun 01", "added": 12, "removed": 3}],
        }
        blueprint_app.dashboard_service.get_asset_timeline.return_value = payload

        resp = client.get(
            "/api/dashboard/asset-timeline", headers=authed_headers("user"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"] == payload
        blueprint_app.dashboard_service.get_asset_timeline.assert_called_once_with(days=30)

    def test_timeline_clamps_days(self, client, authed_headers, blueprint_app):
        blueprint_app.dashboard_service.get_asset_timeline.return_value = {"data": []}

        resp = client.get(
            "/api/dashboard/asset-timeline?days=9999", headers=authed_headers("user"),
        )

        assert resp.status_code == 200
        blueprint_app.dashboard_service.get_asset_timeline.assert_called_once_with(days=365)

    def test_timeline_service_error(self, client, authed_headers, blueprint_app):
        blueprint_app.dashboard_service.get_asset_timeline.side_effect = RuntimeError("DB down")

        resp = client.get(
            "/api/dashboard/asset-timeline", headers=authed_headers("user"),
        )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/dashboard/posture-trend
# ---------------------------------------------------------------------------

class TestDashboardPostureTrend:
    """GET /api/dashboard/posture-trend — chevron background bar charts.

    Visible to every role (homepage) and clamps the days window.
    """

    def test_trend_success_for_regular_user(self, client, authed_headers, blueprint_app):
        payload = {
            "days": 30,
            "series": {
                "secured": [0, 1, 2],
                "monitored": [0, 0, 0],
                "discovered": [100, 99, 98],
            },
        }
        blueprint_app.dashboard_service.get_posture_trend.return_value = payload

        resp = client.get(
            "/api/dashboard/posture-trend", headers=authed_headers("user"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"] == payload
        blueprint_app.dashboard_service.get_posture_trend.assert_called_once_with(days=30)

    def test_trend_clamps_days(self, client, authed_headers, blueprint_app):
        blueprint_app.dashboard_service.get_posture_trend.return_value = {"days": 365, "series": {}}

        resp = client.get(
            "/api/dashboard/posture-trend?days=9999", headers=authed_headers("user"),
        )

        assert resp.status_code == 200
        blueprint_app.dashboard_service.get_posture_trend.assert_called_once_with(days=365)

    def test_trend_service_error(self, client, authed_headers, blueprint_app):
        blueprint_app.dashboard_service.get_posture_trend.side_effect = RuntimeError("DB down")

        resp = client.get(
            "/api/dashboard/posture-trend", headers=authed_headers("user"),
        )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/dashboard/coverage-trend
# ---------------------------------------------------------------------------

class TestDashboardCoverageTrend:
    """GET /api/dashboard/coverage-trend — posture-coverage Trend metrics."""

    def test_coverage_success_for_regular_user(self, client, authed_headers, blueprint_app):
        payload = {
            "metric": "srv_edr",
            "range": "30d",
            "granularity": "daily",
            "data": [{"label": "Jun 01", "added": 680, "removed": 7400}],
        }
        blueprint_app.dashboard_service.get_coverage_trend.return_value = payload

        resp = client.get(
            "/api/dashboard/coverage-trend?metric=srv_edr",
            headers=authed_headers("user"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"] == payload
        blueprint_app.dashboard_service.get_coverage_trend.assert_called_once_with(
            metric="srv_edr", days=30,
        )

    def test_coverage_defaults_metric(self, client, authed_headers, blueprint_app):
        blueprint_app.dashboard_service.get_coverage_trend.return_value = {"data": []}

        client.get("/api/dashboard/coverage-trend", headers=authed_headers("user"))

        blueprint_app.dashboard_service.get_coverage_trend.assert_called_once_with(
            metric="srv_edr", days=30,
        )

    def test_coverage_clamps_days(self, client, authed_headers, blueprint_app):
        blueprint_app.dashboard_service.get_coverage_trend.return_value = {"data": []}

        resp = client.get(
            "/api/dashboard/coverage-trend?metric=vuln&days=9999",
            headers=authed_headers("user"),
        )

        assert resp.status_code == 200
        blueprint_app.dashboard_service.get_coverage_trend.assert_called_once_with(
            metric="vuln", days=365,
        )

    def test_coverage_service_error(self, client, authed_headers, blueprint_app):
        blueprint_app.dashboard_service.get_coverage_trend.side_effect = RuntimeError("DB down")

        resp = client.get(
            "/api/dashboard/coverage-trend", headers=authed_headers("user"),
        )
        assert resp.status_code == 500
