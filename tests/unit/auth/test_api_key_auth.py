# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for @require_api_key decorator."""

import pytest
from unittest.mock import MagicMock
from flask import Flask, g


def make_app():
    """Create a minimal Flask app with a protected test endpoint."""
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config['EXTERNAL_API_RATE_LIMIT'] = 60  # default, overridden per test

    from src.auth.api_key_auth import require_api_key

    @app.route('/test')
    @require_api_key
    def protected():
        return {'user': g.api_key_user['username'], 'key_id': g.api_key_id}

    return app


@pytest.fixture
def app():
    app = make_app()
    app.api_key_service = MagicMock()
    return app


@pytest.fixture(autouse=True)
def _clear_rate_counters():
    """Clear rate limit counters between tests."""
    from src.auth.api_key_auth import _rate_counters
    _rate_counters.clear()
    yield
    _rate_counters.clear()


@pytest.fixture
def client(app):
    return app.test_client()


class TestRequireApiKey:
    def test_missing_header_returns_401(self, client):
        resp = client.get('/test')
        assert resp.status_code == 401
        body = resp.get_json()
        assert body['error'] == 'missing_api_key'

    def test_invalid_key_returns_401(self, client, app):
        app.api_key_service.validate_key.return_value = None
        resp = client.get('/test', headers={'X-API-Key': 'aa_badkey'})
        assert resp.status_code == 401
        body = resp.get_json()
        assert body['error'] == 'invalid_api_key'

    def test_valid_key_passes_through(self, client, app):
        app.api_key_service.validate_key.return_value = {
            'id': 'key-uuid',
            'user_id': 'user-uuid',
            'username': 'admin',
            'role': 'admin',
        }
        resp = client.get('/test', headers={'X-API-Key': 'aa_goodkey12345678901234567890123456789012'})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['user'] == 'admin'
        assert body['key_id'] == 'key-uuid'

    def test_records_usage_after_success(self, client, app):
        app.api_key_service.validate_key.return_value = {
            'id': 'key-uuid', 'user_id': 'u', 'username': 'a', 'role': 'admin',
        }
        client.get('/test', headers={'X-API-Key': 'aa_goodkey12345678901234567890123456789012'})
        app.api_key_service.record_usage.assert_called_once()
        call_args = app.api_key_service.record_usage.call_args[0]
        assert call_args[0] == 'key-uuid'

    def test_passes_last_used_at_through_to_record_usage(self, client, app):
        """The throttle gate lives in the service; the decorator hands it the
        stamp its own validate_key() lookup returned (None when absent)."""
        from datetime import datetime, timezone
        last_used = datetime.now(timezone.utc)
        app.api_key_service.validate_key.return_value = {
            'id': 'key-uuid', 'user_id': 'u', 'username': 'a', 'role': 'admin',
            'last_used_at': last_used,
        }
        client.get('/test', headers={'X-API-Key': 'aa_goodkey12345678901234567890123456789012'})
        call_args = app.api_key_service.record_usage.call_args[0]
        assert call_args[0] == 'key-uuid'
        assert call_args[2] is last_used


class TestRateLimiting:
    def test_rate_limit_returns_429(self, client, app):
        """Exceeding rate limit returns 429 with Retry-After header."""
        app.api_key_service.validate_key.return_value = {
            'id': 'key-uuid', 'user_id': 'u', 'username': 'a', 'role': 'admin',
        }
        app.config['EXTERNAL_API_RATE_LIMIT'] = 2

        # First 2 requests should succeed
        for _ in range(2):
            resp = client.get('/test', headers={'X-API-Key': 'aa_goodkey12345678901234567890123456789012'})
            assert resp.status_code == 200

        # Third request should be rate limited
        resp = client.get('/test', headers={'X-API-Key': 'aa_goodkey12345678901234567890123456789012'})
        assert resp.status_code == 429
        body = resp.get_json()
        assert body['error'] == 'rate_limit_exceeded'
        assert 'Retry-After' in resp.headers


# ---------------------------------------------------------------------------
# Parameterized rate-limit setting (per-path budget)
# ---------------------------------------------------------------------------

INV_KEY = {'X-API-Key': 'aa_inventorykey1234567890123456789012345678'}


def make_inventory_app():
    """Flask app whose route opts into a named rate-limit config key.

    Mirrors how the inventory endpoint uses @require_api_key with a
    per-path budget instead of the default EXTERNAL_API_RATE_LIMIT.
    """
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config['EXTERNAL_API_RATE_LIMIT'] = 60
    app.config['EXTERNAL_API_INVENTORY_RATE_LIMIT'] = 600

    from src.auth.api_key_auth import require_api_key

    @app.route('/inventory')
    @require_api_key(rate_limit_setting='EXTERNAL_API_INVENTORY_RATE_LIMIT')
    def inventory():
        return {'user': g.api_key_user['username']}

    return app


class TestParameterizedRateLimit:
    @pytest.fixture
    def inv_app(self):
        app = make_inventory_app()
        app.api_key_service = MagicMock()
        app.api_key_service.validate_key.return_value = {
            'id': 'k', 'user_id': 'u', 'username': 'svc', 'role': 'user',
        }
        return app

    @pytest.fixture
    def inv_client(self, inv_app):
        return inv_app.test_client()

    def test_bare_decorator_still_supported(self, client, app):
        """@require_api_key with no arguments keeps working (backward compat)."""
        app.api_key_service.validate_key.return_value = {
            'id': 'k', 'user_id': 'u', 'username': 'a', 'role': 'admin',
        }
        resp = client.get('/test', headers={'X-API-Key': 'aa_goodkey12345678901234567890123456789012'})
        assert resp.status_code == 200

    def test_consults_named_setting_not_default(self, inv_app, inv_client):
        """The named budget (2) gates the route, not the default (60).

        If the default were used, the 3rd request would pass; a 429 proves
        the decorator read EXTERNAL_API_INVENTORY_RATE_LIMIT.
        """
        inv_app.config['EXTERNAL_API_RATE_LIMIT'] = 60
        inv_app.config['EXTERNAL_API_INVENTORY_RATE_LIMIT'] = 2

        for _ in range(2):
            assert inv_client.get('/inventory', headers=INV_KEY).status_code == 200

        resp = inv_client.get('/inventory', headers=INV_KEY)
        assert resp.status_code == 429
        assert resp.headers.get('Retry-After')

    def test_independent_of_lookup_default(self, inv_app, inv_client):
        """The inventory path honors its own higher budget even when the
        default lookup limit is tightened below the request rate."""
        inv_app.config['EXTERNAL_API_RATE_LIMIT'] = 2
        inv_app.config['EXTERNAL_API_INVENTORY_RATE_LIMIT'] = 10

        for _ in range(3):
            assert inv_client.get('/inventory', headers=INV_KEY).status_code == 200
