# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for GET /api/auth/teams-bridge/config (service-API-key gated)."""
from __future__ import annotations

import pytest


# Must be >=32 chars to satisfy require_service_api_key's fail-closed check.
_KEY = 'test-bridge-key-' + 'x' * 32


@pytest.fixture(autouse=True)
def _set_bridge_key(monkeypatch):
    monkeypatch.setenv('ASSETATLAS_BRIDGE_API_KEY', _KEY)
    yield


def _key_headers():
    return {'X-Service-Key': _KEY}


class TestBridgeConfigEndpoint:
    def test_returns_decrypted_config(self, blueprint_app, client):
        # The shared `client` fixture replaces `settings_manager.get` with a
        # plain function (see tests/conftest.py), so override it the same way
        # here rather than using MagicMock.side_effect.
        def _get(category, key, default=None):
            return {
                ('teams', 'enabled'): True,
                ('teams', 'bot_app_id'): (
                    'aaaa1111-1111-1111-1111-111111111111'
                ),
                ('teams', 'bot_app_password'): 'plaintext-secret',
                ('teams', 'bot_app_tenant_id'): (
                    'bbbb2222-2222-2222-2222-222222222222'
                ),
            }.get((category, key), default)
        blueprint_app.settings_manager.get = _get

        resp = client.get(
            '/api/auth/teams-bridge/config', headers=_key_headers(),
        )
        assert resp.status_code == 200
        data = resp.get_json()['data']
        assert data == {
            'enabled': True,
            'bot_app_id': 'aaaa1111-1111-1111-1111-111111111111',
            'bot_app_password': 'plaintext-secret',
            'bot_app_tenant_id': 'bbbb2222-2222-2222-2222-222222222222',
        }

    def test_rejects_missing_key_header(self, client):
        resp = client.get('/api/auth/teams-bridge/config')
        assert resp.status_code == 401

    def test_rejects_wrong_key(self, client):
        resp = client.get(
            '/api/auth/teams-bridge/config',
            headers={'X-Service-Key': 'different-key-' + 'y' * 32},
        )
        assert resp.status_code == 403
