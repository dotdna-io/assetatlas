# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bearer credential verification for the MCP server HTTP transport.

Two credential shapes are accepted: a JWT issued by the Flask API's
JWT-Extended auth system, and an AssetAtlas API key (``aa_…``, minted in
Settings > Access & Security > API Keys). The JWT expires after 60 minutes,
which makes it unusable in a desktop MCP client config; the key is the
long-lived alternative (#1158).

No auth is required for stdio transport (local Claude Code use).
"""

import ipaddress
import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import jwt
from anyio import to_thread
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.provider import TokenVerifier as MCPTokenVerifier

from src.mcp.context import ensure_app_context
from src.modules.api_keys.repository import ApiKeyRepository
from src.modules.api_keys.service import KEY_PREFIX, ApiKeyService

logger = logging.getLogger(__name__)

# Recorded when no caller address could be determined. `api_keys.last_used_ip`
# is an `inet` column, so a non-address would fail the UPDATE outright.
UNKNOWN_CLIENT_ADDRESS = "0.0.0.0"

_client_address: ContextVar[str] = ContextVar(
    "mcp_client_address", default=UNKNOWN_CLIENT_ADDRESS
)


@dataclass
class TokenClaims:
    """Verified JWT token claims."""

    sub: str  # user_id
    username: str
    role: str  # user, admin, super_admin
    session_id: Optional[str] = None


class TokenVerifier:
    """Verifies JWT access tokens using the same secret as the Flask API."""

    def __init__(self, secret_key: Optional[str] = None, algorithm: str = "HS256"):
        self.secret_key = secret_key or os.getenv(
            "JWT_SECRET_KEY",
            os.getenv("SECRET_KEY", ""),
        )
        self.algorithm = algorithm

        if not self.secret_key:
            logger.warning(
                "No JWT_SECRET_KEY or SECRET_KEY set — "
                "HTTP auth will reject all tokens"
            )

    def verify(self, token: str) -> TokenClaims:
        """Verify a JWT token and return its claims.

        Args:
            token: Raw JWT token string (without 'Bearer ' prefix)

        Returns:
            TokenClaims with verified user info

        Raises:
            AuthError: If token is invalid, expired, or malformed
        """
        if not self.secret_key:
            raise AuthError("Server not configured for token verification")

        try:
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
            )
        except jwt.ExpiredSignatureError:
            raise AuthError("Token has expired")
        except jwt.InvalidTokenError as e:
            raise AuthError(f"Invalid token: {e}")

        # Extract claims — Flask-JWT-Extended puts identity in 'sub'
        sub = payload.get("sub")
        if not sub:
            raise AuthError("Token missing 'sub' claim")

        return TokenClaims(
            sub=str(sub),
            username=payload.get("username", ""),
            role=payload.get("role", "user"),
            session_id=payload.get("session_id"),
        )


class AuthError(Exception):
    """Authentication error raised by TokenVerifier."""

    pass


def _is_ip_address(value: str) -> bool:
    """True if ``value`` parses as an IPv4/IPv6 address."""
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def scope_client_address(scope: Dict[str, Any]) -> str:
    """Caller address for an ASGI request scope.

    Prefers nginx's ``X-Real-IP``, which the bundled config sets from
    ``$remote_addr`` on the MCP location (``docker/nginx/conf.d/online.conf``);
    ``proxy_set_header`` overwrites whatever the caller sent, so it names the
    real peer rather than the proxy's own container address, which is all
    ``scope["client"]`` would report. Falls back to the socket peer.

    Anything that is not a parseable address is discarded — the value lands in
    ``api_keys.last_used_ip``, an ``inet`` column. That header is only worth
    what the topology makes it: the bundle `expose`s the MCP port on the
    container network and never publishes it, so nginx is the only way in.
    ``last_used_ip`` is informational; nothing authorizes on it.
    """
    for name, value in scope.get("headers") or ():
        if name == b"x-real-ip":
            candidate = value.decode("latin-1").strip()
            if _is_ip_address(candidate):
                return candidate
            break

    client = scope.get("client")
    if client and _is_ip_address(str(client[0])):
        return str(client[0])

    return UNKNOWN_CLIENT_ADDRESS


class ClientAddressMiddleware:
    """Pure-ASGI middleware recording the caller's address for the request.

    ``verify_token`` is handed the bearer token and nothing else — the SDK's
    ``BearerAuthBackend`` never passes the connection down — so the API-key
    branch has no other way to learn who called, and ``last_used_ip`` would be
    unstamped. Installed OUTSIDE the SDK's ``AuthenticationMiddleware`` (see
    ``create_server``) so the address is set before verification runs.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        reset_token = _client_address.set(scope_client_address(scope))
        try:
            await self.app(scope, receive, send)
        finally:
            _client_address.reset(reset_token)


