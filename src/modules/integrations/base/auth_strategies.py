# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Authentication Strategies

Generic, vendor-agnostic authentication strategies for connectors:
- OAuth2ClientCredentials — any client_credentials flow
- OAuth2RefreshToken — authorization_code / refresh_token flow
- OAuth2TokenExchange — exchange secret for short-lived token
- LoginPasswordJsonAuth — POST {identity, password} JSON, extract token via JSONPath
- BasicAuth — username/password
- APIKeyAuth — header-based key with configurable header + prefix
- LDAPAuth — ldap3-based bind for Active Directory

Created: 2025-11-04
Updated: 2026-03-19
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import contextlib
import warnings
import requests
import logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    import ldap3
except ImportError:
    ldap3 = None  # Optional — only needed by LDAPAuth

try:
    import urllib3
    _INSECURE_REQUEST_WARNING = urllib3.exceptions.InsecureRequestWarning
except Exception:  # pragma: no cover — urllib3 always available with requests
    _INSECURE_REQUEST_WARNING = None


@contextlib.contextmanager
def _silence_insecure_warning_if(off: bool):
    """Suppress urllib3's InsecureRequestWarning *only* for the wrapped block,
    and only when ``off`` is True. Per-call scope so we don't disable warnings
    process-wide for code paths that didn't ask for verify=False."""
    if not off or _INSECURE_REQUEST_WARNING is None:
        yield
        return
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", _INSECURE_REQUEST_WARNING)
        yield


