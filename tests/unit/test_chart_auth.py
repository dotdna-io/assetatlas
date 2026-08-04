# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests that chart query endpoint requires authentication.

Regression test for #183: POST /api/charts/query was unauthenticated.
"""

import pytest


class TestChartsQueryAuth:
    """POST /api/charts/query must require JWT authentication."""

    def test_unauthenticated_query_returns_401(self, client):
        """Request without JWT token must be rejected."""
        payload = {
            "queryType": "distribution",
            "from": {"table": "assets"},
            "select": {"dimension": "category"},
        }
        resp = client.post("/api/charts/query", json=payload)
        assert resp.status_code == 401

    def test_authenticated_query_reaches_handler(self, client, auth_headers):
        """Request with valid JWT passes auth and reaches validation."""
        payload = {
            "queryType": "distribution",
            "from": {"table": "assets"},
            "select": {"dimension": "category"},
        }
        headers = auth_headers(role="user")
        resp = client.post("/api/charts/query", json=payload, headers=headers)
        # Should not be 401 — may be 200 or 500 depending on mocks,
        # but auth must pass
        assert resp.status_code != 401
