# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for suggestion deduplication by integration group."""

from unittest.mock import patch, MagicMock


class TestSuggestionDeduplication:
    """GET /api/suggestions — field suggestions should be deduplicated by group."""

    @patch("src.api.blueprints.suggestions.get_cached_group_mapping")
    def test_duplicate_field_suggestions_collapsed(
        self, mock_mapping, client, authed_headers, blueprint_app
    ):
        mock_mapping.return_value = {"Entra - A": "Entra", "Entra - B": "Entra"}

        # Mock the suggestion service to return duplicates
        mock_service = MagicMock()
        mock_service.get_suggestions.return_value = {
            "suggestions": [
                {"type": "field", "field_name": "entra_-_a__device_trust", "display_name": "Device Trust", "data_source": "Entra - A", "score": 0.9},
                {"type": "field", "field_name": "entra_-_b__device_trust", "display_name": "Device Trust", "data_source": "Entra - B", "score": 0.85},
                {"type": "field", "field_name": "intune__enrollment", "display_name": "Enrollment", "data_source": "Intune", "score": 0.8},
            ],
            "query": "device",
            "query_time_ms": 50,
            "total_found": 3,
        }
        blueprint_app.suggestion_service = mock_service

        resp = client.get(
            "/api/suggestions?q=device",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        suggestions = resp.get_json()["suggestions"]

        # Should collapse Entra - A and Entra - B into one "Entra" suggestion
        entra_suggestions = [s for s in suggestions if s.get("data_source") == "Entra"]
        assert len(entra_suggestions) == 1

        # Intune unchanged
        intune_suggestions = [s for s in suggestions if s.get("data_source") == "Intune"]
        assert len(intune_suggestions) == 1

    @patch("src.api.blueprints.suggestions.get_cached_group_mapping")
    def test_no_groups_suggestions_unchanged(
        self, mock_mapping, client, authed_headers, blueprint_app
    ):
        mock_mapping.return_value = {}

        mock_service = MagicMock()
        mock_service.get_suggestions.return_value = {
            "suggestions": [
                {"type": "field", "field_name": "entra_-_a__device_trust", "display_name": "Device Trust", "data_source": "Entra - A", "score": 0.9},
            ],
            "query": "device",
            "query_time_ms": 50,
            "total_found": 1,
        }
        blueprint_app.suggestion_service = mock_service

        resp = client.get(
            "/api/suggestions?q=device",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        suggestions = resp.get_json()["suggestions"]
        assert len(suggestions) == 1
        assert suggestions[0]["data_source"] == "Entra - A"
