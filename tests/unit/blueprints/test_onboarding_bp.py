# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the onboarding endpoints (resend-invite, validate, complete)."""
from unittest.mock import patch

import pytest

from src.api.utils.caching import cache
from tests.factories import make_jwt_user


# Reuse the auth_mocks fixture pattern from test_auth_bp.py — patch the
# repository classes at the auth blueprint's module scope (Task 5 hoisted
# UserRepository, PasswordResetRepository, AuditService and InviteService to
# module-level imports there).


@pytest.fixture(autouse=True)
def _clear_cache(blueprint_app):
    """Reset rate-limit counters between tests.

    Re-primes the JWT user cache after clearing so admin-only resend-invite
    tests still authenticate (the conftest autouse ``_prime_user_cache``
    fixture sets it before this one runs, and a flat ``cache.clear()``
    would otherwise wipe it).
    """
    with blueprint_app.app_context():
        cache.clear()
        cache.set("jwt_user:test-user-id", make_jwt_user("admin"), timeout=300)
    yield
    with blueprint_app.app_context():
        cache.clear()


@pytest.fixture()
def onboard_mocks(blueprint_app):
    blueprint_app.email_service.send_invite_email.return_value = True

    def _settings_get(category, key, default=None):
        if category == 'security' and key == 'public_base_url':
            return 'https://example.test'
        if category == 'auth' and key == 'invite_token_ttl_hours':
            return 168
        return default
    blueprint_app.settings_manager.get = _settings_get

    with patch("src.api.blueprints.auth.UserRepository") as MockUserRepo, \
         patch("src.api.blueprints.auth.PasswordResetRepository") as MockResetRepo, \
         patch("src.api.blueprints.auth.AuditService") as MockAuditSvc, \
         patch("src.api.blueprints.auth.InviteService") as MockInvite:
        user_repo = MockUserRepo.return_value
        reset_repo = MockResetRepo.return_value
        audit_svc = MockAuditSvc.return_value
        MockInvite.DEFAULT_TTL_HOURS = 168
        yield {
            "user_repo": user_repo,
            "reset_repo": reset_repo,
            "audit_svc": audit_svc,
            "invite_cls": MockInvite,
        }


@pytest.fixture()
def _prime_regular_user(blueprint_app):
    """Cache a non-admin user for the JWT lookup so RBAC checks see role=user.

    The JWT user_lookup_loader in factory.py reads from cache before hitting
    the DB; pre-populating the cache lets us bypass the real UserRepository
    without patching the loader.
    """
    user_id = "test-user-id"  # default from conftest auth_headers
    fake_user = {
        "id": user_id,
        "username": "testuser",
        "email": "testuser@test.com",
        "role": "user",
        "is_active": True,
        "full_name": "Test User",
        "email_verified": True,
        "must_change_password": False,
        "last_login_at": None,
        "failed_login_attempts": 0,
        "locked_until": None,
    }
    with blueprint_app.app_context():
        cache.set(f"jwt_user:{user_id}", fake_user, timeout=300)
    yield
    with blueprint_app.app_context():
        cache.delete(f"jwt_user:{user_id}")


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/auth/users/<id>/resend-invite (admin-only)
# ─────────────────────────────────────────────────────────────────────────────

