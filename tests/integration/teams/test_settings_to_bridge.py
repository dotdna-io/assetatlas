# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end: PUT teams settings → bridge config endpoint returns the new tuple."""
from __future__ import annotations

import os
import secrets

import pytest

pytestmark = pytest.mark.integration


_NEW_ID = '99999999-9999-9999-9999-999999999999'
_NEW_TENANT = '88888888-8888-8888-8888-888888888888'
_TEST_SECRET = 'integration-test-secret-' + secrets.token_hex(8)


@pytest.fixture
def _bridge_key(monkeypatch):
    """Set a known bridge API key for this test, ensuring it's at least
    32 chars (required by require_service_api_key's fail-closed check)."""
    key = 'integration-test-bridge-key-' + secrets.token_hex(16)
    monkeypatch.setenv('ASSETATLAS_BRIDGE_API_KEY', key)
    return key


@pytest.fixture
def _reset_teams_settings(live_db):
    """Clear any prior teams settings rows so the test runs clean.

    Uses the db_adapter wired into the live app (live_db === live_app.db_adapter).
    """
    def _wipe():
        live_db.execute_query(
            "UPDATE system_settings "
            "SET value=default_value "
            "WHERE category='teams'",
            fetch=False,
        )
    _wipe()
    yield
    _wipe()


def test_settings_change_propagates_to_bridge_config(
    live_client, live_admin_headers, _bridge_key, _reset_teams_settings,
):
    """An admin PUT lands; a bridge poll sees the new tuple."""
    # Save valid creds via the admin API (one PUT per field).
    fields = [
        ('enabled', True),
        ('bot_app_id', _NEW_ID),
        ('bot_app_password', _TEST_SECRET),
        ('bot_app_tenant_id', _NEW_TENANT),
    ]
    for key, val in fields:
        r = live_client.put(
            f'/api/settings/teams/{key}',
            json={'value': val}, headers=live_admin_headers,
        )
        assert r.status_code == 200, (
            f"PUT teams.{key} failed: {r.status_code} {r.get_data(as_text=True)}"
        )

    # Bridge polls the config endpoint with the service API key.
    r = live_client.get(
        '/api/auth/teams-bridge/config',
        headers={'X-Service-Key': _bridge_key},
    )
    assert r.status_code == 200, (
        f"bridge-config GET failed: {r.status_code} {r.get_data(as_text=True)}"
    )
    body = r.get_json()
    data = body.get('data', body)

    # The bridge sees the decrypted password (not __SET__).
    assert data == {
        'enabled': True,
        'bot_app_id': _NEW_ID,
        'bot_app_password': _TEST_SECRET,
        'bot_app_tenant_id': _NEW_TENANT,
    }


def test_admin_GET_redacts_password_after_save(
    live_client, live_admin_headers, _bridge_key, _reset_teams_settings,
):
    """The same change, viewed via the admin GET, returns the __SET__ sentinel."""
    live_client.put(
        '/api/settings/teams/bot_app_password',
        json={'value': _TEST_SECRET}, headers=live_admin_headers,
    )
    r = live_client.get('/api/settings/teams', headers=live_admin_headers)
    assert r.status_code == 200
    body = r.get_json()
    data = body.get('data', body)
    assert data['bot_app_password'] == '__SET__'


def test_messaging_endpoint_uses_app_config(live_client, live_admin_headers):
    """The /messaging-endpoint route returns whatever app.config has."""
    r = live_client.get(
        '/api/settings/teams/messaging-endpoint',
        headers=live_admin_headers,
    )
    assert r.status_code == 200
    body = r.get_json()
    data = body.get('data', body)
    assert data['url'].endswith('/teams/api/messages')
    assert isinstance(data['https_ok'], bool)


def test_test_connection_decrypts_saved_password(
    live_client, live_admin_headers, _bridge_key, _reset_teams_settings,
    monkeypatch,
):
    """When the request body omits bot_app_password, the saved (encrypted)
    value must be decrypted before being forwarded to the bridge."""
    # Save creds via the admin API (encrypts bot_app_password at rest).
    for key, val in (
        ('enabled', True),
        ('bot_app_id', _NEW_ID),
        ('bot_app_password', _TEST_SECRET),
        ('bot_app_tenant_id', _NEW_TENANT),
    ):
        r = live_client.put(
            f'/api/settings/teams/{key}',
            json={'value': val}, headers=live_admin_headers,
        )
        assert r.status_code == 200

    # Stub the bridge HTTP call so we can capture what password was sent.
    captured = {}

    def _fake_post(url, json=None, timeout=None):  # noqa: ARG001
        captured['payload'] = json
        class _Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {'ok': True}
        return _Resp()

    monkeypatch.setattr(
        'src.api.blueprints.settings.teams_settings_routes._requests.post',
        _fake_post,
    )

    r = live_client.post(
        '/api/settings/teams/test-connection',
        json={}, headers=live_admin_headers,
    )
    assert r.status_code == 200
    assert r.get_json()['data'] == {'ok': True}
    # Most importantly: the password forwarded to the bridge is plaintext,
    # NOT the ENC: ciphertext.
    assert captured['payload']['bot_app_password'] == _TEST_SECRET
    assert not captured['payload']['bot_app_password'].startswith('ENC:')
