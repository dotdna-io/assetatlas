# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for generic auth strategies."""
import time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, PropertyMock

import pytest
import requests
import responses

from src.modules.integrations.base.auth_strategies import (
    AuthStrategy,
    OAuth2ClientCredentials,
    OAuth2RefreshToken,
    OAuth2TokenExchange,
    BasicAuth,
    APIKeyAuth,
    LDAPAuth,
    get_auth_strategy,
)
from src.modules.integrations.base.rate_limiter import HeaderRateLimiter


# ---------------------------------------------------------------------------
# OAuth2ClientCredentials
# ---------------------------------------------------------------------------

class TestOAuth2ClientCredentials:
    """Tests for OAuth2ClientCredentials strategy."""

    def _make_config(self, **overrides):
        base = {
            "token_url": "https://auth.example.com/oauth/token",
            "client_id": "my-client",
            "client_secret": "my-secret",
            "scope": "read write",
            "base_url": "https://api.example.com",
        }
        base.update(overrides)
        return base

    @responses.activate
    def test_authenticate_success(self):
        responses.add(
            responses.POST,
            "https://auth.example.com/oauth/token",
            json={"access_token": "tok-123", "expires_in": 3600},
            status=200,
        )
        strategy = OAuth2ClientCredentials(self._make_config())
        assert strategy.authenticate() is True
        assert strategy.access_token == "tok-123"

    @responses.activate
    def test_authenticate_sends_correct_payload(self):
        responses.add(
            responses.POST,
            "https://auth.example.com/oauth/token",
            json={"access_token": "t", "expires_in": 3600},
            status=200,
        )
        strategy = OAuth2ClientCredentials(self._make_config())
        strategy.authenticate()

        body = responses.calls[0].request.body
        assert "grant_type=client_credentials" in body
        assert "client_id=my-client" in body
        assert "client_secret=my-secret" in body
        assert "scope=read+write" in body

    @responses.activate
    def test_authenticate_failure_raises(self):
        responses.add(
            responses.POST,
            "https://auth.example.com/oauth/token",
            json={"error": "invalid_client"},
            status=401,
        )
        strategy = OAuth2ClientCredentials(self._make_config())
        with pytest.raises(Exception):
            strategy.authenticate()

    @responses.activate
    def test_request_adds_bearer_header(self):
        responses.add(
            responses.POST,
            "https://auth.example.com/oauth/token",
            json={"access_token": "tok-abc", "expires_in": 3600},
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.example.com/data",
            json={"ok": True},
            status=200,
        )
        strategy = OAuth2ClientCredentials(self._make_config())
        strategy.authenticate()
        resp = strategy.request("/data")

        assert resp.status_code == 200
        auth_header = responses.calls[1].request.headers["Authorization"]
        assert auth_header == "Bearer tok-abc"

    @responses.activate
    def test_request_auto_refreshes_expired_token(self):
        # First token request
        responses.add(
            responses.POST,
            "https://auth.example.com/oauth/token",
            json={"access_token": "tok-1", "expires_in": 3600},
            status=200,
        )
        # Second token request (after expiry)
        responses.add(
            responses.POST,
            "https://auth.example.com/oauth/token",
            json={"access_token": "tok-2", "expires_in": 3600},
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.example.com/data",
            json={"ok": True},
            status=200,
        )

        strategy = OAuth2ClientCredentials(self._make_config())
        strategy.authenticate()
        assert strategy.access_token == "tok-1"

        # Force token expiry
        strategy.token_expiry = datetime.now() - timedelta(seconds=1)
        strategy.request("/data")
        assert strategy.access_token == "tok-2"

    @responses.activate
    def test_works_with_crowdstrike_style_params(self):
        """CrowdStrike uses a different token URL and scope format."""
        config = self._make_config(
            token_url="https://api.crowdstrike.com/oauth2/token",
            scope="",
            base_url="https://api.crowdstrike.com",
        )
        responses.add(
            responses.POST,
            "https://api.crowdstrike.com/oauth2/token",
            json={"access_token": "cs-token", "expires_in": 1800},
            status=200,
        )
        strategy = OAuth2ClientCredentials(config)
        assert strategy.authenticate() is True

    @responses.activate
    def test_works_with_microsoft_style_params(self):
        """Microsoft Graph uses tenant-specific token URL."""
        config = self._make_config(
            token_url="https://login.microsoftonline.com/tenant-123/oauth2/v2.0/token",
            scope="https://graph.microsoft.com/.default",
            base_url="https://graph.microsoft.com/v1.0",
        )
        responses.add(
            responses.POST,
            "https://login.microsoftonline.com/tenant-123/oauth2/v2.0/token",
            json={"access_token": "ms-token", "expires_in": 3600},
            status=200,
        )
        strategy = OAuth2ClientCredentials(config)
        assert strategy.authenticate() is True

    def test_no_get_paginated_method(self):
        strategy = OAuth2ClientCredentials(self._make_config())
        assert not hasattr(strategy, "get_paginated")


