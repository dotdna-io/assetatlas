# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for admin embedding management endpoints."""
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def reset_refresh_status():
    """Reset background refresh state between tests."""
    from src.api.blueprints.admin_embedding import _refresh_status
    _refresh_status['running'] = False
    _refresh_status['last_result'] = None
    yield
    _refresh_status['running'] = False
    _refresh_status['last_result'] = None


class TestAdminEmbeddingRefresh:
    """POST /api/admin/embedding/refresh"""

    def test_incremental_refresh_returns_started(self, client, auth_headers, blueprint_app):
        blueprint_app.embedding_integration_service = MagicMock()
        response = client.post(
            '/api/admin/embedding/refresh',
            json={'mode': 'incremental'},
            headers=auth_headers('admin'),
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['data']['status'] == 'started'
        assert data['data']['mode'] == 'incremental'

    def test_refresh_defaults_to_incremental(self, client, auth_headers, blueprint_app):
        blueprint_app.embedding_integration_service = MagicMock()
        response = client.post(
            '/api/admin/embedding/refresh',
            json={},
            headers=auth_headers('admin'),
        )
        assert response.status_code == 200
        assert response.get_json()['data']['mode'] == 'incremental'

    def test_refresh_rejects_concurrent(self, client, auth_headers, blueprint_app):
        from src.api.blueprints.admin_embedding import _refresh_status
        blueprint_app.embedding_integration_service = MagicMock()
        _refresh_status['running'] = True
        try:
            response = client.post(
                '/api/admin/embedding/refresh',
                json={'mode': 'incremental'},
                headers=auth_headers('admin'),
            )
            assert response.status_code == 200
            assert response.get_json()['data']['status'] == 'already_running'
        finally:
            _refresh_status['running'] = False

    def test_refresh_resets_running_when_thread_spawn_fails(self, client, auth_headers, blueprint_app, monkeypatch):
        import src.api.blueprints.admin_embedding as mod
        blueprint_app.embedding_integration_service = MagicMock()

        class _BoomThread:
            def __init__(self, target, args, daemon):
                pass

            def start(self):
                raise RuntimeError('cannot spawn')

        monkeypatch.setattr(mod.threading, 'Thread', _BoomThread)
        resp = client.post(
            '/api/admin/embedding/refresh',
            json={'mode': 'incremental'},
            headers=auth_headers('admin'),
        )
        assert resp.status_code == 500
        # Our spawn-guard returns a plain dict with a 'success' key, so
        # response_wrapper passes it through as-is (unlike the file's
        # pre-existing jsonify(...) error branches, which it mis-wraps).
        assert resp.get_json() == {
            'success': False, 'error': 'Failed to start embedding refresh'}
        # A spawn failure must not leave the job wedged 'running' forever.
        assert mod._refresh_status['running'] is False

    def test_full_refresh_as_admin_returns_403_json_body(self, client, auth_headers, blueprint_app):
        """A plain admin (not super_admin) requesting mode='full' gets a real
        JSON 403 — the pre-existing jsonify(...) form serializes {'success':
        True, 'data': <Response>} instead (response_wrapper nests the
        jsonify Response as data), which is not JSON-serializable."""
        blueprint_app.embedding_integration_service = MagicMock()
        response = client.post(
            '/api/admin/embedding/refresh',
            json={'mode': 'full'},
            headers=auth_headers('admin'),
        )
        assert response.status_code == 403
        assert response.get_json() == {
            'success': False, 'error': 'Full refresh requires super_admin role'}

    def test_refresh_returns_503_json_when_service_unavailable(self, client, auth_headers, blueprint_app):
        """Same jsonify(...)-nesting bug on the service-None branch."""
        blueprint_app.embedding_integration_service = None
        try:
            response = client.post(
                '/api/admin/embedding/refresh',
                json={},
                headers=auth_headers('admin'),
            )
            assert response.status_code == 503
            assert response.get_json() == {
                'success': False, 'error': 'Embedding integration service not available'}
        finally:
            # embedding_integration_service lives on the session-scoped
            # blueprint_app; restore it so later tests get their usual MagicMock.
            blueprint_app.embedding_integration_service = MagicMock()

    def test_background_runner_resets_running_when_context_entry_fails(self):
        import src.api.blueprints.admin_embedding as mod

        class _Ctx:
            def __enter__(self):
                raise RuntimeError('no context')

            def __exit__(self, *a):
                return False

        class _App:
            def app_context(self):
                return _Ctx()

        mod._refresh_status['running'] = True
        mod._run_refresh_in_background(_App(), 'incremental')
        assert mod._refresh_status['running'] is False
        assert 'no context' in mod._refresh_status['last_result']['error']


class TestAdminEmbeddingStatus:
    """GET /api/admin/embedding/status"""

    def test_status_returns_all_components(self, client, auth_headers, blueprint_app):
        blueprint_app.embedding_integration_service = MagicMock()
        blueprint_app.embedding_integration_service.get_status.return_value = {
            'field_registry': {'total': 52, 'static': 20, 'extended': 32},
            'chromadb': {'fields_count': 49},
            'synonyms': {'total_terms': 83},
        }
        response = client.get(
            '/api/admin/embedding/status',
            headers=auth_headers('admin'),
        )
        assert response.status_code == 200
        data = response.get_json()
        assert 'field_registry' in data['data']
        assert 'refresh' in data['data']
