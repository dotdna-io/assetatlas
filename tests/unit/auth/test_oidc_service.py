# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for OidcService — uses unittest.mock for authlib + HTTP."""
import socket
from unittest.mock import MagicMock, patch
import pytest
from flask import Flask

from src.auth.services.oidc_service import OidcService


_DEFAULT_DISCOVERY = {
    'issuer': 'https://login.microsoftonline.com/test-tenant/v2.0',
    'authorization_endpoint': 'https://login.microsoftonline.com/test-tenant/oauth2/v2.0/authorize',
    'token_endpoint': 'https://login.microsoftonline.com/test-tenant/oauth2/v2.0/token',
    'jwks_uri': 'https://login.microsoftonline.com/test-tenant/discovery/v2.0/keys',
    'userinfo_endpoint': 'https://graph.microsoft.com/oidc/userinfo',
}


def _settings(**overrides):
    base = {
        'enabled': True,
        'provider_name': 'Microsoft Entra',
        'discovery_url': 'https://login.microsoftonline.com/test-tenant/v2.0/.well-known/openid-configuration',
        'client_id': 'test-client-id',
        'client_secret': 'test-secret',
        'redirect_uri': 'https://app.example.com/api/auth/oidc/callback',
        'frontend_complete_url': '/oidc-complete',
        'scopes': 'openid email profile',
        'claim_mail': 'email',
        'claim_name': 'name',
        'claim_groups': 'groups',
        'group_to_role_mapping': {
            '00000000-0000-0000-0000-000000000001': 'admin',
            '00000000-0000-0000-0000-000000000002': 'super_admin',
        },
        'default_role': 'user',
        'allow_local_fallback': True,
        'auto_create_users': True,
        'http_timeout_seconds': 10,
    }
    base.update(overrides)
    return base


@pytest.fixture
def settings_manager():
    sm = MagicMock()
    cfg = _settings()
    sm.get.side_effect = lambda category, key, default=None: cfg.get(key, default)
    return sm


@pytest.fixture
def app():
    """Minimal Flask app with cache initialised for tests that touch state cache."""
    application = Flask(__name__)
    application.config['CACHE_TYPE'] = 'SimpleCache'
    from src.api.utils.caching import cache
    cache.init_app(application)
    return application


# ----- is_enabled --------------------------------------------------------

class TestIsEnabled:
    def test_returns_true_when_enabled_with_required_fields(self, settings_manager):
        assert OidcService(settings_manager).is_enabled() is True

    def test_returns_false_when_disabled(self):
        sm = MagicMock()
        cfg = _settings(enabled=False)
        sm.get.side_effect = lambda c, k, d=None: cfg.get(k, d)
        assert OidcService(sm).is_enabled() is False

    @pytest.mark.parametrize('missing_field', ['discovery_url', 'client_id', 'client_secret', 'redirect_uri'])
    def test_returns_false_when_required_field_missing(self, missing_field):
        sm = MagicMock()
        cfg = _settings(**{missing_field: ''})
        sm.get.side_effect = lambda c, k, d=None: cfg.get(k, d)
        assert OidcService(sm).is_enabled() is False


# ----- provider_name -----------------------------------------------------

class TestProviderName:
    def _svc(self, **overrides):
        sm = MagicMock()
        cfg = _settings(**overrides)
        sm.get.side_effect = lambda c, k, d=None: cfg.get(k, d)
        return OidcService(sm)

    def test_configured_name_wins(self):
        assert self._svc(provider_name='Contoso Login').provider_name() == 'Contoso Login'

    @pytest.mark.parametrize('stored', ['', 'SSO', 'Single sign-on'])
    def test_generic_default_derives_name_from_detected_provider(self, stored):
        # Entra discovery URL (the _settings default) + no custom name →
        # the login button reads "Sign in with Microsoft Entra".
        assert self._svc(provider_name=stored).provider_name() == 'Microsoft Entra'

    def test_unrecognized_provider_falls_back_to_sso(self):
        svc = self._svc(
            provider_name='SSO',
            discovery_url='https://idp.example.com/.well-known/openid-configuration',
        )
        assert svc.provider_name() == 'SSO'


# ----- build_authorization_url ------------------------------------------