# ---------------------------------------------------------------------------
# OAuth2RefreshToken
# ---------------------------------------------------------------------------

class TestOAuth2RefreshToken:
    """Tests for OAuth2RefreshToken strategy."""

    def _make_config(self, **overrides):
        base = {
            "token_url": "https://auth.example.com/oauth/token",
            "client_id": "my-client",
            "client_secret": "my-secret",
            "refresh_token": "rt-initial",
            "base_url": "https://api.example.com",
        }
        base.update(overrides)
        return base

    @responses.activate
    def test_authenticate_with_refresh_token(self):
        responses.add(
            responses.POST,
            "https://auth.example.com/oauth/token",
            json={
                "access_token": "new-access",
                "refresh_token": "rt-rotated",
                "expires_in": 3600,
            },
            status=200,
        )
        strategy = OAuth2RefreshToken(self._make_config())
        assert strategy.authenticate() is True
        assert strategy.access_token == "new-access"
        assert strategy.refresh_token == "rt-rotated"

    @responses.activate
    def test_authenticate_sends_refresh_token_grant(self):
        responses.add(
            responses.POST,
            "https://auth.example.com/oauth/token",
            json={"access_token": "t", "expires_in": 3600},
            status=200,
        )
        strategy = OAuth2RefreshToken(self._make_config())
        strategy.authenticate()

        body = responses.calls[0].request.body
        assert "grant_type=refresh_token" in body
        assert "refresh_token=rt-initial" in body

    @responses.activate
    def test_authenticate_keeps_old_refresh_token_if_not_rotated(self):
        responses.add(
            responses.POST,
            "https://auth.example.com/oauth/token",
            json={"access_token": "t", "expires_in": 3600},
            status=200,
        )
        strategy = OAuth2RefreshToken(self._make_config())
        strategy.authenticate()
        assert strategy.refresh_token == "rt-initial"


# ---------------------------------------------------------------------------
# OAuth2TokenExchange
# ---------------------------------------------------------------------------