class AuthStrategy(ABC):
    """Base class for authentication strategies."""

    def __init__(self, config: Dict[str, Any], logger: Optional[logging.Logger] = None):
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        self.session = self._create_session()
        self.rate_limiter = None
        # Honor an opt-out for TLS verification. Default True. When False, the
        # InsecureRequestWarning is suppressed only for THIS strategy's
        # request calls (see _do_request) — never process-wide, so other code
        # paths still see warnings on accidental verify=False.
        self._verify_ssl_off = config.get('verify_ssl') is False
        if self._verify_ssl_off and self.session is not None:
            self.session.verify = False
            self.logger.warning(
                "TLS certificate verification disabled for this connector"
            )

    @abstractmethod
    def authenticate(self) -> bool:
        """Perform authentication. Returns True on success."""
        pass

    @abstractmethod
    def request(self, url: str, method: str = 'GET', **kwargs) -> requests.Response:
        """Make authenticated HTTP request."""
        pass

    def _resolve_url(self, url: str) -> str:
        """Make URL absolute if relative."""
        if not url.startswith('http'):
            return f"{self.base_url}{url}"
        return url

    def _create_session(self) -> requests.Session:
        """Create session with retry logic."""
        session = requests.Session()

        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
        )

        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=20,
        )

        session.mount("http://", adapter)
        session.mount("https://", adapter)

        session.request = self._add_timeout(session.request)
        return session

    @staticmethod
    def _add_timeout(func):
        """Add default timeout to requests."""
        def wrapper(*args, **kwargs):
            if 'timeout' not in kwargs:
                kwargs['timeout'] = 30
            return func(*args, **kwargs)
        return wrapper

    def _do_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Execute request with rate limiter integration.

        Subclasses should call this instead of self.session.request() directly.
        """
        if self.rate_limiter:
            self.rate_limiter.wait_for_token()

        with _silence_insecure_warning_if(self._verify_ssl_off):
            response = self.session.request(method, url, **kwargs)

        if self.rate_limiter:
            if response.status_code == 429:
                self.rate_limiter.on_rate_limit(response)
                response.raise_for_status()
            else:
                self.rate_limiter.on_success(response)

        response.raise_for_status()
        return response

    def close(self):
        """Close session."""
        if self.session:
            self.session.close()


# ---------------------------------------------------------------------------
# OAuth2ClientCredentials
# ---------------------------------------------------------------------------

class OAuth2ClientCredentials(AuthStrategy):
    """OAuth2 Client Credentials flow — any provider.

    Configuration:
        token_url (required): Token endpoint URL
        client_id: Application client ID
        client_secret: Client secret
        scope (required): OAuth2 scope
        base_url: API base URL for relative paths
    """

    def __init__(self, config: Dict[str, Any], logger: Optional[logging.Logger] = None):
        super().__init__(config, logger)

        self.token_url = config.get('token_url')
        self.client_id = config.get('client_id')
        self.client_secret = config.get('client_secret')
        self.scope = config.get('scope')
        self.base_url = config.get('base_url', '').rstrip('/')

        self.access_token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None

    def authenticate(self) -> bool:
        self.logger.info("Authenticating with OAuth2 (Client Credentials)")

        try:
            data = {
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'scope': self.scope,
                'grant_type': 'client_credentials',
            }

            with _silence_insecure_warning_if(self._verify_ssl_off):
                response = self.session.post(self.token_url, data=data, timeout=30)
            response.raise_for_status()

            token_data = response.json()
            self.access_token = token_data['access_token']

            # 5-minute buffer before actual expiry
            expires_in = token_data.get('expires_in', 3600)
            self.token_expiry = datetime.now() + timedelta(seconds=expires_in - 300)

            self.logger.info("OAuth2 authentication successful")
            return True

        except requests.HTTPError as e:
            self.logger.error(f"OAuth2 authentication failed: {e}")
            self.logger.error(f"Response: {e.response.text if e.response else 'No response'}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error during OAuth2 authentication: {e}")
            raise

    def request(self, url: str, method: str = 'GET', **kwargs) -> requests.Response:
        # Auto-refresh token if expired or missing
        if not self.access_token or (self.token_expiry and datetime.now() >= self.token_expiry):
            self.authenticate()

        url = self._resolve_url(url)

        headers = kwargs.get('headers', {})
        headers['Authorization'] = f'Bearer {self.access_token}'
        headers['Content-Type'] = headers.get('Content-Type', 'application/json')
        kwargs['headers'] = headers

        return self._do_request(method, url, **kwargs)


# ---------------------------------------------------------------------------
# OAuth2RefreshToken
# ---------------------------------------------------------------------------

class OAuth2RefreshToken(AuthStrategy):
    """OAuth2 authorization_code / refresh_token flow.

    Configuration:
        token_url (required): Token endpoint URL
        client_id: Application client ID
        client_secret: Client secret
        refresh_token: Refresh token
        base_url: API base URL for relative paths
    """

    def __init__(self, config: Dict[str, Any], logger: Optional[logging.Logger] = None):
        super().__init__(config, logger)

        self.token_url = config.get('token_url')
        self.client_id = config.get('client_id')
        self.client_secret = config.get('client_secret')
        self.refresh_token = config.get('refresh_token')
        self.base_url = config.get('base_url', '').rstrip('/')

        self.access_token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None

    def authenticate(self) -> bool:
        self.logger.info("Authenticating with OAuth2 (Refresh Token)")

        try:
            data = {
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'refresh_token': self.refresh_token,
                'grant_type': 'refresh_token',
            }

            with _silence_insecure_warning_if(self._verify_ssl_off):
                response = self.session.post(self.token_url, data=data, timeout=30)
            response.raise_for_status()

            token_data = response.json()
            self.access_token = token_data['access_token']

            # Update refresh token if rotated
            if 'refresh_token' in token_data:
                self.refresh_token = token_data['refresh_token']

            expires_in = token_data.get('expires_in', 3600)
            self.token_expiry = datetime.now() + timedelta(seconds=expires_in - 300)

            self.logger.info("OAuth2 refresh token authentication successful")
            return True

        except requests.HTTPError as e:
            self.logger.error(f"OAuth2 refresh token authentication failed: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error during OAuth2 refresh auth: {e}")
            raise

    def request(self, url: str, method: str = 'GET', **kwargs) -> requests.Response:
        if not self.access_token or (self.token_expiry and datetime.now() >= self.token_expiry):
            self.authenticate()

        url = self._resolve_url(url)

        headers = kwargs.get('headers', {})
        headers['Authorization'] = f'Bearer {self.access_token}'
        headers['Content-Type'] = headers.get('Content-Type', 'application/json')
        kwargs['headers'] = headers

        return self._do_request(method, url, **kwargs)


# ---------------------------------------------------------------------------
# OAuth2TokenExchange
# ---------------------------------------------------------------------------

class OAuth2TokenExchange(AuthStrategy):
    """Exchange secret for short-lived token (Armis pattern).

    Configuration:
        token_url (required): Token endpoint URL
        secret_key: The secret value to exchange
        secret_field: Form field name for the secret (default: 'secret_key')
        token_path: Dot-separated path to token in response JSON (e.g. 'data.access_token')
        base_url: API base URL for relative paths
    """

    def __init__(self, config: Dict[str, Any], logger: Optional[logging.Logger] = None):
        super().__init__(config, logger)

        self.token_url = config.get('token_url')
        self.secret_key = config.get('secret_key')
        self.secret_field = config.get('secret_field', 'secret_key')
        self.token_path = config.get('token_path', 'access_token')
        self.base_url = config.get('base_url', '').rstrip('/')

        self.access_token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None

    def _extract_token(self, data: dict) -> str:
        """Extract token from response using dot-path."""
        obj = data
        for key in self.token_path.split('.'):
            if not isinstance(obj, dict) or key not in obj:
                raise ValueError(
                    f"Token path '{self.token_path}' not found in response. "
                    f"Missing key '{key}'. Available keys: "
                    f"{list(obj.keys()) if isinstance(obj, dict) else type(obj).__name__}"
                )
            obj = obj[key]
        return obj

    def authenticate(self) -> bool:
        self.logger.info("Authenticating with token exchange")

        try:
            data = {self.secret_field: self.secret_key}

            with _silence_insecure_warning_if(self._verify_ssl_off):
                response = self.session.post(self.token_url, data=data, timeout=30)
            response.raise_for_status()

            token_data = response.json()
            self.access_token = self._extract_token(token_data)

            # Default 15-min expiry with 2-min buffer
            expires_in = token_data.get('expiration_utc', 900)
            if isinstance(expires_in, (int, float)):
                self.token_expiry = datetime.now() + timedelta(seconds=expires_in - 120)
            else:
                self.token_expiry = datetime.now() + timedelta(seconds=780)

            self.logger.info("Token exchange authentication successful")
            return True

        except requests.HTTPError as e:
            self.logger.error(f"Token exchange authentication failed: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error during token exchange: {e}")
            raise

    def request(self, url: str, method: str = 'GET', **kwargs) -> requests.Response:
        if not self.access_token or (self.token_expiry and datetime.now() >= self.token_expiry):
            self.authenticate()

        url = self._resolve_url(url)

        # No Bearer prefix — raw token as Authorization header
        headers = kwargs.get('headers', {})
        headers['Authorization'] = self.access_token
        kwargs['headers'] = headers

        return self._do_request(method, url, **kwargs)


# ---------------------------------------------------------------------------
# LoginPasswordJsonAuth
# ---------------------------------------------------------------------------

class LoginPasswordJsonAuth(AuthStrategy):
    """POST a JSON-body login, extract a token via JSONPath, send it on every
    subsequent request as ``Authorization: <prefix><token>``.

    Used by APIs that expose a "log in with username/password and get a
    short-lived token" endpoint — e.g. PocketBase / Beszel
    (``/api/collections/users/auth-with-password``).

    Configuration:
        login_url (required): full URL of the login endpoint
        identity_field: JSON key for the username/identity (default 'identity')
        identity: username / email / identity value
        password_field: JSON key for the password (default 'password')
        password: password value
        token_path: JSONPath into the response that yields the token
            (default '$.token')
        token_header: header name to send on subsequent requests
            (default 'Authorization')
        token_prefix: prefix for the token value (default '' — i.e. raw token).
            Use 'Bearer ' for APIs that expect a Bearer token.
        token_ttl_seconds: how long to consider the token valid before re-auth
            (default 900). On 401, the strategy re-authenticates and retries
            the request once regardless of TTL.
        base_url: API base URL for relative paths
    """

    def __init__(self, config: Dict[str, Any], logger: Optional[logging.Logger] = None):
        super().__init__(config, logger)

        self.login_url = config.get('login_url')
        self.identity_field = config.get('identity_field') or 'identity'
        self.identity = config.get('identity')
        self.password_field = config.get('password_field') or 'password'
        self.password = config.get('password')
        self.token_path = config.get('token_path') or '$.token'
        self.token_header = config.get('token_header') or 'Authorization'
        self.token_prefix = config.get('token_prefix') or ''
        self.token_ttl_seconds = int(config.get('token_ttl_seconds') or 900)
        self.base_url = config.get('base_url', '').rstrip('/')

        self.access_token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None

    def _extract_token(self, body: Any) -> str:
        """Resolve self.token_path against the response body. Lazy import to
        avoid a hard dep on jsonpath_ng for connectors that don't use it."""
        from src.modules.integrations.shared.json_path import (
            extract_first as _extract_first,
        )
        token = _extract_first(body, self.token_path, default=None)
        if not token or not isinstance(token, str):
            raise ValueError(
                f"Login response did not yield a string token at path "
                f"'{self.token_path}'"
            )
        return token

    def authenticate(self) -> bool:
        self.logger.info(
            "Authenticating via login_password_json to %s", self.login_url
        )
        body = {
            self.identity_field: self.identity,
            self.password_field: self.password,
        }
        try:
            with _silence_insecure_warning_if(self._verify_ssl_off):
                response = self.session.post(self.login_url, json=body, timeout=30)
            response.raise_for_status()
            self.access_token = self._extract_token(response.json())
            self.token_expiry = datetime.now() + timedelta(
                seconds=max(self.token_ttl_seconds - 30, 60)
            )
            self.logger.info("login_password_json authentication successful")
            return True
        except requests.HTTPError as e:
            self.logger.error(f"login_password_json authentication failed: {e}")
            raise
        except Exception as e:
            # Covers JSONDecodeError (login response wasn't JSON), ValueError
            # from _extract_token (bad token_path), ConnectionError, Timeout,
            # and anything else. Mirror OAuth2ClientCredentials so failures
            # always carry an auth-context log entry.
            self.logger.error(
                f"Unexpected error during login_password_json authentication: {e}"
            )
            raise

    def request(self, url: str, method: str = 'GET', **kwargs) -> requests.Response:
        if not self.access_token or (
            self.token_expiry and datetime.now() >= self.token_expiry
        ):
            self.authenticate()

        url = self._resolve_url(url)
        response = self._send_with_token(method, url, **kwargs)

        # Token may have been revoked / rotated server-side before our TTL
        # expired — re-auth once on 401 and retry.
        if response.status_code == 401:
            self.logger.info("Got 401, re-authenticating and retrying once")
            self.access_token = None
            self.authenticate()
            response = self._send_with_token(method, url, **kwargs)

        response.raise_for_status()
        return response

    def _send_with_token(self, method: str, url: str, **kwargs) -> requests.Response:
        """One HTTP call with the current token — shared between initial and
        retry path. Skips raise_for_status so the caller can branch on 401."""
        headers = dict(kwargs.get('headers') or {})
        headers[self.token_header] = f"{self.token_prefix}{self.access_token}"
        kwargs['headers'] = headers

        if self.rate_limiter:
            self.rate_limiter.wait_for_token()

        with _silence_insecure_warning_if(self._verify_ssl_off):
            response = self.session.request(method, url, **kwargs)

        if self.rate_limiter:
            if response.status_code == 429:
                self.rate_limiter.on_rate_limit(response)
                response.raise_for_status()
            else:
                self.rate_limiter.on_success(response)

        return response


