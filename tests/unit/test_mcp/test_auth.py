# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for MCP JWT authentication."""

from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest

from src.mcp.auth import AuthError, TokenClaims, TokenVerifier, extract_bearer_token

SECRET = "test-secret-key-with-enough-length-for-hs256"


def _make_token(claims=None, secret=SECRET, expired=False):
    """Helper to create JWT tokens for testing."""
    payload = {
        "sub": "user-uuid-123",
        "username": "testuser",
        "role": "admin",
        "session_id": "session-456",
        "iat": datetime.now(timezone.utc),
    }
    if expired:
        payload["exp"] = datetime.now(timezone.utc) - timedelta(hours=1)
    else:
        payload["exp"] = datetime.now(timezone.utc) + timedelta(hours=1)
    if claims:
        payload.update(claims)
    return pyjwt.encode(payload, secret, algorithm="HS256")


class TestTokenVerifier:
    """Test JWT token verification."""

    def test_verify_valid_token(self):
        verifier = TokenVerifier(secret_key=SECRET)
        token = _make_token()
        claims = verifier.verify(token)
        assert isinstance(claims, TokenClaims)
        assert claims.sub == "user-uuid-123"
        assert claims.username == "testuser"
        assert claims.role == "admin"
        assert claims.session_id == "session-456"

    def test_verify_expired_token(self):
        verifier = TokenVerifier(secret_key=SECRET)
        token = _make_token(expired=True)
        with pytest.raises(AuthError, match="expired"):
            verifier.verify(token)

    def test_verify_wrong_secret(self):
        verifier = TokenVerifier(secret_key="wrong-secret-key-that-is-long-enough")
        token = _make_token()
        with pytest.raises(AuthError, match="Invalid token"):
            verifier.verify(token)

    def test_verify_malformed_token(self):
        verifier = TokenVerifier(secret_key=SECRET)
        with pytest.raises(AuthError, match="Invalid token"):
            verifier.verify("not.a.valid.jwt")

    def test_verify_missing_sub_claim(self):
        verifier = TokenVerifier(secret_key=SECRET)
        payload = {
            "username": "testuser",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        token = pyjwt.encode(payload, SECRET, algorithm="HS256")
        with pytest.raises(AuthError, match="sub"):
            verifier.verify(token)

    def test_verify_no_secret_configured(self):
        verifier = TokenVerifier(secret_key="")
        with pytest.raises(AuthError, match="not configured"):
            verifier.verify("any-token")

    def test_verify_all_roles(self):
        verifier = TokenVerifier(secret_key=SECRET)
        for role in ("user", "admin", "super_admin"):
            token = _make_token(claims={"role": role})
            claims = verifier.verify(token)
            assert claims.role == role

    def test_verify_defaults_role_to_user(self):
        verifier = TokenVerifier(secret_key=SECRET)
        payload = {
            "sub": "user-1",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        token = pyjwt.encode(payload, SECRET, algorithm="HS256")
        claims = verifier.verify(token)
        assert claims.role == "user"


class TestExtractBearerToken:
    """Test Authorization header parsing."""

    def test_valid_bearer(self):
        assert extract_bearer_token("Bearer abc123") == "abc123"

    def test_bearer_case_insensitive(self):
        assert extract_bearer_token("bearer abc123") == "abc123"

    def test_missing_header(self):
        with pytest.raises(AuthError, match="Missing"):
            extract_bearer_token("")

    def test_empty_header(self):
        with pytest.raises(AuthError, match="Missing"):
            extract_bearer_token("")

    def test_wrong_scheme(self):
        with pytest.raises(AuthError, match="Bearer"):
            extract_bearer_token("Basic abc123")

    def test_no_token_after_bearer(self):
        with pytest.raises(AuthError, match="Bearer"):
            extract_bearer_token("Bearer")

    def test_token_with_dots(self):
        token = "eyJ.eyJ.sig"
        assert extract_bearer_token(f"Bearer {token}") == token