class TestBuildAuthorizationUrl:
    @patch('src.auth.services.oidc_service.OAuth2Session')
    def test_returns_url_and_state(self, MockSession, settings_manager, app):
        with app.app_context():
            with patch.object(OidcService, '_discovery_metadata', return_value=_DEFAULT_DISCOVERY):
                session_inst = MagicMock()
                session_inst.create_authorization_url.return_value = (
                    'https://login.microsoftonline.com/test-tenant/oauth2/v2.0/authorize?client_id=...&state=ST&code_challenge=CC',
                    'ST',
                )
                MockSession.return_value = session_inst

                url, state = OidcService(settings_manager).build_authorization_url()

        assert url.startswith('https://login.microsoftonline.com/')
        assert state == 'ST'


# ----- exchange_code -----------------------------------------------------

class TestExchangeCode:
    @patch('src.auth.services.oidc_service.OAuth2Session')
    @patch.object(OidcService, '_load_jwks', return_value={'keys': []})
    @patch.object(OidcService, '_validate_id_token')
    def test_exchange_returns_user_info(self, MockValidate, MockJwks, MockSession, settings_manager, app):
        """Successful code exchange yields OidcUserInfo with role mapped from groups."""
        with app.app_context():
            from src.api.utils.caching import cache
            cache.set('oidc_state:STATE', {
                'nonce': 'NONCE',
                'code_verifier': 'CVER',
                'redirect_uri': 'https://app.example.com/api/auth/oidc/callback',
            }, timeout=600)

            with patch.object(OidcService, '_discovery_metadata', return_value=_DEFAULT_DISCOVERY):
                session_inst = MagicMock()
                session_inst.fetch_token.return_value = {
                    'id_token': 'fake-id-token',
                    'access_token': 'fake-access',
                }
                MockSession.return_value = session_inst

                MockValidate.return_value = {
                    'sub': 'user-sub-uuid',
                    'email': 'alice@corp.com',
                    'name': 'Alice Smith',
                    'groups': ['00000000-0000-0000-0000-000000000001'],
                    'aud': 'test-client-id',
                    'iss': 'https://login.microsoftonline.com/test-tenant/v2.0',
                    'nonce': 'NONCE',
                }

                info = OidcService(settings_manager).exchange_code('CODE', 'STATE')

        assert info is not None
        assert info.email == 'alice@corp.com'
        assert info.full_name == 'Alice Smith'
        assert info.role == 'admin'
        assert info.groups == ['00000000-0000-0000-0000-000000000001']

    def test_exchange_with_missing_state_returns_none(self, settings_manager):
        # No state seeded in cache
        result = OidcService(settings_manager).exchange_code('CODE', 'UNKNOWN')
        assert result is None

    @patch('src.auth.services.oidc_service.OAuth2Session')
    @patch.object(OidcService, '_validate_id_token')
    def test_exchange_with_nonce_mismatch_returns_none(self, MockValidate, MockSession, settings_manager, app):
        # _validate_id_token raises JoseError on nonce mismatch; exchange_code
        # catches it and returns None. (The duplicate post-validate nonce check
        # was removed — Fix F — so the raise path is the only one tested here.)
        with app.app_context():
            from authlib.jose.errors import JoseError
            from src.api.utils.caching import cache
            cache.set('oidc_state:STATE', {
                'nonce': 'NONCE',
                'code_verifier': 'CVER',
                'redirect_uri': 'https://app.example.com/api/auth/oidc/callback',
            }, timeout=600)

            with patch.object(OidcService, '_discovery_metadata', return_value=_DEFAULT_DISCOVERY):
                session_inst = MagicMock()
                session_inst.fetch_token.return_value = {'id_token': 'fake', 'access_token': 'fake'}
                MockSession.return_value = session_inst
                MockValidate.side_effect = JoseError('Nonce mismatch')

                result = OidcService(settings_manager).exchange_code('CODE', 'STATE')

        assert result is None


# ----- role mapping ------------------------------------------------------