def _expires_at_epoch(value: Optional[datetime]) -> Optional[int]:
    """Epoch seconds for an ``api_keys.expires_at``, or None if it has none.

    Handed to the SDK so a session is dropped the moment the key lapses:
    ``BearerAuthBackend`` compares ``AccessToken.expires_at`` against the wall
    clock on every request. Naive values are read as UTC, matching
    ``ApiKeyService.validate_key``.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


def _validate_and_stamp(
    db, plaintext_key: str, client_address: str
) -> Optional[Dict[str, Any]]:
    """Validate an API key and stamp its usage — one worker thread, ≤2 queries.

    Both calls are blocking psycopg2, so they run off the event loop together
    rather than costing two hops. The stamp is throttled to one write per key
    per minute (``LAST_USED_STAMP_INTERVAL_S``) and skipped entirely when the
    row's ``last_used_at`` is still fresh; it is fire-and-forget exactly as
    the Flask decorator treats it (``src/auth/api_key_auth.py``): a key that
    authenticates must not be rejected because its usage row would not write.
    """
    service = ApiKeyService(ApiKeyRepository(db))
    row = service.validate_key(plaintext_key)
    if row is None:
        return None

    try:
        service.record_usage(row['id'], client_address, row.get('last_used_at'))
    except Exception:
        logger.debug("Failed to record API key usage", exc_info=True)

    return row


class MCPJWTTokenVerifier(MCPTokenVerifier):
    """MCP SDK-compatible verifier for a JWT or an AssetAtlas API key."""

    def __init__(self, secret_key: Optional[str] = None, algorithm: str = "HS256"):
        self._verifier = TokenVerifier(secret_key=secret_key, algorithm=algorithm)

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        """Verify a bearer credential, returning an AccessToken or None."""
        try:
            claims = self._verifier.verify(token)
            return AccessToken(
                token=token,
                client_id=claims.sub,
                scopes=[claims.role],
                expires_at=None,  # JWT expiry already checked by PyJWT
            )
        except AuthError:
            pass

        # An API key can never parse as a JWT, so the JWT attempt above costs
        # one failed decode and nothing else.
        if token.startswith(KEY_PREFIX):
            return await self._verify_api_key(token)

        return None

    async def _verify_api_key(self, token: str) -> Optional[AccessToken]:
        """Authenticate an ``aa_…`` API key against the database.

        The adapter is resolved per call and never cached: this verifier is
        built in ``create_server()``, before the process scope opens the pool.
        Auth runs before any MCP session or lifespan, so it goes through
        ``ensure_app_context()`` — which builds the context if startup could
        not (a DB still coming up) — rather than reading the global (#1448).
        A context that still cannot be built is an auth failure, never an
        exception — the SDK's auth backend would surface a raise as a 500
        instead of a 401.

        There is deliberately NO rate limiting on this path (#1158). The Flask
        decorator throttles before it touches the DB, but that counter is
        Flask-bound — ``current_app.config`` plus per-process in-memory state —
        and the MCP server is its own process, so a counter added here would be
        per-worker rather than global: misleading rather than protective. Every
        MCP tool is read-only, so unthrottled key auth is the accepted bargain
        until a shared-store limiter exists. It is documented in the *MCP
        Server* help topic too, not just here.
        """
        try:
            app_ctx = await ensure_app_context()
        except Exception:
            logger.warning(
                "API key presented while the MCP app context is unavailable — "
                "rejecting", exc_info=True
            )
            return None
        if app_ctx.db is None:
            logger.warning("MCP app context has no database — rejecting API key")
            return None

        try:
            row = await to_thread.run_sync(
                _validate_and_stamp, app_ctx.db, token, _client_address.get()
            )
        except Exception:
            logger.warning("API key verification failed", exc_info=True)
            return None

        if row is None:
            return None

        return AccessToken(
            token=token,
            client_id=str(row['user_id']),
            scopes=[row.get('role') or 'user'],
            expires_at=_expires_at_epoch(row.get('expires_at')),
        )


def extract_bearer_token(authorization: str) -> str:
    """Extract token from 'Bearer <token>' header value.

    Args:
        authorization: Full Authorization header value

    Returns:
        The raw token string

    Raises:
        AuthError: If header format is invalid
    """
    if not authorization:
        raise AuthError("Missing Authorization header")

    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthError("Authorization header must be 'Bearer <token>'")

    return parts[1]
