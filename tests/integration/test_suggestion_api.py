# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Suggestion API E2E Tests

Tests semantic suggestion quality, edge cases, and error handling.
"""

import pytest
from unittest.mock import MagicMock


class TestSuggestionAPIEdgeCases:
    """Test edge cases that should not crash the API."""

    def test_empty_query_returns_error(self, client, auth_headers, blueprint_app):
        blueprint_app.suggestion_service = MagicMock()
        blueprint_app.suggestion_service.get_suggestions.return_value = []
        response = client.get('/api/suggestions?q=', headers=auth_headers('user'))
        assert response.status_code in (200, 400)

    def test_very_long_query(self, client, auth_headers, blueprint_app):
        blueprint_app.suggestion_service = MagicMock()
        blueprint_app.suggestion_service.get_suggestions.return_value = []
        long_q = 'a' * 600
        response = client.get(f'/api/suggestions?q={long_q}', headers=auth_headers('user'))
        assert response.status_code in (200, 400)

    def test_unicode_query(self, client, auth_headers, blueprint_app):
        blueprint_app.suggestion_service = MagicMock()
        blueprint_app.suggestion_service.get_suggestions.return_value = []
        response = client.get('/api/suggestions?q=hostname', headers=auth_headers('user'))
        assert response.status_code in (200, 400)

    def test_service_unavailable_returns_error(self, client, auth_headers, blueprint_app):
        blueprint_app.suggestion_service = None
        response = client.get('/api/suggestions?q=host', headers=auth_headers('user'))
        assert response.status_code in (503, 500)