class TestRoleMapping:
    """Highest-rank role wins; default_role validated; case-insensitive matching."""

    @pytest.mark.parametrize('groups,expected_role', [
        (['00000000-0000-0000-0000-000000000001'], 'admin'),
        (['00000000-0000-0000-0000-000000000002'], 'super_admin'),
        (['00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002'], 'super_admin'),
        (['unknown-group'], 'user'),
        ([], 'user'),
    ])
    def test_role_resolution(self, settings_manager, groups, expected_role):
        svc = OidcService(settings_manager)
        assert svc._resolve_role(groups) == expected_role

    def test_invalid_default_role_falls_back_to_user(self):
        sm = MagicMock()
        cfg = _settings(default_role='administrator', group_to_role_mapping={})
        sm.get.side_effect = lambda c, k, d=None: cfg.get(k, d)
        assert OidcService(sm)._resolve_role([]) == 'user'


# ----- test_discovery ----------------------------------------------------

class TestTestDiscovery:
    """Admin diagnostic — fetches the discovery document, returns success/error info."""

    @patch.object(OidcService, '_discovery_metadata')
    def test_returns_metadata_summary_on_success(self, MockMeta, settings_manager):
        MockMeta.return_value = _DEFAULT_DISCOVERY
        result = OidcService(settings_manager).test_discovery()
        assert result['success'] is True
        assert result['issuer'] == _DEFAULT_DISCOVERY['issuer']
        assert result['errors'] == []

    @patch.object(OidcService, '_discovery_metadata')
    def test_returns_failure_when_discovery_raises(self, MockMeta, settings_manager):
        MockMeta.side_effect = Exception('connection refused')
        result = OidcService(settings_manager).test_discovery()
        assert result['success'] is False
        assert any('connection refused' in e for e in result['errors'])


# ----- build_logout_url --------------------------------------------------

class TestBuildLogoutUrl:
    """RP-initiated logout — returns the IdP end_session_endpoint with
    client_id (always) and post_logout_redirect_uri (when configured)
    appended, or None if RP-init is not supported by the IdP."""

    _DISCOVERY_WITH_LOGOUT = {
        **_DEFAULT_DISCOVERY,
        'end_session_endpoint': 'https://login.microsoftonline.com/test-tenant/oauth2/v2.0/logout',
    }

    @patch.object(OidcService, '_discovery_metadata')
    def test_returns_url_with_client_id_and_redirect_when_configured(
        self, MockMeta, settings_manager
    ):
        MockMeta.return_value = self._DISCOVERY_WITH_LOGOUT
        cfg = _settings(post_logout_redirect_uri='https://app.example.com/login')
        settings_manager.get.side_effect = lambda c, k, d=None: cfg.get(k, d)

        url = OidcService(settings_manager).build_logout_url()

        assert url is not None
        assert url.startswith(self._DISCOVERY_WITH_LOGOUT['end_session_endpoint'])
        assert 'client_id=test-client-id' in url
        assert 'post_logout_redirect_uri=https%3A%2F%2Fapp.example.com%2Flogin' in url

    @patch.object(OidcService, '_discovery_metadata')
    def test_returns_url_with_only_client_id_when_redirect_uri_empty(
        self, MockMeta, settings_manager
    ):
        # Regression for "logout doesn't terminate IdP session out of the box":
        # without post_logout_redirect_uri we still issue an end_session call
        # carrying client_id so Entra actually terminates the SSO cookie. The
        # IdP shows its own "signed out" page when no redirect is supplied.
        MockMeta.return_value = self._DISCOVERY_WITH_LOGOUT
        cfg = _settings(post_logout_redirect_uri='')
        settings_manager.get.side_effect = lambda c, k, d=None: cfg.get(k, d)

        url = OidcService(settings_manager).build_logout_url()

        assert url is not None
        assert url.startswith(self._DISCOVERY_WITH_LOGOUT['end_session_endpoint'])
        assert 'client_id=test-client-id' in url
        assert 'post_logout_redirect_uri=' not in url

    @patch.object(OidcService, '_discovery_metadata')
    def test_returns_none_when_client_id_missing(self, MockMeta, settings_manager):
        # Defensive: without client_id the IdP can't validate the request, and
        # most won't honor the logout. Skip RP-init in that case.
        cfg = _settings(client_id='', post_logout_redirect_uri='https://app.example.com/login')
        settings_manager.get.side_effect = lambda c, k, d=None: cfg.get(k, d)

        assert OidcService(settings_manager).build_logout_url() is None
        MockMeta.assert_not_called()

    @patch.object(OidcService, '_discovery_metadata')
    def test_returns_none_when_idp_lacks_end_session_endpoint(self, MockMeta, settings_manager):
        # Some IdPs (or older Entra v1.0 endpoints) don't expose this
        MockMeta.return_value = _DEFAULT_DISCOVERY  # no end_session_endpoint
        cfg = _settings(post_logout_redirect_uri='https://app.example.com/login')
        settings_manager.get.side_effect = lambda c, k, d=None: cfg.get(k, d)

        assert OidcService(settings_manager).build_logout_url() is None

    @patch.object(OidcService, '_discovery_metadata')
    def test_returns_none_when_discovery_raises(self, MockMeta, settings_manager):
        # SSRF rejection, network error, etc. — don't break logout, just skip RP-init.
        MockMeta.side_effect = Exception('boom')
        cfg = _settings(post_logout_redirect_uri='https://app.example.com/login')
        settings_manager.get.side_effect = lambda c, k, d=None: cfg.get(k, d)

        assert OidcService(settings_manager).build_logout_url() is None

    @patch.object(OidcService, '_discovery_metadata')
    def test_url_uses_ampersand_separator_between_params(
        self, MockMeta, settings_manager
    ):
        # Guard against a regression where a comma or whitespace ends up
        # separating client_id and post_logout_redirect_uri (Entra would
        # fail to parse and silently ignore the request).
        MockMeta.return_value = self._DISCOVERY_WITH_LOGOUT
        cfg = _settings(post_logout_redirect_uri='https://app.example.com/login')
        settings_manager.get.side_effect = lambda c, k, d=None: cfg.get(k, d)

        url = OidcService(settings_manager).build_logout_url()

        assert url is not None
        assert '?client_id=test-client-id&post_logout_redirect_uri=' in url