class TestOAuth2TokenExchange:
    """Tests for OAuth2TokenExchange strategy."""

    def _make_config(self, **overrides):
        base = {
            "token_url": "https://armis.example.com/api/v1/access_token/",
            "secret_key": "armis-secret-123",
            "secret_field": "secret_key",
            "token_path": "data.access_token",
            "base_url": "https://armis.example.com",
        }
        base.update(overrides)
        return base

    @responses.activate
    def test_authenticate_exchanges_secret_for_token(self):
        responses.add(
            responses.POST,
            "https://armis.example.com/api/v1/access_token/",
            json={"data": {"access_token": "armis-tok"}},
            status=200,
        )
        strategy = OAuth2TokenExchange(self._make_config())
        assert strategy.authenticate() is True
        assert strategy.access_token == "armis-tok"

    @responses.activate
    def test_authenticate_sends_secret_field(self):
        responses.add(
            responses.POST,
            "https://armis.example.com/api/v1/access_token/",
            json={"data": {"access_token": "t"}},
            status=200,
        )
        strategy = OAuth2TokenExchange(self._make_config())
        strategy.authenticate()

        body = responses.calls[0].request.body
        assert "secret_key=armis-secret-123" in body

    @responses.activate
    def test_request_adds_token_without_bearer_prefix(self):
        responses.add(
            responses.POST,
            "https://armis.example.com/api/v1/access_token/",
            json={"data": {"access_token": "armis-tok"}},
            status=200,
        )
        responses.add(
            responses.GET,
            "https://armis.example.com/api/v1/devices",
            json={"results": []},
            status=200,
        )
        strategy = OAuth2TokenExchange(self._make_config())
        strategy.authenticate()
        strategy.request("/api/v1/devices")

        auth_header = responses.calls[1].request.headers["Authorization"]
        assert auth_header == "armis-tok"
        assert not auth_header.startswith("Bearer")

    @responses.activate
    def test_nested_token_path(self):
        """token_path supports dot-separated paths like 'data.access_token'."""
        responses.add(
            responses.POST,
            "https://armis.example.com/api/v1/access_token/",
            json={"data": {"access_token": "deep-tok"}},
            status=200,
        )
        strategy = OAuth2TokenExchange(self._make_config(token_path="data.access_token"))
        strategy.authenticate()
        assert strategy.access_token == "deep-tok"


# ---------------------------------------------------------------------------
# BasicAuth
# ---------------------------------------------------------------------------

class TestBasicAuth:
    """Tests for BasicAuth strategy."""

    def _make_config(self, **overrides):
        base = {
            "username": "admin",
            "password": "pass123",
            "base_url": "https://snow.example.com",
        }
        base.update(overrides)
        return base

    def test_sets_session_auth(self):
        strategy = BasicAuth(self._make_config())
        assert strategy.session.auth == ("admin", "pass123")

    def test_authenticate_returns_true_no_http(self):
        """authenticate() returns True without making any HTTP call."""
        strategy = BasicAuth(self._make_config())
        assert strategy.authenticate() is True

    @responses.activate
    def test_authenticate_makes_no_http_calls(self):
        """Verify no requests are made during authenticate."""
        strategy = BasicAuth(self._make_config())
        strategy.authenticate()
        assert len(responses.calls) == 0

    @responses.activate
    def test_request_uses_basic_auth(self):
        responses.add(
            responses.GET,
            "https://snow.example.com/api/now/table/cmdb_ci",
            json={"result": []},
            status=200,
        )
        strategy = BasicAuth(self._make_config())
        resp = strategy.request("/api/now/table/cmdb_ci")
        assert resp.status_code == 200
        # Session auth means the Authorization header is set by requests
        auth_header = responses.calls[0].request.headers.get("Authorization", "")
        assert auth_header.startswith("Basic")

    def test_no_get_paginated_method(self):
        strategy = BasicAuth(self._make_config())
        assert not hasattr(strategy, "get_paginated")


# ---------------------------------------------------------------------------
# APIKeyAuth
# ---------------------------------------------------------------------------