# ---------------------------------------------------------------------------
# BasicAuth
# ---------------------------------------------------------------------------

class BasicAuth(AuthStrategy):
    """HTTP Basic Authentication — generic, no vendor-specific calls.

    Configuration:
        username: Username
        password: Password
        base_url: API base URL for relative paths
    """

    def __init__(self, config: Dict[str, Any], logger: Optional[logging.Logger] = None):
        super().__init__(config, logger)

        self.username = config.get('username')
        self.password = config.get('password')
        self.base_url = config.get('base_url', '').rstrip('/')

        self.session.auth = (self.username, self.password)

    def authenticate(self) -> bool:
        """Returns True — no HTTP call. Credentials are validated on first request."""
        self.logger.info("Basic authentication configured")
        return True

    def request(self, url: str, method: str = 'GET', **kwargs) -> requests.Response:
        url = self._resolve_url(url)
        return self._do_request(method, url, **kwargs)


# ---------------------------------------------------------------------------
# APIKeyAuth
# ---------------------------------------------------------------------------

class APIKeyAuth(AuthStrategy):
    """Header- or query-based API key with configurable name + prefix.

    Configuration:
        api_key: API key value
        api_key_header: Header name (default: 'X-API-Key')
        api_key_param: Query-param name when location='query' (default: api_key_header)
        api_key_prefix: Value prefix (default: '') — e.g. 'ApiToken ', 'Token '
        location: 'header' (default) | 'query'
        base_url: API base URL for relative paths
    """

    def __init__(self, config: Dict[str, Any], logger: Optional[logging.Logger] = None):
        super().__init__(config, logger)

        self.api_key = config.get('api_key')
        self.api_key_header = config.get('api_key_header', 'X-API-Key')
        self.api_key_param = config.get('api_key_param', self.api_key_header)
        self.api_key_prefix = config.get('api_key_prefix', '')
        self.location = config.get('location', 'header')
        self.base_url = config.get('base_url', '').rstrip('/')

    def authenticate(self) -> bool:
        """Returns True — API key is validated on first request."""
        self.logger.info("API Key authentication configured")
        return True

    def request(self, url: str, method: str = 'GET', **kwargs) -> requests.Response:
        url = self._resolve_url(url)
        value = f"{self.api_key_prefix}{self.api_key}"

        if self.location == 'query':
            params = dict(kwargs.get('params') or {})
            params[self.api_key_param] = value
            kwargs['params'] = params
        else:
            headers = dict(kwargs.get('headers') or {})
            headers[self.api_key_header] = value
            kwargs['headers'] = headers

        return self._do_request(method, url, **kwargs)