# ----- direct ID token validation ----------------------------------------

class TestValidateIdTokenDirect:
    """Exercise _validate_id_token directly with crafted claims."""

    def _service_with_jwks_mocked(self, settings_manager):
        from unittest.mock import patch
        svc = OidcService(settings_manager)
        patch.object(svc, '_discovery_metadata', return_value=_DEFAULT_DISCOVERY).start()
        patch.object(svc, '_load_jwks', return_value={'keys': []}).start()
        return svc

    def test_aud_as_string_accepts_when_matches(self, settings_manager):
        from unittest.mock import patch
        svc = self._service_with_jwks_mocked(settings_manager)
        claims = {
            'aud': 'test-client-id',
            'iss': _DEFAULT_DISCOVERY['issuer'],
            'exp': int(__import__('time').time()) + 3600,
            'nonce': 'NONCE',
        }
        with patch('src.auth.services.oidc_service._JsonWebToken') as MockJwt:
            MockJwt.return_value.decode.return_value = claims
            result = svc._validate_id_token('fake.jwt.token', expected_nonce='NONCE')
        assert result == claims

    def test_aud_as_list_accepts_when_client_id_in_list(self, settings_manager):
        from unittest.mock import patch
        svc = self._service_with_jwks_mocked(settings_manager)
        claims = {
            'aud': ['other-id', 'test-client-id'],
            'iss': _DEFAULT_DISCOVERY['issuer'],
            'exp': int(__import__('time').time()) + 3600,
            'nonce': 'NONCE',
        }
        with patch('src.auth.services.oidc_service._JsonWebToken') as MockJwt:
            MockJwt.return_value.decode.return_value = claims
            result = svc._validate_id_token('fake.jwt.token', expected_nonce='NONCE')
        assert result == claims

    def test_aud_as_list_rejects_when_client_id_missing(self, settings_manager):
        from unittest.mock import patch
        svc = self._service_with_jwks_mocked(settings_manager)
        claims = {
            'aud': ['other-id', 'another-id'],
            'iss': _DEFAULT_DISCOVERY['issuer'],
            'exp': int(__import__('time').time()) + 3600,
            'nonce': 'NONCE',
        }
        with patch('src.auth.services.oidc_service._JsonWebToken') as MockJwt:
            MockJwt.return_value.decode.return_value = claims
            from authlib.jose.errors import JoseError
            with pytest.raises(JoseError, match='Invalid audience'):
                svc._validate_id_token('fake.jwt.token', expected_nonce='NONCE')

    def test_missing_exp_raises(self, settings_manager):
        from unittest.mock import patch
        svc = self._service_with_jwks_mocked(settings_manager)
        claims = {
            'aud': 'test-client-id',
            'iss': _DEFAULT_DISCOVERY['issuer'],
            # no exp
            'nonce': 'NONCE',
        }
        with patch('src.auth.services.oidc_service._JsonWebToken') as MockJwt:
            MockJwt.return_value.decode.return_value = claims
            from authlib.jose.errors import JoseError
            with pytest.raises(JoseError, match='missing exp'):
                svc._validate_id_token('fake.jwt.token', expected_nonce='NONCE')

    def test_expired_token_raises(self, settings_manager):
        from unittest.mock import patch
        svc = self._service_with_jwks_mocked(settings_manager)
        claims = {
            'aud': 'test-client-id',
            'iss': _DEFAULT_DISCOVERY['issuer'],
            'exp': int(__import__('time').time()) - 60,  # 1 min ago
            'nonce': 'NONCE',
        }
        with patch('src.auth.services.oidc_service._JsonWebToken') as MockJwt:
            MockJwt.return_value.decode.return_value = claims
            from authlib.jose.errors import JoseError
            with pytest.raises(JoseError, match='expired'):
                svc._validate_id_token('fake.jwt.token', expected_nonce='NONCE')

    def test_nbf_in_future_raises(self, settings_manager):
        from unittest.mock import patch
        svc = self._service_with_jwks_mocked(settings_manager)
        claims = {
            'aud': 'test-client-id',
            'iss': _DEFAULT_DISCOVERY['issuer'],
            'exp': int(__import__('time').time()) + 3600,
            'nbf': int(__import__('time').time()) + 60,  # 1 min in future
            'nonce': 'NONCE',
        }
        with patch('src.auth.services.oidc_service._JsonWebToken') as MockJwt:
            MockJwt.return_value.decode.return_value = claims
            from authlib.jose.errors import JoseError
            with pytest.raises(JoseError, match='not yet valid'):
                svc._validate_id_token('fake.jwt.token', expected_nonce='NONCE')

    def test_iss_mismatch_raises(self, settings_manager):
        from unittest.mock import patch
        svc = self._service_with_jwks_mocked(settings_manager)
        claims = {
            'aud': 'test-client-id',
            'iss': 'https://attacker.example.com',
            'exp': int(__import__('time').time()) + 3600,
            'nonce': 'NONCE',
        }
        with patch('src.auth.services.oidc_service._JsonWebToken') as MockJwt:
            MockJwt.return_value.decode.return_value = claims
            from authlib.jose.errors import JoseError
            with pytest.raises(JoseError, match='Invalid issuer'):
                svc._validate_id_token('fake.jwt.token', expected_nonce='NONCE')

    def test_nonce_mismatch_raises(self, settings_manager):
        from unittest.mock import patch
        svc = self._service_with_jwks_mocked(settings_manager)
        claims = {
            'aud': 'test-client-id',
            'iss': _DEFAULT_DISCOVERY['issuer'],
            'exp': int(__import__('time').time()) + 3600,
            'nonce': 'WRONG',
        }
        with patch('src.auth.services.oidc_service._JsonWebToken') as MockJwt:
            MockJwt.return_value.decode.return_value = claims
            from authlib.jose.errors import JoseError
            with pytest.raises(JoseError, match='Nonce mismatch'):
                svc._validate_id_token('fake.jwt.token', expected_nonce='NONCE')