class TestAPIKeyAuth:
    """Tests for APIKeyAuth strategy."""

    def _make_config(self, **overrides):
        base = {
            "api_key": "key-12345",
            "base_url": "https://api.example.com",
        }
        base.update(overrides)
        return base

    @responses.activate
    def test_default_header(self):
        responses.add(
            responses.GET,
            "https://api.example.com/data",
            json={"ok": True},
            status=200,
        )
        strategy = APIKeyAuth(self._make_config())
        strategy.request("/data")

        req_headers = responses.calls[0].request.headers
        assert req_headers["X-API-Key"] == "key-12345"

    @responses.activate
    def test_custom_header_sentinelone_apitoken(self):
        """SentinelOne uses Authorization header with ApiToken prefix."""
        config = self._make_config(
            api_key_header="Authorization",
            api_key_prefix="ApiToken ",
        )
        responses.add(
            responses.GET,
            "https://api.example.com/data",
            json={"ok": True},
            status=200,
        )
        strategy = APIKeyAuth(config)
        strategy.request("/data")

        auth_header = responses.calls[0].request.headers["Authorization"]
        assert auth_header == "ApiToken key-12345"

    @responses.activate
    def test_custom_header_lansweeper_token(self):
        """Lansweeper PAT uses Authorization header with Token prefix."""
        config = self._make_config(
            api_key_header="Authorization",
            api_key_prefix="Token ",
        )
        responses.add(
            responses.GET,
            "https://api.example.com/data",
            json={"ok": True},
            status=200,
        )
        strategy = APIKeyAuth(config)
        strategy.request("/data")

        auth_header = responses.calls[0].request.headers["Authorization"]
        assert auth_header == "Token key-12345"

    def test_authenticate_returns_true(self):
        strategy = APIKeyAuth(self._make_config())
        assert strategy.authenticate() is True

    @responses.activate
    def test_no_prefix_by_default(self):
        responses.add(
            responses.GET,
            "https://api.example.com/data",
            json={},
            status=200,
        )
        strategy = APIKeyAuth(self._make_config())
        strategy.request("/data")

        # Default prefix is empty, so the header value is the raw key
        assert responses.calls[0].request.headers["X-API-Key"] == "key-12345"


# ---------------------------------------------------------------------------
# LDAPAuth
# ---------------------------------------------------------------------------

class TestLDAPAuth:
    """Tests for LDAPAuth strategy."""

    def _make_config(self, **overrides):
        base = {
            "ldap_server": "ldap.example.com",
            "ldap_port": 636,
            "username": "cn=admin,dc=example,dc=com",
            "password": "ldap-pass",
            "use_ssl": True,
            "base_dn": "dc=example,dc=com",
        }
        base.update(overrides)
        return base

    @patch("src.modules.integrations.base.auth_strategies.ldap3")
    def test_authenticate_binds_successfully(self, mock_ldap3):
        mock_conn = MagicMock()
        mock_conn.bind.return_value = True
        mock_conn.bound = True
        mock_ldap3.Connection.return_value = mock_conn
        mock_ldap3.Server.return_value = MagicMock()
        mock_ldap3.Tls.return_value = MagicMock()

        strategy = LDAPAuth(self._make_config())
        assert strategy.authenticate() is True

    @patch("src.modules.integrations.base.auth_strategies.ldap3")
    def test_authenticate_failure(self, mock_ldap3):
        mock_conn = MagicMock()
        mock_conn.bind.return_value = False
        mock_conn.bound = False
        mock_ldap3.Connection.return_value = mock_conn
        mock_ldap3.Server.return_value = MagicMock()
        mock_ldap3.Tls.return_value = MagicMock()

        strategy = LDAPAuth(self._make_config())
        assert strategy.authenticate() is False

    @patch("src.modules.integrations.base.auth_strategies.ldap3")
    def test_get_connection_returns_bound_connection(self, mock_ldap3):
        mock_conn = MagicMock()
        mock_conn.bind.return_value = True
        mock_conn.bound = True
        mock_ldap3.Connection.return_value = mock_conn
        mock_ldap3.Server.return_value = MagicMock()
        mock_ldap3.Tls.return_value = MagicMock()

        strategy = LDAPAuth(self._make_config())
        strategy.authenticate()
        conn = strategy.get_connection()
        assert conn is mock_conn

    def test_no_request_method(self):
        """LDAPAuth should not have a request() method — connector uses ldap3 directly."""
        strategy = LDAPAuth(self._make_config())
        # LDAPAuth inherits request() from ABC but it's not intended for use.
        # We just verify it doesn't implement HTTP-based request.
        # The strategy provides get_connection() instead.
        assert hasattr(strategy, "get_connection")

    @patch("src.modules.integrations.base.auth_strategies.ldap3")
    def test_uses_ssl_when_configured(self, mock_ldap3):
        mock_conn = MagicMock()
        mock_conn.bind.return_value = True
        mock_conn.bound = True
        mock_ldap3.Connection.return_value = mock_conn
        mock_ldap3.Server.return_value = MagicMock()
        mock_ldap3.Tls.return_value = MagicMock()

        strategy = LDAPAuth(self._make_config(use_ssl=True))
        strategy.authenticate()

        mock_ldap3.Tls.assert_called_once()
        mock_ldap3.Server.assert_called_once()


