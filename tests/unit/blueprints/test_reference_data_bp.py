# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the reference-data blueprint (``src/api/blueprints/reference_data.py``).

Regression coverage for the bug where ``POST /api/reference-data/check`` answered
HTTP 502 when the upstream bundle repo was unreachable (the default placeholder
repo, or no outbound network). The React SPA treats any non-2xx as a hard error,
so users saw a raw "Request failed ... (502)" instead of the intended "Upstream
unavailable" message. "Upstream unreachable" is a normal check outcome, so the
endpoint must return 200 with a ``status: upstream_unavailable`` body that the
UI's onSuccess branch renders.
"""

from src.modules.reference_data.sources import UpstreamUnavailableError


class TestCheckForUpdatesAuth:
    """`POST /api/reference-data/check` requires an authenticated admin."""

    def test_check_requires_auth(self, client):
        assert client.post("/api/reference-data/check").status_code == 401

    def test_check_forbidden_for_regular_user(self, client, authed_headers):
        resp = client.post(
            "/api/reference-data/check", headers=authed_headers("user")
        )
        assert resp.status_code == 403


class TestCheckForUpdates:
    """`POST /api/reference-data/check` — inspect upstream without applying."""

    def test_upstream_unavailable_returns_200_not_502(
        self, client, authed_headers, blueprint_app
    ):
        # The regression: this path used to return 502, which the SPA surfaced
        # as a raw gateway error. It must answer 200 so the UI can render the
        # friendly "Upstream unavailable" message via its onSuccess handler.
        blueprint_app.reference_data_service.check_upstream.side_effect = (
            UpstreamUnavailableError("GitHub API unreachable (404)")
        )
        resp = client.post(
            "/api/reference-data/check", headers=authed_headers("admin")
        )
        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["status"] == "upstream_unavailable"
        assert "unreachable" in body["message"]

    def test_available_update_returns_summary(
        self, client, authed_headers, blueprint_app
    ):
        blueprint_app.reference_data_service.check_upstream.return_value = {
            "available": True,
            "upstream_version": "2026.24.0",
            "current_version": "2026.20.0",
            "datasets": [],
        }
        resp = client.post(
            "/api/reference-data/check", headers=authed_headers("admin")
        )
        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["available"] is True
        assert body["upstream_version"] == "2026.24.0"