# ---------------------------------------------------------------------------
# NoAuth
# ---------------------------------------------------------------------------


class NoAuth(AuthStrategy):
    """No authentication — plain HTTP requests through a session with retries.

    Used by the custom HTTP connector when the user selects auth_type='none'.

    Configuration:
        base_url: API base URL for relative paths (optional)
    """

    def __init__(self, config: Dict[str, Any], logger: Optional[logging.Logger] = None):
        super().__init__(config, logger)
        self.base_url = config.get('base_url', '').rstrip('/')

    def authenticate(self) -> bool:
        self.logger.info("No-auth strategy configured")
        return True

    def request(self, url: str, method: str = 'GET', **kwargs) -> requests.Response:
        return self._do_request(method, self._resolve_url(url), **kwargs)


# ---------------------------------------------------------------------------
# LDAPAuth
# ---------------------------------------------------------------------------

class LDAPAuth(AuthStrategy):
    """LDAP bind authentication for Active Directory connector.

    Connector uses ldap3 directly — no HTTP request() method needed.

    Configuration:
        ldap_server: LDAP server hostname
        ldap_port: Port (default: 389, or 636 for SSL)
        username: Bind DN
        password: Bind password
        use_ssl: Whether to use LDAPS (default: False)
        base_dn: Base DN for searches
    """

    def _create_session(self):
        """LDAPAuth does not use HTTP sessions."""
        return None

    def __init__(self, config: Dict[str, Any], logger: Optional[logging.Logger] = None):
        super().__init__(config, logger)

        self.ldap_server = config.get('ldap_server')
        self.ldap_port = config.get('ldap_port', 636 if config.get('use_ssl') else 389)
        self.username = config.get('username')
        self.password = config.get('password')
        self.use_ssl = config.get('use_ssl', False)
        self.base_dn = config.get('base_dn')

        self._connection = None

    def authenticate(self) -> bool:
        if ldap3 is None:
            raise ImportError("ldap3 is required for LDAP authentication")

        self.logger.info(f"Authenticating via LDAP to {self.ldap_server}:{self.ldap_port}")

        try:
            tls = ldap3.Tls(validate=0) if self.use_ssl else None
            server = ldap3.Server(
                self.ldap_server,
                port=self.ldap_port,
                use_ssl=self.use_ssl,
                tls=tls,
            )
            conn = ldap3.Connection(
                server,
                user=self.username,
                password=self.password,
                auto_bind=False,
            )
            conn.bind()

            if conn.bound:
                self._connection = conn
                self.logger.info("LDAP authentication successful")
                return True
            else:
                self.logger.error("LDAP bind failed — not bound")
                return False

        except Exception as e:
            self.logger.error(f"LDAP authentication error: {e}")
            raise

    def request(self, url: str, method: str = 'GET', **kwargs) -> requests.Response:
        """Not used — LDAP connector uses ldap3 directly via get_connection()."""
        raise NotImplementedError("LDAPAuth does not support HTTP requests. Use get_connection().")

    def get_connection(self):
        """Return the bound ldap3 connection."""
        return self._connection

    def close(self):
        """Close LDAP connection and HTTP session."""
        if self._connection:
            try:
                self._connection.unbind()
            except Exception:
                pass
        super().close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_auth_strategy(config: Dict[str, Any], logger: Optional[logging.Logger] = None) -> AuthStrategy:
    """Factory function to get auth strategy based on config['auth_type'].

    Supported auth_type values:
        oauth2                — OAuth2ClientCredentials
        oauth2_refresh_token  — OAuth2RefreshToken
        oauth2_token_exchange — OAuth2TokenExchange
        login_password        — LoginPasswordJsonAuth
        basic                 — BasicAuth
        api_key               — APIKeyAuth
        none                  — NoAuth
        ldap                  — LDAPAuth
    """
    auth_type = config.get('auth_type', 'oauth2').lower()

    strategy_map = {
        'oauth2': OAuth2ClientCredentials,
        'oauth2_refresh_token': OAuth2RefreshToken,
        'oauth2_token_exchange': OAuth2TokenExchange,
        'login_password': LoginPasswordJsonAuth,
        'basic': BasicAuth,
        'api_key': APIKeyAuth,
        'none': NoAuth,
        'ldap': LDAPAuth,
    }

    cls = strategy_map.get(auth_type)
    if cls is None:
        raise ValueError(f"Unsupported auth_type: {auth_type}")

    return cls(config, logger)
