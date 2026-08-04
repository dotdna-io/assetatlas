# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for MCP server JWT authentication.

Verifies that MCPJWTTokenVerifier correctly validates JWTs
and returns MCP AccessToken objects (or None for invalid tokens).

Regression test for #190.
"""

import asyncio
import time

import jwt
import pytest

from src.mcp.auth import (
    AuthError,
    MCPJWTTokenVerifier,
    TokenVerifier,
    extract_bearer_token,
)

TEST_SECRET = "test-secret-key-for-mcp-auth"


def _make_token(payload: dict, secret: str = TEST_SECRET, algorithm: str = "HS256") -> str:
    """Create a JWT token for testing."""
    return jwt.encode(payload, secret, algorithm=algorithm)


class TestTokenVerifier:
    """Tests for the base TokenVerifier."""

    def test_valid_token(self):
        verifier = TokenVerifier(secret_key=TEST_SECRET)
        token = _make_token({"sub": "user-123", "username": "alice", "role": "admin"})
        claims = verifier.verify(token)
        assert claims.sub == "user-123"
        assert claims.username == "alice"
        assert claims.role == "admin"

    def test_expired_token(self):
        verifier = TokenVerifier(secret_key=TEST_SECRET)
        token = _make_token({"sub": "user-1", "exp": int(time.time()) - 60})
        with pytest.raises(AuthError, match="expired"):
            verifier.verify(token)

    def test_wrong_secret(self):
        verifier = TokenVerifier(secret_key=TEST_SECRET)
        token = _make_token({"sub": "user-1"}, secret="wrong-secret")
        with pytest.raises(AuthError, match="[Ii]nvalid token"):
            verifier.verify(token)

    def test_missing_sub_claim(self):
        verifier = TokenVerifier(secret_key=TEST_SECRET)
        token = _make_token({"username": "alice"})
        with pytest.raises(AuthError, match="missing 'sub'"):
            verifier.verify(token)

    def test_no_secret_key_rejects_all(self):
        verifier = TokenVerifier(secret_key="")
        token = _make_token({"sub": "user-1"})
        with pytest.raises(AuthError, match="not configured"):
            verifier.verify(token)


class TestMCPJWTTokenVerifier:
    """Tests for the MCP SDK-compatible token verifier."""

    def test_valid_token_returns_access_token(self):
        verifier = MCPJWTTokenVerifier(secret_key=TEST_SECRET)
        token = _make_token({"sub": "user-42", "username": "bob", "role": "admin"})
        result = asyncio.run(verifier.verify_token(token))
        assert result is not None
        assert result.client_id == "user-42"
        assert "admin" in result.scopes
        assert result.token == token

    def test_invalid_token_returns_none(self):
        verifier = MCPJWTTokenVerifier(secret_key=TEST_SECRET)
        result = asyncio.run(verifier.verify_token("garbage.token.value"))
        assert result is None

    def test_expired_token_returns_none(self):
        verifier = MCPJWTTokenVerifier(secret_key=TEST_SECRET)
        token = _make_token({"sub": "user-1", "exp": int(time.time()) - 60})
        result = asyncio.run(verifier.verify_token(token))
        assert result is None

    def test_wrong_secret_returns_none(self):
        verifier = MCPJWTTokenVerifier(secret_key=TEST_SECRET)
        token = _make_token({"sub": "user-1"}, secret="wrong")
        result = asyncio.run(verifier.verify_token(token))
        assert result is None


class TestExtractBearerToken:
    """Tests for the extract_bearer_token helper."""

    def test_valid_bearer(self):
        assert extract_bearer_token("Bearer abc123") == "abc123"

    def test_missing_header(self):
        with pytest.raises(AuthError, match="[Mm]issing"):
            extract_bearer_token("")

    def test_wrong_scheme(self):
        with pytest.raises(AuthError, match="Bearer"):
            extract_bearer_token("Basic abc123")


class TestCreateServer:
    """Tests that server creation wires auth correctly."""

    def test_stdio_server_has_no_auth(self):
        from src.mcp.server import create_server
        server = create_server(transport="stdio")
        assert server.settings.auth is None

    def test_http_server_has_auth(self):
        from src.mcp.server import create_server
        server = create_server(transport="streamable-http", port=9999)
        assert server.settings.auth is not None
        assert server._token_verifier is not None
