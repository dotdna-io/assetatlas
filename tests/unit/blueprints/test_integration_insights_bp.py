# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for integration insights and timeline endpoints.

Endpoints tested:
- GET /api/integrations/<id>/insights  — data quality insights (any auth)
- GET /api/integrations/<id>/timeline  — asset added/removed timeline (any auth)
"""



INTEGRATION_ID = "11111111-1111-1111-1111-111111111111"


# =============================================================================
# Insights (GET /api/integrations/<id>/insights)
# =============================================================================

class TestGetIntegrationInsights:
    """GET /api/integrations/<id>/insights"""

    def test_insights_returns_200(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_insights.return_value = {
            "integration_id": INTEGRATION_ID,
            "stale_assets": {"count": 5, "percentage": 10.0},
            "attribute_coverage": {"hostname": 100, "ip_address": 85},
        }

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/insights",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["integration_id"] == INTEGRATION_ID
        assert "stale_assets" in body

    def test_insights_not_found(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_insights.return_value = {
            "error": "NOT_FOUND",
            "message": "Integration not found",
        }

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/insights",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404

    def test_insights_requires_auth(self, client):
        resp = client.get(f"/api/integrations/{INTEGRATION_ID}/insights")
        assert resp.status_code == 401

    def test_insights_invalid_uuid_returns_404(self, client, auth_headers):
        resp = client.get(
            "/api/integrations/not-a-uuid/insights",
            headers=auth_headers("admin"),
        )
        assert resp.status_code == 404


# =============================================================================
# Timeline (GET /api/integrations/<id>/timeline)
# =============================================================================

class TestGetIntegrationTimeline:
    """GET /api/integrations/<id>/timeline"""

    def test_timeline_returns_200(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_timeline.return_value = {
            "integration_id": INTEGRATION_ID,
            "timeline": [
                {"date": "2026-01-01", "added": 10, "removed": 2},
                {"date": "2026-01-02", "added": 5, "removed": 0},
            ],
        }

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/timeline",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["integration_id"] == INTEGRATION_ID
        assert len(body["timeline"]) == 2

    def test_timeline_not_found(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_timeline.return_value = {
            "error": "NOT_FOUND",
            "message": "Integration not found",
        }

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/timeline",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404

    def test_timeline_requires_auth(self, client):
        resp = client.get(f"/api/integrations/{INTEGRATION_ID}/timeline")
        assert resp.status_code == 401

    def test_timeline_invalid_uuid_returns_404(self, client, auth_headers):
        resp = client.get(
            "/api/integrations/not-a-uuid/timeline",
            headers=auth_headers("admin"),
        )
        assert resp.status_code == 404

    def test_timeline_default_days_is_30(
        self, client, auth_headers, blueprint_app
    ):
        blueprint_app.integration_service.get_timeline.return_value = {
            "integration_id": INTEGRATION_ID, "timeline": [],
        }
        client.get(
            f"/api/integrations/{INTEGRATION_ID}/timeline",
            headers=auth_headers("admin"),
        )
        blueprint_app.integration_service.get_timeline.assert_called_once()
        kwargs = blueprint_app.integration_service.get_timeline.call_args.kwargs
        assert kwargs.get('days') == 30

    def test_timeline_forwards_days_query_param(
        self, client, auth_headers, blueprint_app
    ):
        blueprint_app.integration_service.get_timeline.return_value = {
            "integration_id": INTEGRATION_ID, "timeline": [],
        }
        client.get(
            f"/api/integrations/{INTEGRATION_ID}/timeline?days=7",
            headers=auth_headers("admin"),
        )
        kwargs = blueprint_app.integration_service.get_timeline.call_args.kwargs
        assert kwargs.get('days') == 7

    def test_timeline_clamps_days_above_max(
        self, client, auth_headers, blueprint_app
    ):
        blueprint_app.integration_service.get_timeline.return_value = {
            "integration_id": INTEGRATION_ID, "timeline": [],
        }
        client.get(
            f"/api/integrations/{INTEGRATION_ID}/timeline?days=99999",
            headers=auth_headers("admin"),
        )
        kwargs = blueprint_app.integration_service.get_timeline.call_args.kwargs
        assert kwargs.get('days') == 365

    def test_timeline_clamps_days_below_min(
        self, client, auth_headers, blueprint_app
    ):
        blueprint_app.integration_service.get_timeline.return_value = {
            "integration_id": INTEGRATION_ID, "timeline": [],
        }
        client.get(
            f"/api/integrations/{INTEGRATION_ID}/timeline?days=0",
            headers=auth_headers("admin"),
        )
        kwargs = blueprint_app.integration_service.get_timeline.call_args.kwargs
        assert kwargs.get('days') == 1

    def test_timeline_falls_back_to_default_for_garbage_param(
        self, client, auth_headers, blueprint_app
    ):
        blueprint_app.integration_service.get_timeline.return_value = {
            "integration_id": INTEGRATION_ID, "timeline": [],
        }
        client.get(
            f"/api/integrations/{INTEGRATION_ID}/timeline?days=banana",
            headers=auth_headers("admin"),
        )
        kwargs = blueprint_app.integration_service.get_timeline.call_args.kwargs
        assert kwargs.get('days') == 30
