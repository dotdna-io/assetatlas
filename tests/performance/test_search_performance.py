# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Search Performance Benchmarks

Measures response times for synonym lookup and suggestion API.

Run with: pytest tests/performance/ -v --tb=short
"""

import json
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock


SYNONYM_FILE = Path(__file__).parent.parent.parent / 'static' / 'synonym_lookup.json'


class TestSynonymLookupPerformance:
    """Synonym file loading and lookup performance."""

    @pytest.mark.skipif(not SYNONYM_FILE.exists(), reason='synonym file missing')
    def test_synonym_file_loads_under_200ms(self):
        start = time.perf_counter()
        with open(SYNONYM_FILE) as f:
            data = json.load(f)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 200, f'Synonym file load took {elapsed_ms:.0f}ms (target: <200ms)'
        assert len(data.get('synonyms', {})) > 0

    @pytest.mark.skipif(not SYNONYM_FILE.exists(), reason='synonym file missing')
    def test_synonym_lookup_under_1ms(self):
        with open(SYNONYM_FILE) as f:
            data = json.load(f)
        synonyms = data.get('synonyms', {})

        terms = list(synonyms.keys())[:50] * 20
        start = time.perf_counter()
        for term in terms:
            _ = synonyms.get(term)
        elapsed_ms = (time.perf_counter() - start) * 1000
        per_lookup_ms = elapsed_ms / len(terms)
        assert per_lookup_ms < 1, f'Per-lookup: {per_lookup_ms:.3f}ms (target: <1ms)'


class TestSuggestionAPIPerformance:
    """Suggestion API response time benchmarks."""

    def test_suggestion_endpoint_under_100ms_warm(self, client, auth_headers, blueprint_app):
        """Warm suggestion API should respond in <100ms."""
        mock_service = MagicMock()
        mock_service.get_suggestions.return_value = [
            {'field_name': 'hostname', 'score': 0.95}
        ]
        blueprint_app.suggestion_service = mock_service

        client.get('/api/suggestions?q=host', headers=auth_headers('user'))

        start = time.perf_counter()
        response = client.get('/api/suggestions?q=machine', headers=auth_headers('user'))
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert response.status_code == 200
        assert elapsed_ms < 100, f'Warm response: {elapsed_ms:.0f}ms (target: <100ms)'
