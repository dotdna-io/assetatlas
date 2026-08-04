# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for TokenService.

Covers token-pair claim shape, refresh-token hash + verification round-trip
(used by the JWT revocation callback to detect refresh-token reuse, RFC 6819
§5.2.2.3), password-reset token generation, and expiration calculations.
"""
import re
from datetime import datetime, timedelta, timezone

import pytest

from src.auth.services.token_service import TokenService


class TestGenerateTokenPair:
    """generate_token_pair prepares JWT claims and a JTI for revocation tracking."""

    @pytest.fixture
    def pair(self):
        return TokenService.generate_token_pair(
            user_id='user-uuid-123',
            username='alice',
            email='alice@corp.com',
            role='admin',
            session_id='session-uuid-456',
        )

    def test_returns_dict_with_jti_and_claims(self, pair):
        assert isinstance(pair, dict)
        assert set(pair.keys()) == {'access_token_jti', 'user_claims'}

    def test_access_token_jti_is_urlsafe_string(self, pair):
        jti = pair['access_token_jti']
        assert isinstance(jti, str)
        # token_urlsafe(32) -> ~43 chars of [A-Za-z0-9_-]
        assert len(jti) >= 40
        assert re.match(r'^[A-Za-z0-9_\-]+$', jti)

    def test_user_claims_contains_username_email_role(self, pair):
        claims = pair['user_claims']
        assert claims['username'] == 'alice'
        assert claims['email'] == 'alice@corp.com'
        assert claims['role'] == 'admin'

    def test_user_claims_carries_session_and_jti(self, pair):
        claims = pair['user_claims']
        assert claims['session_id'] == 'session-uuid-456'
        # jti in the claims must match the top-level access_token_jti
        assert claims['jti'] == pair['access_token_jti']

    def test_auth_method_not_in_user_claims(self, pair):
        """auth_method is set elsewhere (in the API layer, after auth_provider
        resolves) — this service must NOT bake it into the claims here."""
        assert 'auth_method' not in pair['user_claims']

    def test_each_call_generates_unique_jti(self):
        """Two consecutive token pairs must have different JTIs — otherwise
        revocation of one would silently revoke the other."""
        a = TokenService.generate_token_pair('u', 'a', 'a@a.com', 'user', 's1')
        b = TokenService.generate_token_pair('u', 'a', 'a@a.com', 'user', 's1')
        assert a['access_token_jti'] != b['access_token_jti']


class TestGenerateRefreshToken:
    """generate_refresh_token returns a 256-bit cryptographically random URL-safe string."""

    def test_returns_urlsafe_string(self):
        token = TokenService.generate_refresh_token()
        assert isinstance(token, str)
        assert re.match(r'^[A-Za-z0-9_\-]+$', token)

    def test_token_length_matches_token_urlsafe_32(self):
        # secrets.token_urlsafe(32) returns ~43 chars (32 bytes base64url-encoded)
        token = TokenService.generate_refresh_token()
        assert len(token) >= 40

    def test_tokens_are_unique(self):
        tokens = {TokenService.generate_refresh_token() for _ in range(100)}
        assert len(tokens) == 100  # vanishingly small collision probability


class TestHashRefreshToken:
    def test_hash_is_deterministic(self):
        token = "abc123"
        assert TokenService.hash_refresh_token(token) == TokenService.hash_refresh_token(token)

    def test_different_tokens_produce_different_hashes(self):
        h1 = TokenService.hash_refresh_token("token-A")
        h2 = TokenService.hash_refresh_token("token-B")
        assert h1 != h2

    def test_hash_is_hex_sha256_length(self):
        # SHA-256 hex digest is 64 chars
        assert len(TokenService.hash_refresh_token("anything")) == 64

    def test_hash_is_lowercase_hex(self):
        h = TokenService.hash_refresh_token("anything")
        assert re.match(r'^[0-9a-f]{64}$', h)

    def test_empty_token_raises(self):
        with pytest.raises(ValueError):
            TokenService.hash_refresh_token("")

    def test_none_token_raises(self):
        with pytest.raises(ValueError):
            TokenService.hash_refresh_token(None)


class TestVerifyRefreshTokenHash:
    def test_round_trip_matches(self):
        raw = "some.jwt.string"
        stored = TokenService.hash_refresh_token(raw)
        assert TokenService.verify_refresh_token_hash(raw, stored) is True

    def test_mismatched_token_does_not_verify(self):
        stored = TokenService.hash_refresh_token("the-real-token")
        assert TokenService.verify_refresh_token_hash("a-different-token", stored) is False

    def test_empty_token_returns_false(self):
        stored = TokenService.hash_refresh_token("real")
        assert TokenService.verify_refresh_token_hash("", stored) is False

    def test_empty_stored_hash_returns_false(self):
        assert TokenService.verify_refresh_token_hash("real", "") is False

    def test_none_inputs_return_false(self):
        assert TokenService.verify_refresh_token_hash(None, "x") is False
        assert TokenService.verify_refresh_token_hash("x", None) is False

    def test_full_round_trip_with_generated_token(self):
        """Real-world flow: generate -> hash -> store -> verify."""
        raw = TokenService.generate_refresh_token()
        stored = TokenService.hash_refresh_token(raw)
        assert TokenService.verify_refresh_token_hash(raw, stored) is True
        # Tampered raw must not verify
        assert TokenService.verify_refresh_token_hash(raw + "x", stored) is False


class TestGeneratePasswordResetToken:
    def test_returns_urlsafe_string(self):
        token = TokenService.generate_password_reset_token()
        assert isinstance(token, str)
        assert re.match(r'^[A-Za-z0-9_\-]+$', token)

    def test_token_length(self):
        # secrets.token_urlsafe(32) -> ~43 chars
        token = TokenService.generate_password_reset_token()
        assert len(token) >= 40

    def test_tokens_are_unique(self):
        tokens = {TokenService.generate_password_reset_token() for _ in range(100)}
        assert len(tokens) == 100


class TestPasswordResetTokenHashing:
    """hash_password_reset_token / verify_password_reset_token mirror the refresh
    flow but live separately so the two token types can never be confused."""

    def test_hash_is_sha256_hex(self):
        h = TokenService.hash_password_reset_token("reset-token-xyz")
        assert re.match(r'^[0-9a-f]{64}$', h)

    def test_hash_empty_raises(self):
        with pytest.raises(ValueError):
            TokenService.hash_password_reset_token("")

    def test_verify_round_trip(self):
        raw = TokenService.generate_password_reset_token()
        stored = TokenService.hash_password_reset_token(raw)
        assert TokenService.verify_password_reset_token(raw, stored) is True

    def test_verify_mismatch_returns_false(self):
        stored = TokenService.hash_password_reset_token("real-reset")
        assert TokenService.verify_password_reset_token("other", stored) is False

    def test_verify_empty_inputs_return_false(self):
        assert TokenService.verify_password_reset_token("", "x") is False
        assert TokenService.verify_password_reset_token("x", "") is False
        assert TokenService.verify_password_reset_token(None, "x") is False
        assert TokenService.verify_password_reset_token("x", None) is False


class TestGenerateJti:
    def test_returns_urlsafe_string(self):
        jti = TokenService.generate_jti()
        assert isinstance(jti, str)
        assert re.match(r'^[A-Za-z0-9_\-]+$', jti)
        assert len(jti) >= 40

    def test_jtis_are_unique(self):
        jtis = {TokenService.generate_jti() for _ in range(50)}
        assert len(jtis) == 50


class TestIsTokenExpired:
    def test_naive_future_returns_false(self):
        future = datetime.utcnow() + timedelta(minutes=5)
        assert TokenService.is_token_expired(future) is False

    def test_naive_past_returns_true(self):
        past = datetime.utcnow() - timedelta(minutes=5)
        assert TokenService.is_token_expired(past) is True

    def test_tzaware_future_returns_false(self):
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        assert TokenService.is_token_expired(future) is False

    def test_tzaware_past_returns_true(self):
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert TokenService.is_token_expired(past) is True

    def test_none_returns_true(self):
        # Treat missing expiration as already-expired (fail-closed).
        assert TokenService.is_token_expired(None) is True


class TestCalculateTokenExpiration:
    """calculate_token_expiration returns datetime now + duration matching
    the per-type class constants."""

    def test_access_returns_now_plus_access_expiry(self):
        before = datetime.utcnow()
        exp = TokenService.calculate_token_expiration('access')
        after = datetime.utcnow()
        # exp should fall within [before + ACCESS, after + ACCESS]
        assert before + TokenService.ACCESS_TOKEN_EXPIRES <= exp <= after + TokenService.ACCESS_TOKEN_EXPIRES

    def test_default_token_type_is_access(self):
        before = datetime.utcnow()
        exp = TokenService.calculate_token_expiration()
        after = datetime.utcnow()
        assert before + TokenService.ACCESS_TOKEN_EXPIRES <= exp <= after + TokenService.ACCESS_TOKEN_EXPIRES

    def test_refresh_returns_now_plus_refresh_expiry(self):
        before = datetime.utcnow()
        exp = TokenService.calculate_token_expiration('refresh')
        after = datetime.utcnow()
        assert before + TokenService.REFRESH_TOKEN_EXPIRES <= exp <= after + TokenService.REFRESH_TOKEN_EXPIRES

    def test_password_reset_returns_now_plus_reset_expiry(self):
        before = datetime.utcnow()
        exp = TokenService.calculate_token_expiration('password_reset')
        after = datetime.utcnow()
        assert before + TokenService.PASSWORD_RESET_TOKEN_EXPIRES <= exp <= after + TokenService.PASSWORD_RESET_TOKEN_EXPIRES

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match='Invalid token type'):
            TokenService.calculate_token_expiration('bogus')

    def test_returns_datetime(self):
        assert isinstance(TokenService.calculate_token_expiration('access'), datetime)

    def test_default_durations_match_config(self):
        """Lock the default durations so a future regression can't silently
        shorten/lengthen access or refresh lifetimes."""
        assert TokenService.ACCESS_TOKEN_EXPIRES == timedelta(minutes=60)
        assert TokenService.REFRESH_TOKEN_EXPIRES == timedelta(days=7)
        assert TokenService.PASSWORD_RESET_TOKEN_EXPIRES == timedelta(hours=1)


class TestGetTokenExpirationSeconds:
    def test_access_returns_3600(self):
        assert TokenService.get_token_expiration_seconds('access') == 3600

    def test_default_is_access(self):
        assert TokenService.get_token_expiration_seconds() == 3600

    def test_refresh_returns_seven_days_in_seconds(self):
        assert TokenService.get_token_expiration_seconds('refresh') == 7 * 24 * 3600

    def test_password_reset_returns_one_hour_in_seconds(self):
        assert TokenService.get_token_expiration_seconds('password_reset') == 3600

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match='Invalid token type'):
            TokenService.get_token_expiration_seconds('nope')
