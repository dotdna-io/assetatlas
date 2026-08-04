# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for POST /api/auth/password-reset/request.

Focus: enumeration safety, per-IP rate limiting, audit trail for the
request endpoint. The /verify endpoint is not covered here.
"""
from unittest.mock import patch

import pytest

from src.api.utils.caching import cache
from tests.factories import make_user


_KNOWN_USER = make_user(
    id="11111111-1111-1111-1111-111111111111",
    username="alice",
    email="alice@example.com",
    is_active=True,
)
_INACTIVE_USER = make_user(
    id="22222222-2222-2222-2222-222222222222",
    username="bob",
    email="bob@example.com",
    is_active=False,
)


@pytest.fixture(autouse=True)
def _clear_cache(blueprint_app):
    """Reset rate-limit counters between tests."""
    with blueprint_app.app_context():
        cache.clear()
    yield
    with blueprint_app.app_context():
        cache.clear()


@pytest.fixture()
def reset_mocks(blueprint_app):
    """Mock repositories + audit service used by the password-reset endpoints."""
    blueprint_app.email_service.send_password_reset_email.return_value = True
    blueprint_app.email_service.send_password_changed_notification.return_value = True

    # Default settings_manager.get returns the requested default. Tests can
    # override per-call via patch.
    def _settings_get(category, key, default=None):
        return default
    blueprint_app.settings_manager.get = _settings_get

    # NOTE: request_password_reset / verify_password_reset perform their imports
    # inside the function body (not at module scope), so we must patch each
    # class at its SOURCE module — not on src.api.blueprints.auth.
    with patch("src.auth.repositories.user_repository.UserRepository") as MockUserRepo, \
         patch("src.auth.repositories.password_reset_repository.PasswordResetRepository") as MockResetRepo, \
         patch("src.auth.services.audit_service.AuditService") as MockAuditSvc, \
         patch("src.auth.services.password_reset_service.PasswordResetService") as MockResetSvc:

        user_repo = MockUserRepo.return_value
        reset_repo = MockResetRepo.return_value
        audit_svc = MockAuditSvc.return_value

        # Sensible defaults — individual tests override.
        user_repo.get_user_by_email.return_value = None  # unknown email by default
        MockResetSvc.check_rate_limit.return_value = True  # per-user limit not exceeded
        MockResetSvc.create_reset_token.return_value = {
            "token": "rawtoken",
            "token_id": "33333333-3333-3333-3333-333333333333",
            "expires_at": None,
            "user_email": "x@example.com",
            "username": "x",
        }
        MockResetSvc.TOKEN_EXPIRATION_HOURS = 1

        yield {
            "user_repo": user_repo,
            "reset_repo": reset_repo,
            "audit_svc": audit_svc,
            "reset_svc_cls": MockResetSvc,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Enumeration safety
# ─────────────────────────────────────────────────────────────────────────────

class TestPasswordResetEnumerationSafety:
    """Response body and status MUST be identical for known, unknown, and
    inactive emails. Otherwise an attacker can enumerate valid accounts."""

    EXPECTED_BODY = {
        "success": True,
        "message": "If an account exists with that email, a password reset link has been sent.",
    }

    def test_unknown_email_returns_generic_success(self, client, reset_mocks):
        reset_mocks["user_repo"].get_user_by_email.return_value = None

        resp = client.post(
            "/api/auth/password-reset/request",
            json={"email": "ghost@example.com"},
        )

        assert resp.status_code == 200
        assert resp.get_json() == self.EXPECTED_BODY

    def test_known_email_returns_generic_success(self, client, reset_mocks):
        reset_mocks["user_repo"].get_user_by_email.return_value = _KNOWN_USER

        resp = client.post(
            "/api/auth/password-reset/request",
            json={"email": _KNOWN_USER["email"]},
        )

        assert resp.status_code == 200
        assert resp.get_json() == self.EXPECTED_BODY

    def test_inactive_user_returns_generic_success(self, client, reset_mocks):
        reset_mocks["user_repo"].get_user_by_email.return_value = _INACTIVE_USER

        resp = client.post(
            "/api/auth/password-reset/request",
            json={"email": _INACTIVE_USER["email"]},
        )

        assert resp.status_code == 200
        assert resp.get_json() == self.EXPECTED_BODY


# ─────────────────────────────────────────────────────────────────────────────
# Per-IP rate limit
# ─────────────────────────────────────────────────────────────────────────────

class TestPasswordResetIPRateLimit:
    """The 6th request from a single IP within the window must be 429,
    regardless of whether the email matches a real user."""

    def _set_limit(self, blueprint_app, limit):
        def _settings_get(category, key, default=None):
            if category == "auth" and key == "password_reset_rate_limit_per_ip_per_hour":
                return limit
            return default
        blueprint_app.settings_manager.get = _settings_get

    def test_under_limit_succeeds(self, client, reset_mocks, blueprint_app):
        self._set_limit(blueprint_app, 5)

        for _ in range(5):
            resp = client.post(
                "/api/auth/password-reset/request",
                json={"email": "ghost@example.com"},
                environ_base={"REMOTE_ADDR": "10.0.0.1"},
            )
            assert resp.status_code == 200

    def test_over_limit_returns_429(self, client, reset_mocks, blueprint_app):
        self._set_limit(blueprint_app, 5)

        for _ in range(5):
            client.post(
                "/api/auth/password-reset/request",
                json={"email": "ghost@example.com"},
                environ_base={"REMOTE_ADDR": "10.0.0.2"},
            )

        resp = client.post(
            "/api/auth/password-reset/request",
            json={"email": "ghost@example.com"},
            environ_base={"REMOTE_ADDR": "10.0.0.2"},
        )
        assert resp.status_code == 429
        body = resp.get_json()
        assert body["error"] == "rate_limit_exceeded"

    def test_separate_ips_have_independent_counters(self, client, reset_mocks, blueprint_app):
        self._set_limit(blueprint_app, 5)

        for _ in range(5):
            client.post(
                "/api/auth/password-reset/request",
                json={"email": "ghost@example.com"},
                environ_base={"REMOTE_ADDR": "10.0.0.3"},
            )

        # Different IP — should still be allowed.
        resp = client.post(
            "/api/auth/password-reset/request",
            json={"email": "ghost@example.com"},
            environ_base={"REMOTE_ADDR": "10.0.0.4"},
        )
        assert resp.status_code == 200

    def test_verify_endpoint_rate_limit(self, client, reset_mocks, blueprint_app):
        """6th /verify request from one IP within the window must return 429."""
        def _settings_get(category, key, default=None):
            if category == 'auth' and key == 'token_verify_rate_limit_per_ip_per_hour':
                return 5
            return default
        blueprint_app.settings_manager.get = _settings_get

        # 5 calls under the limit (each will fail token validation but won't be rate-limited)
        for _ in range(5):
            client.post(
                "/api/auth/password-reset/verify",
                json={"token": "x" * 64, "new_password": "Strong-Pass-1!"},
                environ_base={"REMOTE_ADDR": "10.99.0.1"},
            )

        # 6th call from same IP must be 429
        resp = client.post(
            "/api/auth/password-reset/verify",
            json={"token": "x" * 64, "new_password": "Strong-Pass-1!"},
            environ_base={"REMOTE_ADDR": "10.99.0.1"},
        )
        assert resp.status_code == 429
        body = resp.get_json()
        assert body["error"] == "rate_limit_exceeded"


# ─────────────────────────────────────────────────────────────────────────────
# Audit trail
# ─────────────────────────────────────────────────────────────────────────────

class TestPasswordResetAuditTrail:
    """Audit-log entries for the request paths covered by this file:
    unknown email, inactive user, success, and per-IP rate-limit breach.
    (Per-user rate-limit, email-send failure, and generic-exception paths
    predate this PR and remain untested by this module.)"""

    def _audit_calls(self, audit_svc):
        """Return list of (kwargs) for each log_auth call."""
        return [c.kwargs for c in audit_svc.log_auth.call_args_list]

    def test_unknown_email_logs_failure(self, client, reset_mocks):
        reset_mocks["user_repo"].get_user_by_email.return_value = None

        client.post(
            "/api/auth/password-reset/request",
            json={"email": "ghost@example.com"},
        )

        calls = self._audit_calls(reset_mocks["audit_svc"])
        assert any(
            c["action"] == "password_reset_request"
            and c["success"] is False
            and c["failure_reason"] == "Email not found"
            for c in calls
        )

    def test_inactive_email_logs_failure(self, client, reset_mocks):
        reset_mocks["user_repo"].get_user_by_email.return_value = _INACTIVE_USER

        client.post(
            "/api/auth/password-reset/request",
            json={"email": _INACTIVE_USER["email"]},
        )

        calls = self._audit_calls(reset_mocks["audit_svc"])
        assert any(
            c["action"] == "password_reset_request"
            and c["success"] is False
            and c["failure_reason"] == "Account inactive"
            for c in calls
        )

    def test_successful_request_logs_success(self, client, reset_mocks, blueprint_app):
        reset_mocks["user_repo"].get_user_by_email.return_value = _KNOWN_USER

        client.post(
            "/api/auth/password-reset/request",
            json={"email": _KNOWN_USER["email"]},
        )

        calls = self._audit_calls(reset_mocks["audit_svc"])
        assert any(
            c["action"] == "password_reset_request"
            and c["success"] is True
            for c in calls
        )

    def test_ip_rate_limit_breach_logs_failure(self, client, reset_mocks, blueprint_app):
        def _settings_get(category, key, default=None):
            if category == "auth" and key == "password_reset_rate_limit_per_ip_per_hour":
                return 1
            return default
        blueprint_app.settings_manager.get = _settings_get

        client.post(
            "/api/auth/password-reset/request",
            json={"email": "x@example.com"},
            environ_base={"REMOTE_ADDR": "10.0.0.5"},
        )
        client.post(
            "/api/auth/password-reset/request",
            json={"email": "x@example.com"},
            environ_base={"REMOTE_ADDR": "10.0.0.5"},
        )

        calls = self._audit_calls(reset_mocks["audit_svc"])
        assert any(
            c["action"] == "password_reset_request"
            and c["success"] is False
            and c["failure_reason"] == "IP rate limit exceeded"
            for c in calls
        )


# ─────────────────────────────────────────────────────────────────────────────
# Forgot-password TOTP recovery (#613)
# ─────────────────────────────────────────────────────────────────────────────

class TestForgotPasswordLookup:
    def test_returns_email_path_for_any_username(self, client):
        # Always returns ['email'] — never reveals whether the user exists.
        r = client.post(
            '/api/auth/forgot-password/lookup',
            json={'username': 'nobody-here-at-all'},
        )
        assert r.status_code == 200
        assert r.json == {'paths': ['email']}

    def test_rejects_missing_username(self, client):
        r = client.post('/api/auth/forgot-password/lookup', json={})
        assert r.status_code == 400

    def test_rate_limited_per_ip(self, client, blueprint_app):
        # _enforce_token_rate_limit uses settings_manager.get(...) for the cap;
        # set it low and pound the endpoint to hit the 429.
        with blueprint_app.app_context():
            blueprint_app.settings_manager.get = lambda *a, **kw: 2
        for _ in range(2):
            r = client.post(
                '/api/auth/forgot-password/lookup',
                json={'username': 'alice'},
            )
            assert r.status_code == 200
        r = client.post(
            '/api/auth/forgot-password/lookup',
            json={'username': 'alice'},
        )
        assert r.status_code == 429


class TestForgotPasswordTotp:
    def test_resets_password_with_valid_totp(self, client):
        # Local user with TOTP enrolled, valid code, strong new password.
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.SessionRepository') as SR, \
             patch('src.api.blueprints.auth.AuditService') as AS, \
             patch('src.api.blueprints.auth.MfaService') as MS, \
             patch('src.api.blueprints.auth.PasswordService') as PS:
            UR.return_value.get_user_by_username.return_value = {
                'id': 'uid', 'username': 'alice', 'email': 'a@a.com',
                'is_active': True, 'password_hash': '$2b$12$old',
            }
            UR.is_external_user = staticmethod(lambda u: False)
            MR.return_value.list_enabled_methods.return_value = [
                {'id': 'm1', 'mfa_secret': 'ENC:x', 'label': 'Authenticator app'},
            ]
            MS.decrypt_secret.return_value = 'B32SECRET'
            MS.verify_totp.return_value = True
            PS.validate_password_strength.return_value = {'valid': True, 'errors': []}
            PS.hash_password.return_value = '$2b$12$new'
            SR.return_value.deactivate_all_user_sessions.return_value = 2

            r = client.post(
                '/api/auth/forgot-password/totp',
                json={
                    'username': 'alice',
                    'code': '123456',
                    'new_password': 'NewPass1!Aa',
                },
            )
        assert r.status_code == 200
        UR.return_value.update_password.assert_called_once_with('uid', '$2b$12$new')
        SR.return_value.deactivate_all_user_sessions.assert_called_once_with('uid')
        actions = [c.kwargs.get('action') for c in AS.return_value.log_auth.call_args_list]
        assert 'password_reset_via_totp' in actions

    def test_resets_password_with_code_matching_second_method(self, client):
        """Multi-method (#608 follow-up): the supplied code is matched
        against EVERY enrolled method's secret. A user with two TOTP
        authenticators can recover using either one."""
        from src.auth.services.mfa_service import MfaService
        import pyotp
        secret_a = MfaService.generate_secret()
        secret_b = MfaService.generate_secret()
        # Code generated from method B — matching against A first must NOT
        # short-circuit the loop.
        code_b = pyotp.TOTP(secret_b).now()

        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.SessionRepository') as SR, \
             patch('src.api.blueprints.auth.AuditService') as AS, \
             patch('src.api.blueprints.auth.PasswordService') as PS:
            UR.return_value.get_user_by_username.return_value = {
                'id': 'uid', 'username': 'alice', 'email': 'a@a.com',
                'is_active': True, 'password_hash': '$2b$12$old',
            }
            UR.is_external_user = staticmethod(lambda u: False)
            MR.return_value.list_enabled_methods.return_value = [
                {'id': 'a', 'mfa_secret': MfaService.encrypt_secret(secret_a), 'label': 'A'},
                {'id': 'b', 'mfa_secret': MfaService.encrypt_secret(secret_b), 'label': 'B'},
            ]
            PS.validate_password_strength.return_value = {'valid': True, 'errors': []}
            PS.hash_password.return_value = '$2b$12$new'
            SR.return_value.deactivate_all_user_sessions.return_value = 1

            r = client.post(
                '/api/auth/forgot-password/totp',
                json={'username': 'alice', 'code': code_b, 'new_password': 'NewPass1!Aa'},
            )
        assert r.status_code == 200, r.json
        UR.return_value.update_password.assert_called_once_with('uid', '$2b$12$new')
        actions = [c.kwargs.get('action') for c in AS.return_value.log_auth.call_args_list]
        assert 'password_reset_via_totp' in actions

    def test_returns_invalid_credentials_for_unknown_user(self, client):
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository'), \
             patch('src.api.blueprints.auth.AuditService'), \
             patch('src.api.blueprints.auth.PasswordService') as PS:
            UR.return_value.get_user_by_username.return_value = None
            PS.validate_password_strength.return_value = {'valid': True, 'errors': []}
            r = client.post(
                '/api/auth/forgot-password/totp',
                json={'username': 'nope', 'code': '123456', 'new_password': 'NewPass1!Aa'},
            )
        assert r.status_code == 400
        assert r.json['error'] == 'invalid_credentials'

    def test_returns_invalid_credentials_for_external_user(self, client):
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository'), \
             patch('src.api.blueprints.auth.AuditService'), \
             patch('src.api.blueprints.auth.PasswordService') as PS:
            UR.return_value.get_user_by_username.return_value = {
                'id': 'uid', 'username': 'alice', 'is_active': True,
                'password_hash': 'LDAP:xx',
                'email': 'a@a.com',
            }
            UR.is_external_user = staticmethod(lambda u: True)
            PS.validate_password_strength.return_value = {'valid': True, 'errors': []}
            r = client.post(
                '/api/auth/forgot-password/totp',
                json={'username': 'alice', 'code': '123456', 'new_password': 'NewPass1!Aa'},
            )
        assert r.status_code == 400
        assert r.json['error'] == 'invalid_credentials'

    def test_returns_invalid_credentials_when_no_totp_enrolled(self, client):
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.AuditService'), \
             patch('src.api.blueprints.auth.PasswordService') as PS:
            UR.return_value.get_user_by_username.return_value = {
                'id': 'uid', 'username': 'alice', 'is_active': True,
                'password_hash': '$2b$12$old', 'email': 'a@a.com',
            }
            UR.is_external_user = staticmethod(lambda u: False)
            MR.return_value.list_enabled_methods.return_value = []
            PS.validate_password_strength.return_value = {'valid': True, 'errors': []}
            r = client.post(
                '/api/auth/forgot-password/totp',
                json={'username': 'alice', 'code': '123456', 'new_password': 'NewPass1!Aa'},
            )
        assert r.status_code == 400
        assert r.json['error'] == 'invalid_credentials'

    def test_locks_out_after_threshold(self, client):
        # _clear_cache (autouse) already wipes the SimpleCache between tests,
        # so the per-IP and per-username counters start fresh here.
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.AuditService'), \
             patch('src.api.blueprints.auth.MfaService') as MS, \
             patch('src.api.blueprints.auth.PasswordService') as PS:
            UR.return_value.get_user_by_username.return_value = {
                'id': 'uid', 'username': 'alice', 'is_active': True,
                'password_hash': '$2b$12$old', 'email': 'a@a.com',
            }
            UR.is_external_user = staticmethod(lambda u: False)
            MR.return_value.list_enabled_methods.return_value = [
                {'id': 'm1', 'mfa_secret': 'ENC:x', 'label': 'Authenticator app'},
            ]
            MS.decrypt_secret.return_value = 'B32SECRET'
            MS.verify_totp.return_value = False  # always wrong
            PS.validate_password_strength.return_value = {'valid': True, 'errors': []}

            for _ in range(5):
                r = client.post(
                    '/api/auth/forgot-password/totp',
                    json={'username': 'alice', 'code': '000000', 'new_password': 'NewPass1!Aa'},
                )
                assert r.status_code == 400
            # 6th call hits the lockout
            r = client.post(
                '/api/auth/forgot-password/totp',
                json={'username': 'alice', 'code': '000000', 'new_password': 'NewPass1!Aa'},
            )
            assert r.status_code == 429

    def test_rejects_weak_password(self, client):
        # Strength check happens after TOTP verification — mock the success
        # path up to that point.
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.AuditService'), \
             patch('src.api.blueprints.auth.MfaService') as MS, \
             patch('src.api.blueprints.auth.PasswordService') as PS:
            UR.return_value.get_user_by_username.return_value = {
                'id': 'uid', 'username': 'alice', 'is_active': True,
                'password_hash': '$2b$12$old', 'email': 'a@a.com',
            }
            UR.is_external_user = staticmethod(lambda u: False)
            MR.return_value.list_enabled_methods.return_value = [
                {'id': 'm1', 'mfa_secret': 'ENC:x', 'label': 'Authenticator app'},
            ]
            MS.decrypt_secret.return_value = 'B32SECRET'
            MS.verify_totp.return_value = True
            PS.validate_password_strength.return_value = {
                'valid': False, 'errors': ['too short'],
            }
            r = client.post(
                '/api/auth/forgot-password/totp',
                json={'username': 'alice', 'code': '123456', 'new_password': 'allLowerCase'},
            )
        assert r.status_code == 400
        assert r.json['error'] == 'weak_password'
        # Update path must NOT have run.
        UR.return_value.update_password.assert_not_called()
