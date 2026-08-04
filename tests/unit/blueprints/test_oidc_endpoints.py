# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the 3 OIDC SSO endpoints.

POST /api/auth/oidc/login
GET  /api/auth/oidc/callback
POST /api/auth/oidc/exchange
"""
from unittest.mock import patch, MagicMock

import pytest

from src.api.utils.caching import cache
from src.auth.services.oidc_service import OidcUserInfo

OIDC_SVC = 'src.api.blueprints.auth.OidcService'
USER_REPO = 'src.api.blueprints.auth.UserRepository'
SESSION_REPO = 'src.api.blueprints.auth.SessionRepository'
AUDIT_SVC = 'src.api.blueprints.auth.AuditService'
TOKEN_SVC = 'src.api.blueprints.auth.TokenService'

# A minimal user dict returned by user_repo stubs.
_USER = {
    'id': 'user-uuid-1',
    'username': 'alice',
    'email': 'alice@example.com',
    'full_name': 'Alice Smith',
    'role': 'user',
    'is_active': True,
    'email_verified': True,
    'must_change_password': False,
    'last_login_at': None,
    'created_at': '2025-01-01T00:00:00',
    'password_hash': 'OIDC:unusable',
}

_OIDC_USER_INFO = OidcUserInfo(
    subject='oidc-sub-123',
    email='alice@example.com',
    full_name='Alice Smith',
    role='user',
    groups=[],
)

# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_oidc_svc(
    enabled=True,
    auth_url='https://idp.example.com/authorize',
    state='test-state-abc',
    exchange_result=_OIDC_USER_INFO,
    complete_url='/oidc-complete',
):
    """Return a mock OidcService instance."""
    svc = MagicMock()
    svc.is_enabled.return_value = enabled
    svc.build_authorization_url.return_value = (auth_url, state)
    svc.exchange_code.return_value = exchange_result
    svc.frontend_complete_url.return_value = complete_url
    return svc


def _make_user_repo(user=_USER, auto_create=True):
    repo = MagicMock()
    repo.get_or_create_oidc_user.return_value = user
    repo.get_user_by_email.return_value = user
    repo.get_user_by_id.return_value = user
    repo.get_user_by_username.return_value = user
    return repo


def _make_session_repo(session_id='sess-uuid-1'):
    repo = MagicMock()
    repo.create_session.return_value = session_id
    return repo


def _make_token_meta():
    return {
        'access_token_jti': 'jti-abc',
        'user_claims': {'sub': 'user-uuid-1', 'role': 'user'},
    }


# ─── POST /api/auth/oidc/login ────────────────────────────────────────────────

class TestOidcLogin:

    def test_returns_authorization_url_when_enabled(self, blueprint_app):
        client = blueprint_app.test_client()
        with patch(OIDC_SVC) as OidcCls, patch(AUDIT_SVC):
            OidcCls.return_value = _make_oidc_svc()
            r = client.post('/api/auth/oidc/login')
        assert r.status_code == 200
        assert r.json['authorization_url'] == 'https://idp.example.com/authorize'

    def test_returns_503_when_oidc_disabled(self, blueprint_app):
        client = blueprint_app.test_client()
        with patch(OIDC_SVC) as OidcCls, patch(AUDIT_SVC):
            OidcCls.return_value = _make_oidc_svc(enabled=False)
            r = client.post('/api/auth/oidc/login')
        assert r.status_code == 503
        assert r.json['error'] == 'oidc_not_enabled'

    def test_returns_503_when_discovery_fails(self, blueprint_app):
        client = blueprint_app.test_client()
        with patch(OIDC_SVC) as OidcCls, patch(AUDIT_SVC):
            svc = _make_oidc_svc()
            svc.build_authorization_url.side_effect = Exception("discovery failed")
            OidcCls.return_value = svc
            r = client.post('/api/auth/oidc/login')
        assert r.status_code == 503
        assert r.json['error'] == 'oidc_unavailable'

    def test_rate_limited_after_threshold(self, blueprint_app):
        original_get = blueprint_app.settings_manager.get

        def _strict_get(cat, key, default=None):
            if key == 'token_verify_rate_limit_per_ip_per_hour':
                return 1
            return default

        blueprint_app.settings_manager.get = _strict_get
        try:
            with blueprint_app.app_context():
                cache.delete('ratelimit:oidc_login:127.0.0.1')

            client = blueprint_app.test_client()

            with patch(OIDC_SVC) as OidcCls, patch(AUDIT_SVC):
                OidcCls.return_value = _make_oidc_svc()
                # First request — within limit
                client.post('/api/auth/oidc/login')
                # Second request — exceeds limit=1
                r = client.post('/api/auth/oidc/login')

            assert r.status_code == 429
            assert r.json['error'] == 'rate_limit_exceeded'
        finally:
            blueprint_app.settings_manager.get = original_get
            with blueprint_app.app_context():
                cache.delete('ratelimit:oidc_login:127.0.0.1')


# ─── GET /api/auth/oidc/callback ─────────────────────────────────────────────

class TestOidcCallback:

    def _prime_exchange_cache(self, blueprint_app, code, user_id):
        """Put a valid oidc_exchange entry in the cache."""
        with blueprint_app.app_context():
            cache.set(
                f'oidc_exchange:{code}',
                {'user_id': user_id},
                timeout=300,
            )

    def test_valid_callback_redirects_to_frontend(self, blueprint_app):
        client = blueprint_app.test_client()
        with patch(OIDC_SVC) as OidcCls, \
             patch(USER_REPO) as UserRepoCls, \
             patch(AUDIT_SVC):
            OidcCls.return_value = _make_oidc_svc()
            UserRepoCls.return_value = _make_user_repo()
            # auto_create_users=True is default from settings_manager mock
            blueprint_app.settings_manager.get = lambda cat, key, default=None: (
                True if key == 'auto_create_users' else default
            )
            r = client.get('/api/auth/oidc/callback?code=authcode&state=test-state-abc')

        assert r.status_code == 302
        loc = r.headers['Location']
        assert loc.startswith('/oidc-complete')
        assert 'code=' in loc

    def test_idp_error_param_redirects_to_login(self, blueprint_app):
        client = blueprint_app.test_client()
        with patch(AUDIT_SVC):
            r = client.get('/api/auth/oidc/callback?error=access_denied&error_description=Denied')
        assert r.status_code == 302
        assert '/login' in r.headers['Location']
        assert 'oidc_failed' in r.headers['Location']

    def test_missing_code_redirects_to_login(self, blueprint_app):
        client = blueprint_app.test_client()
        with patch(AUDIT_SVC):
            # state present, code absent
            r = client.get('/api/auth/oidc/callback?state=some-state')
        assert r.status_code == 302
        assert 'oidc_invalid_state' in r.headers['Location']

    def test_missing_state_redirects_to_login(self, blueprint_app):
        client = blueprint_app.test_client()
        with patch(AUDIT_SVC):
            r = client.get('/api/auth/oidc/callback?code=authcode')
        assert r.status_code == 302
        assert 'oidc_invalid_state' in r.headers['Location']

    def test_exchange_code_returns_none_redirects(self, blueprint_app):
        client = blueprint_app.test_client()
        with patch(OIDC_SVC) as OidcCls, patch(AUDIT_SVC):
            svc = _make_oidc_svc(exchange_result=None)
            OidcCls.return_value = svc
            r = client.get('/api/auth/oidc/callback?code=authcode&state=s')
        assert r.status_code == 302
        assert 'oidc_invalid_state' in r.headers['Location']

    def test_no_email_claim_redirects(self, blueprint_app):
        client = blueprint_app.test_client()
        no_email_info = OidcUserInfo(
            subject='sub', email='', full_name='Alice', role='user'
        )
        with patch(OIDC_SVC) as OidcCls, patch(AUDIT_SVC):
            OidcCls.return_value = _make_oidc_svc(exchange_result=no_email_info)
            r = client.get('/api/auth/oidc/callback?code=c&state=s')
        assert r.status_code == 302
        assert 'oidc_no_email' in r.headers['Location']

    def test_inactive_user_redirects(self, blueprint_app):
        client = blueprint_app.test_client()
        inactive_user = dict(_USER, is_active=False)
        with patch(OIDC_SVC) as OidcCls, \
             patch(USER_REPO) as UserRepoCls, \
             patch(AUDIT_SVC):
            OidcCls.return_value = _make_oidc_svc()
            repo = _make_user_repo()
            repo.get_or_create_oidc_user.side_effect = ValueError("inactive")
            UserRepoCls.return_value = repo
            blueprint_app.settings_manager.get = lambda cat, key, default=None: (
                True if key == 'auto_create_users' else default
            )
            r = client.get('/api/auth/oidc/callback?code=c&state=s')
        assert r.status_code == 302
        assert 'account_disabled' in r.headers['Location']

    def test_auto_create_false_user_not_found_redirects(self, blueprint_app):
        client = blueprint_app.test_client()
        with patch(OIDC_SVC) as OidcCls, \
             patch(USER_REPO) as UserRepoCls, \
             patch(AUDIT_SVC):
            OidcCls.return_value = _make_oidc_svc()
            repo = _make_user_repo()
            repo.get_user_by_email.return_value = None
            UserRepoCls.return_value = repo
            blueprint_app.settings_manager.get = lambda cat, key, default=None: (
                False if key == 'auto_create_users' else default
            )
            r = client.get('/api/auth/oidc/callback?code=c&state=s')
        assert r.status_code == 302
        assert 'oidc_user_not_provisioned' in r.headers['Location']


# ─── POST /api/auth/oidc/exchange ────────────────────────────────────────────

class TestOidcExchange:

    def _prime_exchange(self, blueprint_app, code='test-exchange-code', user_id='user-uuid-1'):
        with blueprint_app.app_context():
            cache.set(f'oidc_exchange:{code}', {'user_id': user_id}, timeout=300)

    def test_valid_code_returns_login_response(self, blueprint_app):
        self._prime_exchange(blueprint_app)
        client = blueprint_app.test_client()
        with patch(USER_REPO) as UserRepoCls, \
             patch(SESSION_REPO) as SessRepoCls, \
             patch(AUDIT_SVC), \
             patch(TOKEN_SVC) as TokenSvcCls:
            UserRepoCls.return_value = _make_user_repo()
            SessRepoCls.return_value = _make_session_repo()
            TokenSvcCls.generate_token_pair.return_value = _make_token_meta()
            TokenSvcCls.generate_refresh_token.return_value = 'raw-refresh'
            TokenSvcCls.hash_refresh_token.return_value = 'hashed-refresh'
            TokenSvcCls.calculate_token_expiration.return_value = 'some-expiry'

            with blueprint_app.app_context():
                from flask_jwt_extended import create_access_token
                # Ensure the access token creation can work
                pass

            r = client.post(
                '/api/auth/oidc/exchange',
                json={'code': 'test-exchange-code'},
            )
        assert r.status_code == 200
        body = r.json
        assert 'access_token' in body
        assert 'refresh_token' in body
        assert body['user']['email'] == 'alice@example.com'
        assert body['user']['is_oidc_user'] is True

        # Verify cache entry was consumed (single-use)
        with blueprint_app.app_context():
            assert cache.get('oidc_exchange:test-exchange-code') is None

    def test_is_oidc_user_true_without_db_lookup(
        self, blueprint_app
    ):
        """is_oidc_user is now derived from auth_method='oidc' in the JWT claim —
        no get_user_by_username call needed, even when get_user_by_id omits
        password_hash."""
        self._prime_exchange(blueprint_app, code='code-no-hash')
        client = blueprint_app.test_client()

        # get_user_by_id returns user WITHOUT password_hash (production behavior)
        user_without_hash = {
            k: v for k, v in _USER.items() if k != 'password_hash'
        }

        with patch(USER_REPO) as UserRepoCls, \
             patch(SESSION_REPO) as SessRepoCls, \
             patch(AUDIT_SVC), \
             patch(TOKEN_SVC) as TokenSvcCls:
            repo = _make_user_repo(user=user_without_hash)
            UserRepoCls.return_value = repo
            SessRepoCls.return_value = _make_session_repo()
            TokenSvcCls.generate_token_pair.return_value = _make_token_meta()
            TokenSvcCls.generate_refresh_token.return_value = 'raw-refresh'
            TokenSvcCls.hash_refresh_token.return_value = 'hashed-refresh'
            TokenSvcCls.calculate_token_expiration.return_value = 'some-expiry'

            r = client.post(
                '/api/auth/oidc/exchange', json={'code': 'code-no-hash'}
            )

        assert r.status_code == 200
        assert r.json['user']['is_oidc_user'] is True
        # Confirm no extra get_user_by_username call was made
        repo.get_user_by_username.assert_not_called()

    def test_unknown_code_returns_401(self, blueprint_app):
        client = blueprint_app.test_client()
        with patch(AUDIT_SVC):
            r = client.post(
                '/api/auth/oidc/exchange',
                json={'code': 'nonexistent-code'},
            )
        assert r.status_code == 401
        assert r.json['error'] == 'invalid_or_expired_code'

    def test_ip_rate_limit_returns_429(self, blueprint_app):
        # Override settings so the limit is 1, then hit the endpoint twice.
        original_get = blueprint_app.settings_manager.get

        def _strict_get(cat, key, default=None):
            if key == 'token_verify_rate_limit_per_ip_per_hour':
                return 1
            return default

        blueprint_app.settings_manager.get = _strict_get

        try:
            client = blueprint_app.test_client()

            # First request — within limit
            with patch(AUDIT_SVC):
                client.post('/api/auth/oidc/exchange', json={'code': 'x'})

            # Second request — should be rate-limited
            with patch(AUDIT_SVC):
                r = client.post('/api/auth/oidc/exchange', json={'code': 'x'})

            assert r.status_code == 429
        finally:
            blueprint_app.settings_manager.get = original_get


# ─── GET /api/auth/oidc/test-discovery ───────────────────────────────────────

class TestOidcTestDiscovery:

    def test_success_returns_issuer_and_endpoints(self, blueprint_app, auth_headers):
        client = blueprint_app.test_client()
        with patch(OIDC_SVC) as OidcCls:
            svc = MagicMock()
            svc.is_enabled.return_value = True
            svc.test_discovery.return_value = {
                'success': True,
                'issuer': 'https://idp.example.com',
                'authorization_endpoint': 'https://idp.example.com/authorize',
                'token_endpoint': 'https://idp.example.com/token',
                'jwks_uri': 'https://idp.example.com/.well-known/jwks.json',
                'errors': [],
            }
            OidcCls.return_value = svc
            r = client.get('/api/auth/oidc/test-discovery', headers=auth_headers(role='admin'))
        assert r.status_code == 200
        assert r.json['success'] is True
        assert r.json['issuer'] == 'https://idp.example.com'
        assert r.json['authorization_endpoint'] == 'https://idp.example.com/authorize'
        assert r.json['token_endpoint'] == 'https://idp.example.com/token'
        assert r.json['jwks_uri'] == 'https://idp.example.com/.well-known/jwks.json'

    def test_failure_returns_success_false_with_errors(self, blueprint_app, auth_headers):
        client = blueprint_app.test_client()
        with patch(OIDC_SVC) as OidcCls:
            svc = MagicMock()
            svc.is_enabled.return_value = True
            svc.test_discovery.return_value = {
                'success': False,
                'issuer': None,
                'authorization_endpoint': None,
                'token_endpoint': None,
                'jwks_uri': None,
                'errors': ['Discovery URL unreachable'],
            }
            OidcCls.return_value = svc
            r = client.get('/api/auth/oidc/test-discovery', headers=auth_headers(role='admin'))
        assert r.status_code == 200
        assert r.json['success'] is False
        assert 'Discovery URL unreachable' in r.json['errors']

    def test_requires_admin(self, blueprint_app, auth_headers, _prime_regular_user):
        client = blueprint_app.test_client()
        r = client.get('/api/auth/oidc/test-discovery', headers=auth_headers(role='user'))
        assert r.status_code == 403


# ─── GET /api/auth/sso-providers ─────────────────────────────────────────────

class TestSsoProviders:

    def test_returns_enabled_true_when_oidc_configured(self, blueprint_app):
        """oidc.enabled=True and provider_name forwarded when fully configured."""
        client = blueprint_app.test_client()
        with patch(OIDC_SVC) as OidcCls:
            svc = MagicMock()
            svc.is_enabled.return_value = True
            svc.provider_name.return_value = 'Entra ID'
            OidcCls.return_value = svc
            r = client.get('/api/auth/sso-providers')
        assert r.status_code == 200
        assert r.json['oidc']['enabled'] is True
        assert r.json['oidc']['provider_name'] == 'Entra ID'

    def test_returns_enabled_false_when_oidc_disabled(self, blueprint_app):
        """oidc.enabled=False when OidcService.is_enabled() returns False."""
        client = blueprint_app.test_client()
        with patch(OIDC_SVC) as OidcCls:
            svc = MagicMock()
            svc.is_enabled.return_value = False
            svc.provider_name.return_value = 'Single sign-on'
            OidcCls.return_value = svc
            r = client.get('/api/auth/sso-providers')
        assert r.status_code == 200
        assert r.json['oidc']['enabled'] is False


# =============================================================================
# JWT auth_method claim for OIDC exchange — Task 1 (PR 7)
# =============================================================================

class TestOidcExchangeAuthMethodClaim:
    """Verify that oidc_exchange embeds auth_method='oidc' in the access token."""

    def _prime_exchange(self, blueprint_app, code='oidc-code-1'):
        with blueprint_app.app_context():
            cache.set(f'oidc_exchange:{code}', {'user_id': _USER['id']}, timeout=120)

    def test_oidc_exchange_jwt_has_auth_method_oidc(self, blueprint_app):
        """oidc_exchange embeds auth_method='oidc' so downstream reads from JWT."""
        from flask_jwt_extended import decode_token

        self._prime_exchange(blueprint_app)
        client = blueprint_app.test_client()

        with patch(USER_REPO) as UserRepoCls, \
             patch(SESSION_REPO) as SessRepoCls, \
             patch(AUDIT_SVC), \
             patch(TOKEN_SVC) as TokenSvcCls:
            UserRepoCls.return_value = _make_user_repo()
            SessRepoCls.return_value = _make_session_repo()
            TokenSvcCls.generate_token_pair.return_value = _make_token_meta()
            TokenSvcCls.generate_refresh_token.return_value = 'raw-refresh'
            TokenSvcCls.hash_refresh_token.return_value = 'hashed-refresh'
            TokenSvcCls.calculate_token_expiration.return_value = 'some-expiry'

            r = client.post(
                '/api/auth/oidc/exchange',
                json={'code': 'oidc-code-1'},
            )

        assert r.status_code == 200
        with blueprint_app.app_context():
            claims = decode_token(r.json['access_token'])
        assert claims.get('auth_method') == 'oidc'
        assert r.json['user']['is_oidc_user'] is True
        assert r.json['user']['is_ldap_user'] is False