class TestResendInvite:
    def test_no_auth_returns_401(self, client):
        resp = client.post("/api/auth/users/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/resend-invite")
        assert resp.status_code == 401

    def test_user_role_returns_403(self, client, auth_headers, _prime_regular_user):
        resp = client.post(
            "/api/auth/users/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/resend-invite",
            headers=auth_headers("user"),
        )
        assert resp.status_code == 403

    def test_invalid_uuid_returns_400(self, client, auth_headers, onboard_mocks):
        resp = client.post(
            "/api/auth/users/not-a-uuid/resend-invite",
            headers=auth_headers("admin"),
        )
        assert resp.status_code == 400

    def test_user_not_found_returns_404(self, client, auth_headers, onboard_mocks):
        onboard_mocks["user_repo"].get_user_by_id.return_value = None
        resp = client.post(
            "/api/auth/users/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/resend-invite",
            headers=auth_headers("admin"),
        )
        assert resp.status_code == 404

    def test_success_returns_invite_url(self, client, auth_headers, onboard_mocks):
        from datetime import datetime, timedelta
        onboard_mocks["user_repo"].get_user_by_id.return_value = {
            "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "username": "alice",
            "email": "alice@example.com",
            "is_active": True,
        }
        onboard_mocks["invite_cls"].create_invite_token.return_value = {
            "token": "rawtoken",
            "token_id": "55555555-5555-5555-5555-555555555555",
            "expires_at": datetime.utcnow() + timedelta(hours=168),
            "user_email": "alice@example.com",
            "username": "alice",
        }

        resp = client.post(
            "/api/auth/users/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/resend-invite",
            headers=auth_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["invite_url"] == "https://example.test/onboarding?token=rawtoken"
        assert body["email_sent"] is True

    def test_inactive_user_returns_400(self, client, auth_headers, onboard_mocks):
        onboard_mocks["user_repo"].get_user_by_id.return_value = {
            "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "username": "alice",
            "email": "alice@example.com",
            "is_active": False,
        }
        onboard_mocks["invite_cls"].create_invite_token.side_effect = ValueError(
            "Cannot send invite to inactive account"
        )
        resp = client.post(
            "/api/auth/users/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/resend-invite",
            headers=auth_headers("admin"),
        )
        assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/auth/onboarding/validate (public)
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateOnboardingToken:
    def test_missing_token_returns_invalid(self, client, onboard_mocks):
        resp = client.get("/api/auth/onboarding/validate")
        assert resp.status_code == 200
        assert resp.get_json() == {
            "valid": False, "username": None, "email": None, "requires_mfa": False,
        }

    def test_valid_token_returns_user_info(self, client, onboard_mocks):
        onboard_mocks["invite_cls"].validate_invite_token.return_value = {
            "valid": True,
            "username": "alice",
            "email": "alice@example.com",
            "requires_mfa": False,
        }
        resp = client.get("/api/auth/onboarding/validate?token=goodtoken")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["valid"] is True
        assert body["username"] == "alice"
        assert body["requires_mfa"] is False

    def test_invalid_token_returns_invalid(self, client, onboard_mocks):
        onboard_mocks["invite_cls"].validate_invite_token.return_value = {
            "valid": False,
        }
        resp = client.get("/api/auth/onboarding/validate?token=badtoken")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["valid"] is False

    def test_service_exception_returns_invalid_not_500(self, client, onboard_mocks):
        onboard_mocks["invite_cls"].validate_invite_token.side_effect = RuntimeError("boom")
        resp = client.get("/api/auth/onboarding/validate?token=anything")
        assert resp.status_code == 200
        assert resp.get_json()["valid"] is False

    def test_validate_endpoint_rate_limit(self, client, onboard_mocks, blueprint_app):
        """6th /validate request from one IP within the window must return 429."""
        def _settings_get(category, key, default=None):
            if category == 'auth' and key == 'token_verify_rate_limit_per_ip_per_hour':
                return 5
            if category == 'security' and key == 'public_base_url':
                return 'https://example.test'
            return default
        blueprint_app.settings_manager.get = _settings_get

        for _ in range(5):
            client.get(
                "/api/auth/onboarding/validate?token=anything",
                environ_base={"REMOTE_ADDR": "10.99.0.2"},
            )

        resp = client.get(
            "/api/auth/onboarding/validate?token=anything",
            environ_base={"REMOTE_ADDR": "10.99.0.2"},
        )
        assert resp.status_code == 429
        assert resp.get_json()["error"] == "rate_limit_exceeded"


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/auth/onboarding/complete (public)
# ─────────────────────────────────────────────────────────────────────────────

class TestCompleteOnboarding:
    def test_missing_body_returns_400(self, client, onboard_mocks):
        resp = client.post("/api/auth/onboarding/complete", json={})
        assert resp.status_code == 400

    def test_invalid_token_returns_400(self, client, onboard_mocks):
        onboard_mocks["invite_cls"].consume_invite_token.side_effect = ValueError(
            "Invalid or expired invite token"
        )
        resp = client.post(
            "/api/auth/onboarding/complete",
            json={"token": "x" * 64, "new_password": "Strong-Pass-123!"},
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["error"] == "invalid_token_or_password"

    def test_weak_password_returns_400(self, client, onboard_mocks):
        onboard_mocks["invite_cls"].consume_invite_token.side_effect = ValueError(
            "Password does not meet requirements: too short"
        )
        resp = client.post(
            "/api/auth/onboarding/complete",
            json={"token": "y" * 64, "new_password": "Strong-Pass-123!"},
        )
        assert resp.status_code == 400

    def test_success_returns_username(self, client, onboard_mocks):
        onboard_mocks["invite_cls"].consume_invite_token.return_value = {
            "success": True,
            "user_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "username": "alice",
        }
        resp = client.post(
            "/api/auth/onboarding/complete",
            json={"token": "z" * 64, "new_password": "Strong-Pass-123!"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["username"] == "alice"

    def test_complete_endpoint_rate_limit(self, client, onboard_mocks, blueprint_app):
        """6th /complete request from one IP within the window must return 429."""
        def _settings_get(category, key, default=None):
            if category == 'auth' and key == 'token_verify_rate_limit_per_ip_per_hour':
                return 5
            return default
        blueprint_app.settings_manager.get = _settings_get
        onboard_mocks["invite_cls"].consume_invite_token.side_effect = ValueError(
            "Invalid or expired invite token"
        )

        for _ in range(5):
            client.post(
                "/api/auth/onboarding/complete",
                json={"token": "x" * 64, "new_password": "Strong-Pass-1!"},
                environ_base={"REMOTE_ADDR": "10.99.0.3"},
            )

        resp = client.post(
            "/api/auth/onboarding/complete",
            json={"token": "x" * 64, "new_password": "Strong-Pass-1!"},
            environ_base={"REMOTE_ADDR": "10.99.0.3"},
        )
        assert resp.status_code == 429
        assert resp.get_json()["error"] == "rate_limit_exceeded"
