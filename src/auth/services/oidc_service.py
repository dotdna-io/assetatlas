# SPDX-License-Identifier: AGPL-3.0-or-later
"""OIDC (OpenID Connect) authentication service.

Wraps `authlib` for Authorization Code Flow with PKCE. Supports any OIDC-compliant
provider (Microsoft Entra ID, Google, Okta, etc.) configured via discovery URL.

The service is responsible for:
  1. Building the IdP authorization URL (with state, nonce, PKCE).
  2. Exchanging the authorization code for tokens.
  3. Validating the ID token (signature via JWKS, audience, issuer, expiration, nonce).
  4. Mapping IdP group claims to AssetAtlas roles.

State management is cache-backed (Flask-Caching), keyed by `oidc_state:<state>`,
with a 10-minute TTL. The service is stateless beyond the cache.
"""
from __future__ import annotations

import ipaddress
import logging
import secrets
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from src.auth.services._role_utils import resolve_role
from src.modules.integrations.shared.encryption import decrypt, is_encrypted

try:
    from authlib.integrations.requests_client import OAuth2Session
    from authlib.jose import jwt as _jose_jwt
    from authlib.jose import JsonWebToken as _JsonWebToken
    from authlib.jose.errors import JoseError
    import requests as _requests
except ImportError:  # authlib in requirements-prod.txt; this is for type-checkers
    OAuth2Session = None  # type: ignore
    _jose_jwt = None  # type: ignore
    _JsonWebToken = None  # type: ignore
    JoseError = Exception  # type: ignore
    _requests = None  # type: ignore

logger = logging.getLogger(__name__)


_STATE_TTL_SECONDS = 600  # 10 min for OIDC dance
_DISCOVERY_TTL_SECONDS = 3600  # cache discovery doc + JWKS for 1 hour

# SSRF guard: cap response body size for discovery and JWKS fetches to defeat
# memory exhaustion via a malicious or compromised IdP endpoint. 1 MB is well
# above typical discovery (~2 KB) / JWKS (~10 KB) payloads.
MAX_DISCOVERY_RESPONSE_BYTES = 1_048_576

# Cap redirect hops when following Location headers manually, so we can re-validate
# each hop against the SSRF allowlist (private/loopback IPs blocked).
_MAX_REDIRECT_HOPS = 3

# RFC 8725 §3.1: pin id-token signature algs explicitly. We always strip 'none'
# and HMAC-based algs (HS*) from the IdP's advertised set — HMAC algs would let
# anyone with the public key fingerprint forge a token, which defeats the point
# of asymmetric verification. The fallback set is the standard asymmetric algs.
_FORBIDDEN_ID_TOKEN_ALGS = frozenset({
    'none', 'HS256', 'HS384', 'HS512',
})
_FALLBACK_ID_TOKEN_ALGS = (
    'RS256', 'RS384', 'RS512',
    'ES256', 'ES384', 'ES512',
    'PS256', 'PS384', 'PS512',
)


class OidcSsrfError(Exception):
    """Raised when an OIDC URL fails the SSRF allowlist (scheme or resolved IP)."""


@dataclass
class OidcUserInfo:
    """User identity successfully authenticated via OIDC."""
    subject: str           # `sub` claim — IdP-side user ID
    email: str
    full_name: str
    role: str              # 'user' | 'admin' | 'super_admin'
    groups: List[str] = field(default_factory=list)


def _assert_https_url(url: str) -> str:
    """Reject anything that is not an absolute https:// URL with a hostname.

    Returns the hostname (lower-cased) for downstream checks. Raises
    OidcSsrfError on file://, http://, missing host, or malformed URLs.
    """
    if not isinstance(url, str) or not url:
        raise OidcSsrfError("OIDC URL must be a non-empty string")
    parsed = urlparse(url)
    if parsed.scheme != 'https':
        raise OidcSsrfError(
            f"OIDC URL must use https scheme (got {parsed.scheme!r})"
        )
    host = (parsed.hostname or '').lower()
    if not host:
        raise OidcSsrfError("OIDC URL must include a hostname")
    return host