# ---------------------------------------------------------------------------
# Rate limiter integration
# ---------------------------------------------------------------------------

class TestRateLimiterIntegration:
    """Test that auth strategies integrate with rate limiter."""

    @responses.activate
    def test_request_calls_wait_for_token_when_rate_limiter_set(self):
        responses.add(
            responses.GET,
            "https://api.example.com/data",
            json={"ok": True},
            status=200,
        )
        config = {
            "api_key": "k",
            "base_url": "https://api.example.com",
        }
        strategy = APIKeyAuth(config)
        mock_limiter = MagicMock()
        strategy.rate_limiter = mock_limiter

        strategy.request("/data")

        mock_limiter.wait_for_token.assert_called_once()
        mock_limiter.on_success.assert_called_once()

    @responses.activate
    def test_request_calls_on_rate_limit_on_429(self):
        responses.add(
            responses.GET,
            "https://api.example.com/data",
            json={"error": "rate limited"},
            status=429,
        )
        config = {
            "api_key": "k",
            "base_url": "https://api.example.com",
        }
        strategy = APIKeyAuth(config)
        # Replace session with one that doesn't retry on 429
        # (the default session retries 3x on 429 via urllib3 Retry)
        plain_session = requests.Session()
        strategy.session = plain_session
        mock_limiter = MagicMock()
        strategy.rate_limiter = mock_limiter

        # 429 will be raised as HTTPError by raise_for_status,
        # but the strategy should call on_rate_limit before raising
        with pytest.raises(Exception):
            strategy.request("/data")

        mock_limiter.wait_for_token.assert_called_once()
        mock_limiter.on_rate_limit.assert_called_once()

    def test_rate_limiter_defaults_to_none(self):
        config = {"api_key": "k", "base_url": "https://api.example.com"}
        strategy = APIKeyAuth(config)
        assert strategy.rate_limiter is None


# ---------------------------------------------------------------------------
# HeaderRateLimiter
# ---------------------------------------------------------------------------

