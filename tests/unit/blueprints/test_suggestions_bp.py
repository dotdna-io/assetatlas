# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the suggestions blueprint (no auth on routes, uses suggestion_service).

Endpoints tested:
- GET /api/suggestions          — semantic search suggestions
- GET /api/suggestions/health   — suggestion service health
- GET /api/suggestions/stats    — suggestion service statistics
"""

import pytest
from unittest.mock import patch


class TestGetSuggestions:
    """GET /api/suggestions -- returns semantic field suggestions."""

    def test_missing_query_returns_400(self, client):
        """Missing 'q' parameter returns 400."""
        resp = client.get("/api/suggestions")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data

    def test_empty_query_returns_400(self, client):
        """Empty 'q' parameter returns 400."""
        resp = client.get("/api/suggestions?q=")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "Missing query parameter" in data["error"]

    def test_short_query_returns_empty_suggestions(self, client):
        """Single-character query returns empty suggestions with message."""
        resp = client.get("/api/suggestions?q=x")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["suggestions"] == []
        assert "too short" in data.get("message", "").lower()

    def test_valid_query_returns_suggestions(self, client, blueprint_app):
        """Valid query returns suggestions from the service."""
        mock_result = {
            "suggestions": [
                {"type": "field", "field_name": "hostname", "score": 0.95}
            ],
            "query": "machine name",
            "query_time_ms": 42,
            "total_found": 1,
        }
        blueprint_app.suggestion_service.get_suggestions.return_value = mock_result

        resp = client.get("/api/suggestions?q=machine+name")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["suggestions"]) == 1
        assert data["suggestions"][0]["field_name"] == "hostname"

    def test_limit_parameter_clamped(self, client, blueprint_app):
        """Limit parameter is clamped to 1-20 range."""
        blueprint_app.suggestion_service.get_suggestions.return_value = {
            "suggestions": [], "query": "test", "query_time_ms": 0, "total_found": 0,
        }

        # Limit too high
        client.get("/api/suggestions?q=test&limit=100")
        call_args = blueprint_app.suggestion_service.get_suggestions.call_args
        assert call_args.kwargs.get("limit", call_args[1].get("limit")) == 20

    def test_min_score_parameter(self, client, blueprint_app):
        """min_score parameter is passed to the service."""
        blueprint_app.suggestion_service.get_suggestions.return_value = {
            "suggestions": [], "query": "test", "query_time_ms": 0, "total_found": 0,
        }
        client.get("/api/suggestions?q=test&min_score=0.8")
        call_args = blueprint_app.suggestion_service.get_suggestions.call_args
        assert call_args.kwargs.get("min_score", call_args[1].get("min_score")) == 0.8

    def test_service_exception_returns_graceful_degradation(self, client, blueprint_app):
        """When suggestion_service raises, returns empty suggestions with message."""
        blueprint_app.suggestion_service.get_suggestions.side_effect = RuntimeError(
            "Model not loaded"
        )

        resp = client.get("/api/suggestions?q=hostname")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["suggestions"] == []
        assert data["service_unavailable"] is True
        assert "not available" in data["message"].lower()

    def test_include_values_parameter(self, client, blueprint_app):
        """include_values parameter is passed to the service."""
        blueprint_app.suggestion_service.get_suggestions.return_value = {
            "suggestions": [], "query": "test", "query_time_ms": 0, "total_found": 0,
        }
        client.get("/api/suggestions?q=test&include_values=true")
        call_args = blueprint_app.suggestion_service.get_suggestions.call_args
        assert call_args.kwargs.get("include_values", call_args[1].get("include_values")) is True


class TestSuggestionsHealth:
    """GET /api/suggestions/health -- service health check."""

    def test_healthy_service_returns_200(self, client, blueprint_app):
        """Healthy service returns 200."""
        blueprint_app.suggestion_service.health_check.return_value = {
            "status": "healthy",
            "chromadb_available": True,
            "fields_count": 49,
            "model_loaded": True,
        }
        resp = client.get("/api/suggestions/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "healthy"

    def test_unhealthy_service_returns_503(self, client, blueprint_app):
        """Unhealthy service returns 503."""
        blueprint_app.suggestion_service.health_check.return_value = {
            "status": "unhealthy",
            "error": "ChromaDB not available",
        }
        resp = client.get("/api/suggestions/health")
        assert resp.status_code == 503
        data = resp.get_json()
        assert data["status"] == "unhealthy"

    def test_health_exception_returns_503(self, client, blueprint_app):
        """Exception during health check returns 503."""
        blueprint_app.suggestion_service.health_check.side_effect = RuntimeError("boom")
        resp = client.get("/api/suggestions/health")
        assert resp.status_code == 503
        data = resp.get_json()
        assert data["status"] == "unhealthy"
        assert "error" in data


class TestSuggestionsStats:
    """GET /api/suggestions/stats -- service statistics."""

    def test_stats_returns_200(self, client, blueprint_app):
        """Stats endpoint returns service statistics."""
        blueprint_app.suggestion_service.get_stats.return_value = {
            "embedding_model": "bge-base-en-v1.5",
            "embedding_loaded": True,
            "chromadb_fields_count": 49,
            "chromadb_values_count": 120,
        }
        resp = client.get("/api/suggestions/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["embedding_model"] == "bge-base-en-v1.5"

    def test_stats_exception_returns_500(self, client, blueprint_app):
        """Exception during stats returns 500."""
        blueprint_app.suggestion_service.get_stats.side_effect = RuntimeError(
            "service down"
        )
        resp = client.get("/api/suggestions/stats")
        assert resp.status_code == 500
        data = resp.get_json()
        assert "error" in data