# ----- SSRF guards on discovery_url + jwks_uri ---------------------------

def _gai_for(ip: str):
    """Build a getaddrinfo() return shape for a single IPv4 address."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 0))]


class TestSettingsHookSsrfGuard:
    """OidcSettingsHooks.validate('discovery_url', ...) — fail closed on SSRF."""

    def _hooks(self):
        from src.api.blueprints.settings.category_hooks import OidcSettingsHooks
        return OidcSettingsHooks()

    def test_rejects_http_scheme(self):
        from src.api.blueprints.settings.category_hooks import HookValidationError
        with pytest.raises(HookValidationError, match='https'):
            self._hooks().validate(
                'discovery_url',
                'http://login.microsoftonline.com/.well-known/openid-configuration',
            )

    def test_rejects_file_scheme(self):
        from src.api.blueprints.settings.category_hooks import HookValidationError
        with pytest.raises(HookValidationError, match='https'):
            self._hooks().validate('discovery_url', 'file:///etc/passwd')

    def test_rejects_loopback(self):
        from src.api.blueprints.settings.category_hooks import HookValidationError
        with patch('src.auth.services.oidc_service.socket.getaddrinfo',
                   return_value=_gai_for('127.0.0.1')):
            with pytest.raises(HookValidationError, match='loopback'):
                self._hooks().validate(
                    'discovery_url',
                    'https://localhost/.well-known/openid-configuration',
                )

    def test_rejects_link_local_metadata(self):
        from src.api.blueprints.settings.category_hooks import HookValidationError
        with patch('src.auth.services.oidc_service.socket.getaddrinfo',
                   return_value=_gai_for('169.254.169.254')):
            with pytest.raises(HookValidationError, match='link-local'):
                self._hooks().validate(
                    'discovery_url',
                    'https://169.254.169.254/.well-known/openid-configuration',
                )

    def test_rejects_rfc1918_private(self):
        from src.api.blueprints.settings.category_hooks import HookValidationError
        with patch('src.auth.services.oidc_service.socket.getaddrinfo',
                   return_value=_gai_for('10.1.2.3')):
            with pytest.raises(HookValidationError, match='private'):
                self._hooks().validate(
                    'discovery_url',
                    'https://10.1.2.3/.well-known/openid-configuration',
                )

    def test_fails_closed_on_dns_resolution_failure(self):
        from src.api.blueprints.settings.category_hooks import HookValidationError
        with patch('src.auth.services.oidc_service.socket.getaddrinfo',
                   side_effect=socket.gaierror('Name or service not known')):
            with pytest.raises(HookValidationError, match='could not be resolved'):
                self._hooks().validate(
                    'discovery_url',
                    'https://nonexistent.invalid/.well-known/openid-configuration',
                )

    def test_accepts_public_https_url(self):
        with patch('src.auth.services.oidc_service.socket.getaddrinfo',
                   return_value=_gai_for('20.190.190.1')):
            url = 'https://login.microsoftonline.com/tenant/.well-known/openid-configuration'
            assert self._hooks().validate('discovery_url', url) == url

    def test_empty_value_accepted(self):
        # Allows admins to clear the URL to disable OIDC.
        assert self._hooks().validate('discovery_url', '') == ''


class TestFetchTimeSsrfGuard:
    """_discovery_metadata / _load_jwks must re-validate at fetch time (DNS rebinding)."""

    def _make_response(self, *, status=200, body=b'{}', location=None):
        resp = MagicMock()
        resp.status_code = status
        resp.headers = {'Location': location} if location else {}
        resp.iter_content.return_value = [body]
        resp.raise_for_status = MagicMock()
        resp.close = MagicMock()
        return resp

    def test_discovery_rejected_when_resolves_to_loopback(self, settings_manager, app):
        from src.api.utils.caching import cache as flask_cache
        with app.app_context():
            flask_cache.clear()
            with patch('src.auth.services.oidc_service.socket.getaddrinfo',
                       return_value=_gai_for('127.0.0.1')):
                with patch('src.auth.services.oidc_service._requests.get') as mock_get:
                    with pytest.raises(Exception, match='loopback'):
                        OidcService(settings_manager)._discovery_metadata()
                    mock_get.assert_not_called()

    def test_discovery_response_size_capped(self, settings_manager, app):
        """If IdP returns a payload larger than the cap, we abort instead of OOMing."""
        from src.auth.services.oidc_service import (
            MAX_DISCOVERY_RESPONSE_BYTES,
            OidcSsrfError,
        )
        oversized = b'x' * (MAX_DISCOVERY_RESPONSE_BYTES + 1024)

        from src.api.utils.caching import cache as flask_cache
        with app.app_context():
            flask_cache.clear()
            with patch('src.auth.services.oidc_service.socket.getaddrinfo',
                       return_value=_gai_for('20.190.190.1')):
                resp = MagicMock()
                resp.status_code = 200
                resp.headers = {}
                # Stream chunks small enough to loop a few times before cap trips.
                resp.iter_content.return_value = [b'x' * 8192] * (
                    (MAX_DISCOVERY_RESPONSE_BYTES // 8192) + 2
                )
                resp.raise_for_status = MagicMock()
                resp.close = MagicMock()
                with patch('src.auth.services.oidc_service._requests.get',
                           return_value=resp):
                    with pytest.raises(OidcSsrfError, match='exceeded'):
                        OidcService(settings_manager)._discovery_metadata()

    def test_discovery_redirect_to_private_ip_rejected(self, settings_manager, app):
        """A 302 to a private IP must be rejected on the redirect re-validation."""
        from src.auth.services.oidc_service import OidcSsrfError

        # First hop resolves public, returns 302 to internal host.
        # Second hop resolves to a private IP — must abort.
        gai_calls = {'count': 0}
        def gai(host, *_a, **_k):
            gai_calls['count'] += 1
            if gai_calls['count'] == 1:
                return _gai_for('20.190.190.1')   # public
            return _gai_for('10.0.0.5')           # private

        from src.api.utils.caching import cache as flask_cache
        with app.app_context():
            flask_cache.clear()
            with patch('src.auth.services.oidc_service.socket.getaddrinfo',
                       side_effect=gai):
                redirect_resp = self._make_response(
                    status=302,
                    location='https://internal.corp.local/.well-known/openid-configuration',
                )
                with patch('src.auth.services.oidc_service._requests.get',
                           return_value=redirect_resp):
                    with pytest.raises(OidcSsrfError, match='private'):
                        OidcService(settings_manager)._discovery_metadata()

    def test_jwks_uri_validated_independently(self, settings_manager, app):
        """A discovery doc that returns a private jwks_uri must be rejected."""
        import json as _json
        from src.auth.services.oidc_service import OidcSsrfError

        # discovery_url resolves public; jwks_uri inside the doc points at private host.
        gai_calls = {'count': 0}
        def gai(host, *_a, **_k):
            gai_calls['count'] += 1
            if gai_calls['count'] == 1:
                return _gai_for('20.190.190.1')   # discovery host: public
            return _gai_for('10.0.0.5')           # jwks host: private

        meta_doc = {
            'issuer': 'https://idp.example.com',
            'authorization_endpoint': 'https://idp.example.com/authorize',
            'token_endpoint': 'https://idp.example.com/token',
            'jwks_uri': 'https://internal.corp.local/keys',
        }
        from src.api.utils.caching import cache as flask_cache
        with app.app_context():
            flask_cache.clear()
            with patch('src.auth.services.oidc_service.socket.getaddrinfo',
                       side_effect=gai):
                discovery_resp = self._make_response(
                    body=_json.dumps(meta_doc).encode('utf-8'),
                )
                with patch('src.auth.services.oidc_service._requests.get',
                           return_value=discovery_resp):
                    with pytest.raises(OidcSsrfError, match='private'):
                        OidcService(settings_manager)._load_jwks()


# ----- ID token alg pinning (Task 4.2) -----------------------------------

class TestAllowedIdTokenAlgs:
    """OidcService._allowed_id_token_algs filters IdP-advertised algs (RFC 8725 §3.1)."""

    def test_filters_none_and_hs_from_advertised(self):
        meta = {'id_token_signing_alg_values_supported': ['none', 'HS256', 'RS256', 'ES256']}
        assert OidcService._allowed_id_token_algs(meta) == ['RS256', 'ES256']

    def test_falls_back_when_idp_only_advertises_forbidden(self):
        from src.auth.services.oidc_service import _FALLBACK_ID_TOKEN_ALGS
        meta = {'id_token_signing_alg_values_supported': ['none', 'HS256', 'HS384']}
        assert OidcService._allowed_id_token_algs(meta) == list(_FALLBACK_ID_TOKEN_ALGS)

    def test_falls_back_when_idp_advertises_nothing(self):
        from src.auth.services.oidc_service import _FALLBACK_ID_TOKEN_ALGS
        meta = {}
        assert OidcService._allowed_id_token_algs(meta) == list(_FALLBACK_ID_TOKEN_ALGS)

    def test_keeps_safe_advertised_set(self):
        meta = {'id_token_signing_alg_values_supported': ['RS256', 'PS256', 'ES512']}
        assert OidcService._allowed_id_token_algs(meta) == ['RS256', 'PS256', 'ES512']


class TestIdTokenAlgEnforcement:
    """JsonWebToken used by _validate_id_token must reject 'none' and HS* algs.

    These are integration-ish tests: we forge a real JWT (none/HS256) and let
    the real authlib JWS layer reject it. Avoids over-mocking and exercises
    the actual security control.
    """

    def _settings_with_alg_advertised(self, advertised):
        sm = MagicMock()
        cfg = dict(_settings(), )
        sm.get.side_effect = lambda c, k, d=None: cfg.get(k, d)
        return sm, dict(_DEFAULT_DISCOVERY,
                        id_token_signing_alg_values_supported=advertised)

    def test_passes_allowed_algs_to_decoder(self, settings_manager):
        """_JsonWebToken constructor receives the filtered allowed-algs list."""
        meta = dict(_DEFAULT_DISCOVERY,
                    id_token_signing_alg_values_supported=['none', 'HS256', 'RS256'])
        svc = OidcService(settings_manager)
        with patch.object(svc, '_discovery_metadata', return_value=meta):
            with patch.object(svc, '_load_jwks', return_value={'keys': []}):
                with patch('src.auth.services.oidc_service._JsonWebToken') as MockCtor:
                    MockCtor.return_value.decode.return_value = {
                        'aud': 'test-client-id',
                        'iss': _DEFAULT_DISCOVERY['issuer'],
                        'exp': int(__import__('time').time()) + 3600,
                        'nonce': 'NONCE',
                    }
                    svc._validate_id_token('fake.jwt.token', expected_nonce='NONCE')
                    MockCtor.assert_called_once_with(['RS256'])

    def test_real_authlib_rejects_alg_none_token(self, settings_manager, app):
        """A token with alg=none gets rejected by the constructed JsonWebToken."""
        import base64
        import json as _json
        from authlib.jose.errors import JoseError

        # Build alg=none JWT manually: header.payload.<empty signature>
        header = base64.urlsafe_b64encode(_json.dumps({'alg': 'none', 'typ': 'JWT'}).encode()).rstrip(b'=')
        payload = base64.urlsafe_b64encode(_json.dumps({
            'aud': 'test-client-id',
            'iss': _DEFAULT_DISCOVERY['issuer'],
            'exp': int(__import__('time').time()) + 3600,
            'nonce': 'NONCE',
        }).encode()).rstrip(b'=')
        token = f"{header.decode()}.{payload.decode()}."

        meta = dict(_DEFAULT_DISCOVERY,
                    id_token_signing_alg_values_supported=['RS256'])
        svc = OidcService(settings_manager)
        with patch.object(svc, '_discovery_metadata', return_value=meta):
            with patch.object(svc, '_load_jwks', return_value={'keys': []}):
                with pytest.raises(JoseError):
                    svc._validate_id_token(token, expected_nonce='NONCE')

    def test_real_authlib_rejects_alg_hs256_token(self, settings_manager):
        """A forged HS256 token must be rejected even if the secret is the JWKS pubkey."""
        import base64
        import hmac
        import hashlib
        import json as _json
        from authlib.jose.errors import JoseError

        secret = b'attacker-knows-public-key-fingerprint'
        header_b = base64.urlsafe_b64encode(
            _json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode()
        ).rstrip(b'=')
        payload_b = base64.urlsafe_b64encode(_json.dumps({
            'aud': 'test-client-id',
            'iss': _DEFAULT_DISCOVERY['issuer'],
            'exp': int(__import__('time').time()) + 3600,
            'nonce': 'NONCE',
        }).encode()).rstrip(b'=')
        signing_input = header_b + b'.' + payload_b
        sig = hmac.new(secret, signing_input, hashlib.sha256).digest()
        sig_b = base64.urlsafe_b64encode(sig).rstrip(b'=')
        token = f"{header_b.decode()}.{payload_b.decode()}.{sig_b.decode()}"

        meta = dict(_DEFAULT_DISCOVERY,
                    id_token_signing_alg_values_supported=['RS256'])
        svc = OidcService(settings_manager)
        with patch.object(svc, '_discovery_metadata', return_value=meta):
            with patch.object(svc, '_load_jwks', return_value={'keys': []}):
                with pytest.raises(JoseError):
                    svc._validate_id_token(token, expected_nonce='NONCE')