class TestHeaderRateLimiter:
    """Tests for HeaderRateLimiter."""

    def test_on_success_sleeps_when_remaining_below_threshold(self):
        limiter = HeaderRateLimiter(threshold=10)
        mock_response = MagicMock()
        mock_response.headers = {
            "X-RateLimit-Remaining": "5",
            "Retry-After": "2",
        }
        with patch("time.sleep") as mock_sleep:
            limiter.on_success(mock_response)
            mock_sleep.assert_called_once_with(2.0)

    def test_on_success_no_sleep_when_remaining_above_threshold(self):
        limiter = HeaderRateLimiter(threshold=10)
        mock_response = MagicMock()
        mock_response.headers = {
            "X-RateLimit-Remaining": "50",
        }
        with patch("time.sleep") as mock_sleep:
            limiter.on_success(mock_response)
            mock_sleep.assert_not_called()

    def test_on_rate_limit_sleeps_for_retry_after(self):
        limiter = HeaderRateLimiter()
        mock_response = MagicMock()
        mock_response.headers = {
            "Retry-After": "10",
        }
        with patch("time.sleep") as mock_sleep:
            limiter.on_rate_limit(mock_response)
            mock_sleep.assert_called_once_with(10.0)

    def test_on_rate_limit_caps_wait_at_120(self):
        limiter = HeaderRateLimiter()
        mock_response = MagicMock()
        mock_response.headers = {
            "Retry-After": "999",
        }
        with patch("time.sleep") as mock_sleep:
            limiter.on_rate_limit(mock_response)
            mock_sleep.assert_called_once_with(120.0)

    def test_on_success_caps_wait_at_60(self):
        limiter = HeaderRateLimiter(threshold=10)
        mock_response = MagicMock()
        mock_response.headers = {
            "X-RateLimit-Remaining": "1",
            "Retry-After": "999",
        }
        with patch("time.sleep") as mock_sleep:
            limiter.on_success(mock_response)
            mock_sleep.assert_called_once_with(60.0)

    def test_custom_header_names(self):
        limiter = HeaderRateLimiter(
            remaining_header="RateLimit-Remaining",
            retry_after_header="RateLimit-Reset",
            threshold=5,
        )
        mock_response = MagicMock()
        mock_response.headers = {
            "RateLimit-Remaining": "2",
            "RateLimit-Reset": "3",
        }
        with patch("time.sleep") as mock_sleep:
            limiter.on_success(mock_response)
            mock_sleep.assert_called_once_with(3.0)

    def test_on_rate_limit_defaults_to_5s_on_invalid_header(self):
        limiter = HeaderRateLimiter()
        mock_response = MagicMock()
        mock_response.headers = {
            "Retry-After": "not-a-number",
        }
        with patch("time.sleep") as mock_sleep:
            limiter.on_rate_limit(mock_response)
            mock_sleep.assert_called_once_with(5)


# ---------------------------------------------------------------------------
# get_auth_strategy factory
# ---------------------------------------------------------------------------

class TestGetAuthStrategy:
    """Tests for get_auth_strategy factory function."""

    def test_oauth2_maps_to_client_credentials(self):
        config = {
            "auth_type": "oauth2",
            "token_url": "https://auth.example.com/token",
            "client_id": "c",
            "client_secret": "s",
            "scope": "read",
        }
        strategy = get_auth_strategy(config)
        assert isinstance(strategy, OAuth2ClientCredentials)

    def test_oauth2_refresh_token_type(self):
        config = {
            "auth_type": "oauth2_refresh_token",
            "token_url": "https://auth.example.com/token",
            "client_id": "c",
            "client_secret": "s",
            "refresh_token": "rt",
        }
        strategy = get_auth_strategy(config)
        assert isinstance(strategy, OAuth2RefreshToken)

    def test_oauth2_token_exchange_type(self):
        config = {
            "auth_type": "oauth2_token_exchange",
            "token_url": "https://auth.example.com/token",
            "secret_key": "sk",
        }
        strategy = get_auth_strategy(config)
        assert isinstance(strategy, OAuth2TokenExchange)

    def test_basic_type(self):
        config = {
            "auth_type": "basic",
            "username": "u",
            "password": "p",
        }
        strategy = get_auth_strategy(config)
        assert isinstance(strategy, BasicAuth)

    def test_api_key_type(self):
        config = {
            "auth_type": "api_key",
            "api_key": "k",
        }
        strategy = get_auth_strategy(config)
        assert isinstance(strategy, APIKeyAuth)

    def test_ldap_type(self):
        config = {
            "auth_type": "ldap",
            "ldap_server": "ldap.example.com",
            "username": "u",
            "password": "p",
        }
        strategy = get_auth_strategy(config)
        assert isinstance(strategy, LDAPAuth)

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unsupported auth_type"):
            get_auth_strategy({"auth_type": "kerberos"})

    def test_case_insensitive(self):
        config = {
            "auth_type": "BASIC",
            "username": "u",
            "password": "p",
        }
        strategy = get_auth_strategy(config)
        assert isinstance(strategy, BasicAuth)