def _assert_public_host(host: str) -> None:
    """Resolve `host` and assert every resolved address is public.

    SECURITY POLICY (fail-closed): we reject if ANY of these hold:
      - hostname is a literal private/loopback/link-local IP
      - DNS resolution fails (we cannot prove the address is safe)
      - any resolved address is private, loopback, link-local, multicast,
        unspecified, or reserved

    The fail-closed posture trades a small operational risk (a brief IdP DNS
    outage rejects a settings save or token refresh) for SSRF safety. Admins
    will see a clear error and can retry once their IdP is reachable.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise OidcSsrfError(
            f"OIDC host {host!r} could not be resolved: {exc}. "
            "Refusing to use an unresolvable host (fail-closed)."
        ) from exc

    seen = set()
    for info in infos:
        sockaddr = info[4]
        addr_str = sockaddr[0]
        if addr_str in seen:
            continue
        seen.add(addr_str)
        try:
            addr = ipaddress.ip_address(addr_str)
        except ValueError:
            raise OidcSsrfError(
                f"OIDC host {host!r} resolved to non-IP value {addr_str!r}"
            )
        # is_global rules out private, loopback, link-local, multicast, reserved,
        # unspecified, and (on 3.13) shared-address ranges in one shot. We use
        # the explicit checks too so the error message names the failure cleanly.
        if addr.is_loopback:
            raise OidcSsrfError(
                f"OIDC host {host!r} resolves to loopback address {addr_str}"
            )
        if addr.is_link_local:
            raise OidcSsrfError(
                f"OIDC host {host!r} resolves to link-local address {addr_str} "
                "(blocks cloud instance-metadata endpoints like 169.254.169.254)"
            )
        if addr.is_private:
            raise OidcSsrfError(
                f"OIDC host {host!r} resolves to private address {addr_str}"
            )
        if addr.is_multicast or addr.is_unspecified or addr.is_reserved:
            raise OidcSsrfError(
                f"OIDC host {host!r} resolves to non-routable address {addr_str}"
            )


def _validated_get(url: str, *, max_bytes: int, timeout: int) -> Any:
    """SSRF-safe HTTPS GET that enforces scheme, IP allowlist, response cap.

    - Asserts https scheme and resolves the hostname to public IPs only.
    - Manually follows up to _MAX_REDIRECT_HOPS redirects, re-validating
      every Location header (DNS rebinding / open-redirect defense).
    - Streams the response and reads up to max_bytes; raises if exceeded.
    """
    visited: List[str] = []
    current = url
    for _hop in range(_MAX_REDIRECT_HOPS + 1):
        host = _assert_https_url(current)
        _assert_public_host(host)
        visited.append(current)

        resp = _requests.get(
            current,
            timeout=timeout,
            stream=True,
            allow_redirects=False,
        )
        try:
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get('Location') or ''
                if not location:
                    raise OidcSsrfError(
                        f"OIDC fetch of {current!r} returned redirect "
                        f"{resp.status_code} with no Location header"
                    )
                # Resolve relative redirects against the current URL.
                from urllib.parse import urljoin
                current = urljoin(current, location)
                continue

            resp.raise_for_status()

            chunks: List[bytes] = []
            total = 0
            for chunk in resp.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise OidcSsrfError(
                        f"OIDC response from {current!r} exceeded "
                        f"{max_bytes} bytes (cap to defeat memory exhaustion)"
                    )
                chunks.append(chunk)
            body = b''.join(chunks)

            import json as _json
            return _json.loads(body.decode('utf-8'))
        finally:
            resp.close()

    raise OidcSsrfError(
        f"OIDC fetch exceeded {_MAX_REDIRECT_HOPS} redirect hops "
        f"(visited: {visited})"
    )


class OidcService:
    """Authenticate users via OIDC Authorization Code + PKCE flow."""

    SETTINGS_CATEGORY = 'oidc'

    def __init__(self, settings_manager):
        self._settings = settings_manager

    # ----- introspection -------------------------------------------------

    def is_enabled(self) -> bool:
        if not self._get('enabled', False):
            return False
        for required in ('discovery_url', 'client_id', 'client_secret', 'redirect_uri'):
            if not self._get(required, ''):
                return False
        return True

    def allow_local_fallback(self) -> bool:
        return bool(self._get('allow_local_fallback', True))

    # Friendly labels for the detected IdP families (see provider_kind) —
    # used when no custom provider_name is configured.
    _KIND_LABELS = {
        'entra': 'Microsoft Entra',
        'okta': 'Okta',
        'google': 'Google',
        'aws': 'Amazon Cognito',
        'cyberark': 'CyberArk',
    }

    def provider_name(self) -> str:
        """Display label for the login button ("Sign in with <name>").

        A configured name always wins. The generic defaults ('SSO', and the
        pre-138 'Single sign-on') mean "not configured" and fall back to the
        detected IdP family's name, then to plain 'SSO'.
        """
        name = str(self._get('provider_name', '') or '').strip()
        if name and name not in ('SSO', 'Single sign-on'):
            return name
        return self._KIND_LABELS.get(self.provider_kind(), 'SSO')

    def provider_kind(self) -> str:
        """Detect the IdP family from the configured discovery_url so the
        login UI can show the right vendor icon. Falls back to 'generic'
        when the host doesn't match anything we recognize."""
        url = (self._get('discovery_url', '') or '').lower()
        if any(s in url for s in (
            'microsoftonline.com', 'login.microsoft', 'sts.windows.net',
            'login.partner.microsoftonline.cn',
        )):
            return 'entra'
        if 'okta.com' in url or 'oktapreview.com' in url:
            return 'okta'
        if 'accounts.google.com' in url:
            return 'google'
        if 'cognito-idp' in url or 'amazoncognito.com' in url:
            return 'aws'
        if 'cyberark.com' in url or 'idaptive.com' in url:
            return 'cyberark'
        return 'generic'

    def frontend_complete_url(self) -> str:
        return str(self._get('frontend_complete_url', '/oidc-complete') or '/oidc-complete')

    # ----- public auth API -----------------------------------------------

    def build_authorization_url(self) -> Tuple[str, str]:
        """Build an authorization URL and persist state for the upcoming callback.

        Returns (authorization_url, state).
        """
        nonce = secrets.token_urlsafe(36)        # ~48 chars, ~256 bits entropy
        code_verifier = secrets.token_urlsafe(48)  # ~64 chars, ~384 bits entropy

        meta = self._discovery_metadata()
        client = OAuth2Session(
            client_id=self._get('client_id', ''),
            client_secret=self._client_secret(),
            scope=self._get('scopes', 'openid email profile'),
            redirect_uri=self._get('redirect_uri', ''),
            code_challenge_method='S256',
        )
        url, state = client.create_authorization_url(
            meta['authorization_endpoint'],
            nonce=nonce,
            code_verifier=code_verifier,
        )
        self._store_state(state, nonce, code_verifier)
        return url, state

    def exchange_code(self, code: str, state: str) -> Optional[OidcUserInfo]:
        """Exchange the authorization code for tokens, validate ID token, return user info.

        Returns None on any failure (invalid state, validation error, etc.). Caller treats
        None as "OIDC rejected, fall through to local password if allowed".
        """
        if not code or not state:
            return None
        try:
            entry = self._consume_state(state)
            if entry is None:
                logger.info("OIDC: state %r not found in cache (expired or unknown)", state)
                return None

            meta = self._discovery_metadata()
            client = OAuth2Session(
                client_id=self._get('client_id', ''),
                client_secret=self._client_secret(),
                scope=self._get('scopes', 'openid email profile'),
                redirect_uri=entry['redirect_uri'],
            )
            token = client.fetch_token(
                meta['token_endpoint'],
                code=code,
                code_verifier=entry['code_verifier'],
                grant_type='authorization_code',
            )
            id_token_jwt = token.get('id_token')
            if not id_token_jwt:
                logger.warning("OIDC: token endpoint did not return id_token")
                return None

            claims = self._validate_id_token(id_token_jwt, expected_nonce=entry['nonce'])
            return self._build_user_info(claims)
        except JoseError as e:
            logger.warning("OIDC: ID token validation failed: %s", e)
            return None
        except Exception as e:
            logger.warning("OIDC: code exchange failed: %s", e)
            return None

    def build_logout_url(self) -> Optional[str]:
        """Return the IdP end_session_endpoint URL for RP-initiated logout,
        or None when the IdP doesn't support it / OIDC isn't fully configured.

        Always includes ``client_id``: without it Microsoft Entra (and most
        OIDC IdPs) silently ignore the request, leaving the SSO cookie alive
        — so the next "Sign in with <provider>" silently re-authenticates.

        Includes ``post_logout_redirect_uri`` only when the operator has
        configured one (and registered it at the IdP). Without it the IdP
        terminates the session and shows its own "signed out" page —
        the important property (real session termination) holds either way.

        Returns None when ``client_id`` is missing, the IdP doesn't expose
        ``end_session_endpoint``, or discovery fails — the caller falls back
        to the local-only logout path so a flaky IdP can't break sign-out.
        """
        client_id = self._get('client_id', '') or ''
        if not client_id:
            return None
        try:
            meta = self._discovery_metadata()
        except Exception as e:
            logger.warning("OIDC: cannot build logout URL — discovery failed: %s", e)
            return None
        end_session = meta.get('end_session_endpoint')
        if not end_session:
            return None

        from urllib.parse import quote
        params = [f"client_id={quote(client_id, safe='')}"]
        post_logout = self._get('post_logout_redirect_uri', '') or ''
        if post_logout:
            params.append(f"post_logout_redirect_uri={quote(post_logout, safe='')}")
        return f"{end_session}?{'&'.join(params)}"

    def test_discovery(self) -> Dict[str, Any]:
        """Admin diagnostic — fetch the discovery document, return summary."""
        result: Dict[str, Any] = {
            'success': False,
            'issuer': None,
            'authorization_endpoint': None,
            'token_endpoint': None,
            'jwks_uri': None,
            'errors': [],
        }
        try:
            meta = self._discovery_metadata()
            result.update({
                'success': True,
                'issuer': meta.get('issuer'),
                'authorization_endpoint': meta.get('authorization_endpoint'),
                'token_endpoint': meta.get('token_endpoint'),
                'jwks_uri': meta.get('jwks_uri'),
            })
        except Exception as e:
            result['errors'].append(f'OIDC discovery error: {e}')
        return result

    # ----- internals -----------------------------------------------------

    def _get(self, key: str, default: Any = None) -> Any:
        return self._settings.get(self.SETTINGS_CATEGORY, key, default)

    def _client_secret(self) -> str:
        raw = self._get('client_secret', '') or ''
        return decrypt(raw) if is_encrypted(raw) else raw

    def _http_timeout(self) -> int:
        return int(self._get('http_timeout_seconds', 10))

    # ----- state cache (10-min TTL) -------------------------------------

    def _store_state(self, state: str, nonce: str, code_verifier: str) -> None:
        from src.api.utils.caching import cache
        cache.set(
            f'oidc_state:{state}',
            {
                'nonce': nonce,
                'code_verifier': code_verifier,
                'redirect_uri': self._get('redirect_uri', ''),
            },
            timeout=_STATE_TTL_SECONDS,
        )

    def _consume_state(self, state: str) -> Optional[Dict[str, Any]]:
        from src.api.utils.caching import cache
        key = f'oidc_state:{state}'
        entry = cache.get(key)
        if entry is not None:
            cache.delete(key)
        return entry

    # ----- discovery + JWKS (cached) ------------------------------------

    def _discovery_metadata(self) -> Dict[str, Any]:
        from src.api.utils.caching import cache
        url = self._get('discovery_url', '')
        cache_key = f'oidc_discovery:{url}'
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        # SSRF guard at fetch time: the host could resolve differently than at
        # write time (DNS rebinding), so we re-validate scheme + IP here.
        meta = _validated_get(
            url,
            max_bytes=MAX_DISCOVERY_RESPONSE_BYTES,
            timeout=self._http_timeout(),
        )
        cache.set(cache_key, meta, timeout=_DISCOVERY_TTL_SECONDS)
        return meta

    def _load_jwks(self, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        from src.api.utils.caching import cache
        if meta is None:
            meta = self._discovery_metadata()
        jwks_uri = meta['jwks_uri']
        cache_key = f'oidc_jwks:{jwks_uri}'
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        # SSRF guard: the discovery doc is admin-trusted but the jwks_uri it
        # returns could point anywhere — re-validate scheme + resolved IP.
        jwks = _validated_get(
            jwks_uri,
            max_bytes=MAX_DISCOVERY_RESPONSE_BYTES,
            timeout=self._http_timeout(),
        )
        cache.set(cache_key, jwks, timeout=_DISCOVERY_TTL_SECONDS)
        return jwks

    # ----- ID token validation ------------------------------------------

    def _validate_id_token(self, id_token_jwt: str, expected_nonce: str) -> Dict[str, Any]:
        meta = self._discovery_metadata()
        jwks = self._load_jwks(meta=meta)
        # Pin the accepted id-token signing algs explicitly (RFC 8725 §3.1).
        # Without this, authlib accepts whichever alg the token header advertises,
        # which is mostly safe via JWKS key-type matching but is defense-in-depth
        # otherwise. We construct a fresh JsonWebToken restricted to the safe set
        # — the module-level `_jose_jwt` accepts HS*/none.
        allowed_algs = self._allowed_id_token_algs(meta)
        decoder = _JsonWebToken(allowed_algs)
        claims = decoder.decode(id_token_jwt, jwks)
        # Manual validation of audience, issuer, expiration, nonce
        # RFC 7519 §4.1.3: aud MAY be a string OR an array of strings.
        # Microsoft Entra returns it as an array even for single-client tokens.
        aud = claims.get('aud')
        client_id = self._get('client_id', '')
        if isinstance(aud, list):
            if client_id not in aud:
                raise JoseError(f"Invalid audience: {aud}")
        elif aud != client_id:
            raise JoseError(f"Invalid audience: {aud}")
        if claims.get('iss') != meta.get('issuer'):
            raise JoseError(f"Invalid issuer: {claims.get('iss')}")
        exp = claims.get('exp')
        if exp is None:
            raise JoseError('ID token missing exp claim')
        if int(exp) < int(time.time()):
            raise JoseError('ID token expired')
        nbf = claims.get('nbf')
        if nbf is not None and int(nbf) > int(time.time()):
            raise JoseError('ID token not yet valid (nbf in future)')
        if claims.get('nonce') != expected_nonce:
            raise JoseError('Nonce mismatch')
        return dict(claims)

    # ----- user info construction ---------------------------------------

    def _build_user_info(self, claims: Dict[str, Any]) -> OidcUserInfo:
        mail_claim = self._get('claim_mail', 'email')
        name_claim = self._get('claim_name', 'name')
        groups_claim = self._get('claim_groups', 'groups')

        email = claims.get(mail_claim) or claims.get('preferred_username') or ''
        full_name = claims.get(name_claim) or ''
        groups_raw = claims.get(groups_claim) or []
        groups = list(groups_raw) if isinstance(groups_raw, list) else [groups_raw]

        return OidcUserInfo(
            subject=str(claims.get('sub', '')),
            email=str(email),
            full_name=str(full_name),
            role=self._resolve_role(groups),
            groups=[str(g) for g in groups],
        )

    def _resolve_role(self, groups: List[str]) -> str:
        return resolve_role(groups, self._get, 'OIDC')

    # ----- alg pinning ---------------------------------------------------

    @staticmethod
    def _allowed_id_token_algs(meta: Dict[str, Any]) -> List[str]:
        """Return the allowed signing algs for id-token decoding.

        Reads ``id_token_signing_alg_values_supported`` from the IdP discovery
        document and filters out HMAC variants and 'none' (RFC 8725 §3.1).
        Falls back to the standard asymmetric set when the IdP doesn't
        advertise any allowed alg or only advertises forbidden ones.
        """
        advertised = meta.get('id_token_signing_alg_values_supported') or []
        if not isinstance(advertised, list):
            advertised = [advertised]
        safe = [
            alg for alg in advertised
            if isinstance(alg, str) and alg not in _FORBIDDEN_ID_TOKEN_ALGS
        ]
        if safe:
            return safe
        return list(_FALLBACK_ID_TOKEN_ALGS)
