# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the auth blueprint.

The auth blueprint is SPECIAL: it bypasses the service layer and instantiates
UserRepository, SessionRepository, and AuditService directly from
current_app.connection_manager.  We must patch those classes at the module level.

Endpoints tested:
- POST   /api/auth/login                — authenticate + get tokens
- POST   /api/auth/register             — register new user
- POST   /api/auth/logout               — revoke session
- GET    /api/auth/me                   — get current user info
- POST   /api/auth/refresh              — refresh access token
- GET    /api/auth/session-config       — public session config
- GET    /api/auth/users                — list users (admin)
- POST   /api/auth/users                — create user (admin)
- PUT    /api/auth/users/<id>           — update user (admin)
- PATCH  /api/auth/users/<id>/disable   — disable user (admin)
- PATCH  /api/auth/users/<id>/enable    — enable user (admin)
- DELETE /api/auth/users/<id>           — delete user (admin)
- GET    /api/auth/users/<id>           — get user (self or admin)
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

from src.api.utils.caching import cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEST_USER_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _make_user(role="admin", user_id=TEST_USER_UUID, username="testuser"):
    return {
        "id": user_id,
        "username": username,
        "role": role,
        "is_active": True,
        "email": f"{username}@test.com",
        "full_name": "Test User",
        "email_verified": False,
        "must_change_password": False,
        "last_login_at": None,
        "created_at": datetime(2025, 1, 1),
    }


def _make_db_user(**overrides):
    """Return a full user dict as would come from the database."""
    base = {
        "id": "user-uuid-1",
        "username": "testuser",
        "email": "test@example.com",
        "full_name": "Test User",
        "role": "user",
        "is_active": True,
        "email_verified": False,
        "must_change_password": False,
        "password_hash": "$2b$12$fakehashfakehashfakehash",
        "last_login_at": None,
        "created_at": datetime(2025, 1, 1),
        "failed_login_attempts": 0,
        "locked_until": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _prime_user_cache(blueprint_app):
    """Put an admin user dict in the JWT cache for authenticated tests."""
    with blueprint_app.app_context():
        cache.set(f"jwt_user:{TEST_USER_UUID}", _make_user("admin"), timeout=300)
    yield
    with blueprint_app.app_context():
        cache.delete(f"jwt_user:{TEST_USER_UUID}")


@pytest.fixture()
def _prime_regular_user(blueprint_app):
    """Override cache with a regular (non-admin) user for RBAC tests."""
    with blueprint_app.app_context():
        cache.set(f"jwt_user:{TEST_USER_UUID}", _make_user("user"), timeout=300)
    yield
    with blueprint_app.app_context():
        cache.set(f"jwt_user:{TEST_USER_UUID}", _make_user("admin"), timeout=300)


@pytest.fixture()
def auth_headers(auth_headers):
    """Wrap the conftest auth_headers fixture to use a valid UUID by default."""
    def _make(role="admin", username="testuser", user_id=TEST_USER_UUID):
        return auth_headers(role=role, username=username, user_id=user_id)
    return _make


# Patch the repositories and services at the module level where auth.py
# imports them.  Each test method gets the mock instances.
@pytest.fixture()
def auth_mocks():
    """Patch UserRepository, SessionRepository, AuditService, PasswordService,
    TokenService, and UserMfaRepository at the module level so that auth.py uses
    mocks instead of real DB classes."""
    with patch("src.api.blueprints.auth.UserRepository") as MockUserRepo, \
         patch("src.api.blueprints.auth.SessionRepository") as MockSessionRepo, \
         patch("src.api.blueprints.auth.AuditService") as MockAuditSvc, \
         patch("src.api.blueprints.auth.PasswordService") as MockPwdSvc, \
         patch("src.api.blueprints.auth.TokenService") as MockTokenSvc, \
         patch("src.api.blueprints.auth.UserMfaRepository") as MockMfaRepo:
        user_repo = MockUserRepo.return_value
        session_repo = MockSessionRepo.return_value
        audit_svc = MockAuditSvc.return_value
        mfa_repo = MockMfaRepo.return_value
        # Default: zero enrolled MFA methods. login() flips the user into the
        # enrollment-required branch when this is 0; tests that need the
        # MFA-challenge branch override it to a positive value.
        mfa_repo.count_enabled_methods.return_value = 0
        mfa_repo.list_enabled_methods.return_value = []
        mfa_repo.list_methods.return_value = []
        mfa_repo.get_pending_method.return_value = None
        yield {
            "UserRepoCls": MockUserRepo,
            "user_repo": user_repo,
            "SessionRepoCls": MockSessionRepo,
            "session_repo": session_repo,
            "AuditSvcCls": MockAuditSvc,
            "audit_svc": audit_svc,
            "PasswordService": MockPwdSvc,
            "TokenService": MockTokenSvc,
            "MfaRepoCls": MockMfaRepo,
            "mfa_repo": mfa_repo,
        }


# =============================================================================
# Login (POST /api/auth/login)
# =============================================================================

class TestLogin:
    """POST /api/auth/login"""

    def test_login_success_returns_enrollment_challenge(self, client, auth_mocks, blueprint_app):
        """Correct credentials for a local user without MFA → enrollment challenge (not tokens).

        Under mandatory MFA enforcement, local users must enroll before getting
        full session tokens. The enrollment wizard is driven with the returned
        mfa_enrollment_token.
        """
        db_user = _make_db_user()
        auth_mocks["user_repo"].get_user_by_username.return_value = db_user
        auth_mocks["PasswordService"].verify_password.return_value = True
        # MFA fixture default: zero enrolled methods (count_enabled_methods returns 0)

        resp = client.post("/api/auth/login", json={
            "username": "testuser",
            "password": "TestPass123!",
        })

        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get("requires_mfa_enrollment") is True
        assert "mfa_enrollment_token" in body
        assert body.get("expires_in") == 300
        assert "access_token" not in body

    def test_login_wrong_password(self, client, auth_mocks):
        db_user = _make_db_user()
        auth_mocks["user_repo"].get_user_by_username.return_value = db_user
        auth_mocks["PasswordService"].verify_password.return_value = False

        resp = client.post("/api/auth/login", json={
            "username": "testuser",
            "password": "wrong",
        })

        assert resp.status_code == 401
        body = resp.get_json()
        assert body["error"] == "authentication_failed"

    def test_login_user_not_found(self, client, auth_mocks):
        auth_mocks["user_repo"].get_user_by_username.return_value = None
        auth_mocks["user_repo"].get_user_by_email.return_value = None

        resp = client.post("/api/auth/login", json={
            "username": "nonexistent",
            "password": "anything",
        })

        assert resp.status_code == 401
        body = resp.get_json()
        assert body["error"] == "authentication_failed"

    def test_login_account_disabled(self, client, auth_mocks):
        db_user = _make_db_user(is_active=False)
        auth_mocks["user_repo"].get_user_by_username.return_value = db_user

        resp = client.post("/api/auth/login", json={
            "username": "testuser",
            "password": "TestPass123!",
        })

        assert resp.status_code == 401
        body = resp.get_json()
        assert body["error"] == "account_disabled"

    def test_login_account_locked(self, client, auth_mocks):
        db_user = _make_db_user(
            failed_login_attempts=5,
            locked_until=datetime.utcnow() + timedelta(hours=1),
        )
        auth_mocks["user_repo"].get_user_by_username.return_value = db_user

        resp = client.post("/api/auth/login", json={
            "username": "testuser",
            "password": "TestPass123!",
        })

        assert resp.status_code == 401
        body = resp.get_json()
        assert body["error"] == "account_locked"

    def test_login_missing_fields(self, client, auth_mocks):
        resp = client.post("/api/auth/login", json={})
        assert resp.status_code == 400

    def test_login_missing_password(self, client, auth_mocks):
        resp = client.post("/api/auth/login", json={"username": "testuser"})
        assert resp.status_code == 400

    def test_login_by_email_fallback(self, client, auth_mocks, blueprint_app):
        """If username lookup fails, the endpoint tries email lookup."""
        db_user = _make_db_user()
        auth_mocks["user_repo"].get_user_by_username.return_value = None
        auth_mocks["user_repo"].get_user_by_email.return_value = db_user
        auth_mocks["PasswordService"].verify_password.return_value = True
        auth_mocks["session_repo"].create_session.return_value = "session-uuid-1"
        auth_mocks["TokenService"].generate_token_pair.return_value = {
            "access_token_jti": "jti-1",
            "user_claims": {"role": "user", "username": "testuser"},
        }
        auth_mocks["TokenService"].generate_refresh_token.return_value = "raw-refresh"
        auth_mocks["TokenService"].hash_refresh_token.return_value = "hashed-refresh"
        auth_mocks["TokenService"].calculate_token_expiration.return_value = (
            datetime.utcnow() + timedelta(days=7)
        )

        resp = client.post("/api/auth/login", json={
            "username": "test@example.com",
            "password": "TestPass123!",
        })

        assert resp.status_code == 200
        auth_mocks["user_repo"].get_user_by_email.assert_called_once()


# =============================================================================
# Register (POST /api/auth/register)
# =============================================================================

class TestRegister:
    """POST /api/auth/register"""

    def test_register_success(self, client, auth_headers, auth_mocks):
        auth_mocks["user_repo"].get_user_by_username.return_value = None
        auth_mocks["user_repo"].get_user_by_email.return_value = None
        auth_mocks["PasswordService"].hash_password.return_value = "hashed-pw"
        auth_mocks["user_repo"].create_user.return_value = "new-user-id"
        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id="new-user-id",
            username="newuser",
            email="new@example.com",
        )

        resp = client.post("/api/auth/register", json={
            "username": "newuser",
            "email": "new@example.com",
            "password": "StrongPass123!",
        }, headers=auth_headers("admin"))

        assert resp.status_code == 201
        body = resp.get_json()
        assert body["message"] == "User registered successfully"
        assert "user" in body

    def test_register_duplicate_username(self, client, auth_headers, auth_mocks):
        auth_mocks["user_repo"].get_user_by_username.return_value = _make_db_user()

        resp = client.post("/api/auth/register", json={
            "username": "testuser",
            "email": "new@example.com",
            "password": "StrongPass123!",
        }, headers=auth_headers("admin"))

        assert resp.status_code == 400
        assert resp.get_json()["error"] == "username_exists"

    def test_register_duplicate_email(self, client, auth_headers, auth_mocks):
        auth_mocks["user_repo"].get_user_by_username.return_value = None
        auth_mocks["user_repo"].get_user_by_email.return_value = _make_db_user()

        resp = client.post("/api/auth/register", json={
            "username": "brandnew",
            "email": "test@example.com",
            "password": "StrongPass123!",
        }, headers=auth_headers("admin"))

        assert resp.status_code == 409
        assert resp.get_json()["error"] == "email_exists"

    def test_register_missing_fields(self, client, auth_headers, auth_mocks):
        resp = client.post("/api/auth/register", json={"username": "x"}, headers=auth_headers("admin"))
        assert resp.status_code == 400


# =============================================================================
# Logout (POST /api/auth/logout)
# =============================================================================

class TestLogout:
    """POST /api/auth/logout -- requires JWT."""

    def test_logout_requires_auth(self, client):
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 401

    def test_logout_success(self, client, auth_headers, auth_mocks):
        auth_mocks["session_repo"].get_session_by_access_jti.return_value = {
            "id": "session-1"
        }

        resp = client.post("/api/auth/logout", headers=auth_headers("admin"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["message"] == "Logout successful"

    def test_logout_no_session_still_succeeds(self, client, auth_headers, auth_mocks):
        """Even if no session is found, logout should succeed gracefully."""
        auth_mocks["session_repo"].get_session_by_access_jti.return_value = None

        resp = client.post("/api/auth/logout", headers=auth_headers("admin"))
        assert resp.status_code == 200

    def _oidc_token_headers(self, blueprint_app, user_id=TEST_USER_UUID, role="admin"):
        """Build headers with an access token carrying auth_method='oidc'."""
        from flask_jwt_extended import create_access_token
        with blueprint_app.app_context():
            token = create_access_token(
                identity=user_id,
                additional_claims={
                    "role": role,
                    "username": "testuser",
                    "auth_method": "oidc",
                },
            )
        return {"Authorization": f"Bearer {token}"}

    def test_logout_returns_oidc_logout_url_for_oidc_users(
        self, client, blueprint_app, auth_mocks
    ):
        """OIDC users get an oidc_logout_url so the UI can redirect to the IdP's
        end_session_endpoint and terminate the IdP session as well."""
        auth_mocks["session_repo"].get_session_by_access_jti.return_value = {"id": "s1"}
        with patch("src.api.blueprints.auth.OidcService") as MockSvc:
            MockSvc.return_value.build_logout_url.return_value = (
                "https://login.microsoftonline.com/t/oauth2/v2.0/logout"
                "?post_logout_redirect_uri=https%3A%2F%2Fapp.example.com%2Flogin"
            )
            resp = client.post(
                "/api/auth/logout",
                headers=self._oidc_token_headers(blueprint_app),
            )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["message"] == "Logout successful"
        assert "oidc_logout_url" in body
        assert body["oidc_logout_url"].startswith(
            "https://login.microsoftonline.com/"
        )

    def test_logout_omits_oidc_logout_url_for_local_users(
        self, client, auth_headers, auth_mocks
    ):
        """Local-auth users must not receive an oidc_logout_url even if RP-init
        is configured — they never had an IdP session in the first place."""
        auth_mocks["session_repo"].get_session_by_access_jti.return_value = {"id": "s1"}
        with patch("src.api.blueprints.auth.OidcService") as MockSvc:
            # Even if build_logout_url WOULD return something, we don't ask.
            MockSvc.return_value.build_logout_url.return_value = "should-not-appear"
            resp = client.post("/api/auth/logout", headers=auth_headers("admin"))

        assert resp.status_code == 200
        body = resp.get_json()
        assert "oidc_logout_url" not in body
        # Service must not be consulted for non-OIDC sessions
        MockSvc.return_value.build_logout_url.assert_not_called()

    def test_logout_omits_oidc_logout_url_when_rp_init_not_configured(
        self, client, blueprint_app, auth_mocks
    ):
        """If post_logout_redirect_uri is empty (build_logout_url returns None),
        the response is the standard logout body — not an error."""
        auth_mocks["session_repo"].get_session_by_access_jti.return_value = {"id": "s1"}
        with patch("src.api.blueprints.auth.OidcService") as MockSvc:
            MockSvc.return_value.build_logout_url.return_value = None
            resp = client.post(
                "/api/auth/logout",
                headers=self._oidc_token_headers(blueprint_app),
            )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["message"] == "Logout successful"
        assert "oidc_logout_url" not in body


# =============================================================================
# Get current user (GET /api/auth/me)
# =============================================================================

class TestGetMe:
    """GET /api/auth/me -- requires JWT."""

    def test_me_requires_auth(self, client):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_me_returns_user(self, client, auth_headers):
        """Primed cache user should be returned via /me."""
        resp = client.get("/api/auth/me", headers=auth_headers("admin"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["username"] == "testuser"
        assert body["role"] == "admin"


# =============================================================================
# Session config (GET /api/auth/session-config)
# =============================================================================

class TestSessionConfig:
    """GET /api/auth/session-config -- public, no auth needed."""

    def test_session_config_returns_timeout(self, client, blueprint_app):
        resp = client.get("/api/auth/session-config")
        assert resp.status_code == 200
        body = resp.get_json()
        # settings_manager.get returns the default (None) per conftest mock
        assert "session_timeout_minutes" in body


# =============================================================================
# Refresh token (POST /api/auth/refresh)
# =============================================================================

class TestRefreshToken:
    """POST /api/auth/refresh -- requires a valid refresh JWT."""

    def test_refresh_requires_auth(self, client):
        resp = client.post("/api/auth/refresh")
        assert resp.status_code == 401

    def test_refresh_success(self, client, auth_mocks, blueprint_app):
        """Use a proper refresh token to get a new access token."""
        from flask_jwt_extended import create_refresh_token

        # Create a refresh token with session_id claim
        with blueprint_app.app_context():
            refresh_tok = create_refresh_token(
                identity=TEST_USER_UUID,
                additional_claims={"session_id": "session-1"},
            )

        # Mock all the DB lookups the refresh endpoint performs
        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id=TEST_USER_UUID
        )
        auth_mocks["session_repo"].get_session_by_id.return_value = {
            "id": "session-1",
            "is_active": True,
        }

        resp = client.post(
            "/api/auth/refresh",
            headers={"Authorization": f"Bearer {refresh_tok}"},
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["token_type"] == "Bearer"

    def test_refresh_inactive_user(self, client, auth_mocks, blueprint_app):
        """Refreshing a token for a disabled user should fail."""
        from flask_jwt_extended import create_refresh_token

        with blueprint_app.app_context():
            refresh_tok = create_refresh_token(
                identity=TEST_USER_UUID,
                additional_claims={"session_id": "session-1"},
            )

        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id=TEST_USER_UUID, is_active=False
        )

        resp = client.post(
            "/api/auth/refresh",
            headers={"Authorization": f"Bearer {refresh_tok}"},
        )

        assert resp.status_code == 401
        assert resp.get_json()["error"] == "account_disabled"

    def test_refresh_invalid_session(self, client, auth_mocks, blueprint_app):
        """Refresh with a revoked session should fail."""
        from flask_jwt_extended import create_refresh_token

        with blueprint_app.app_context():
            refresh_tok = create_refresh_token(
                identity=TEST_USER_UUID,
                additional_claims={"session_id": "session-1"},
            )

        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id=TEST_USER_UUID
        )
        auth_mocks["session_repo"].get_session_by_id.return_value = None

        resp = client.post(
            "/api/auth/refresh",
            headers={"Authorization": f"Bearer {refresh_tok}"},
        )

        assert resp.status_code == 401


# =============================================================================
# Refresh-token reuse detection (JWT revocation callback)
# =============================================================================

class TestRefreshTokenReuseDetection:
    """check_if_token_revoked must reject refresh tokens whose hash no longer
    matches user_sessions.refresh_token_hash (RFC 6819 §5.2.2.3)."""

    def _get_callback(self, blueprint_app):
        """Return the registered token_in_blocklist callback."""
        return blueprint_app.extensions["flask-jwt-extended"]._token_in_blocklist_callback

    @staticmethod
    def _closure_var(callback, name):
        """Resolve a free-var by name (robust to free-var ordering changes)."""
        idx = callback.__code__.co_freevars.index(name)
        return callback.__closure__[idx].cell_contents

    def _build_payload(self, session_id="session-1", token_type="refresh"):
        return {
            "type": token_type,
            "session_id": session_id,
            "jti": "test-jti",
        }

    def test_inbound_hash_mismatch_revokes_session(self, blueprint_app):
        """An old (rotated-out) refresh token must be rejected and the
        session deactivated."""
        from src.auth.services.token_service import TokenService

        callback = self._get_callback(blueprint_app)
        session_repo = self._closure_var(callback, "session_repo")

        old_raw = "old.jwt.token"
        new_raw = "new.jwt.token"
        # session.refresh_token_hash reflects the NEW (rotated) token
        session_dict = {
            "id": "session-1",
            "user_id": "user-uuid-1",
            "is_active": True,
            "refresh_token_hash": TokenService.hash_refresh_token(new_raw),
            "expires_at": None,
        }

        with patch.object(session_repo, "get_session", return_value=session_dict), \
             patch.object(session_repo, "deactivate_session") as deact, \
             patch("src.auth.services.audit_service.AuditService") as MockAudit:
            audit_instance = MockAudit.return_value

            with blueprint_app.test_request_context(
                "/api/auth/refresh",
                headers={"Authorization": f"Bearer {old_raw}"},
            ):
                revoked = callback({}, self._build_payload())

        assert revoked is True
        deact.assert_called_once_with("session-1")
        audit_instance.log_auth.assert_called_once()
        kwargs = audit_instance.log_auth.call_args.kwargs
        assert kwargs["action"] == "refresh_token_reuse_detected"
        assert kwargs["success"] is False
        assert kwargs["failure_reason"] == "session_revoked_for_safety"
        assert kwargs["user_id"] == "user-uuid-1"
        assert kwargs["metadata"] == {"session_id": "session-1"}

    def test_inbound_hash_match_does_not_revoke(self, blueprint_app):
        """A current (legitimate) refresh token whose hash matches the
        stored hash must be accepted."""
        from src.auth.services.token_service import TokenService

        callback = self._get_callback(blueprint_app)
        session_repo = self._closure_var(callback, "session_repo")

        raw = "current.jwt.token"
        session_dict = {
            "id": "session-1",
            "user_id": "user-uuid-1",
            "is_active": True,
            "refresh_token_hash": TokenService.hash_refresh_token(raw),
            "expires_at": None,
        }

        with patch.object(session_repo, "get_session", return_value=session_dict), \
             patch.object(session_repo, "deactivate_session") as deact:
            with blueprint_app.test_request_context(
                "/api/auth/refresh",
                headers={"Authorization": f"Bearer {raw}"},
            ):
                revoked = callback({}, self._build_payload())

        assert revoked is False
        deact.assert_not_called()

    def test_missing_authorization_header_revokes(self, blueprint_app):
        """Defensive: if no Authorization header is present we cannot verify
        the token, so we revoke."""
        callback = self._get_callback(blueprint_app)
        session_repo = self._closure_var(callback, "session_repo")

        session_dict = {
            "id": "session-1",
            "user_id": "user-uuid-1",
            "is_active": True,
            "refresh_token_hash": "some-hash",
            "expires_at": None,
        }

        with patch.object(session_repo, "get_session", return_value=session_dict):
            # No Authorization header on the request context
            with blueprint_app.test_request_context("/api/auth/refresh"):
                revoked = callback({}, self._build_payload())

        assert revoked is True


# =============================================================================
# Login-path refresh-token hash consistency (regression for refresh_token_raw bug)
# =============================================================================

class TestLoginRefreshTokenHashConsistency:
    """The hash stored in user_sessions.refresh_token_hash MUST equal
    hash_refresh_token(<the JWT returned to the client>). Otherwise the new
    RFC 6819 reuse-detection callback rejects every freshly-issued refresh
    token as a "reuse" because the hashes never match.

    Earlier code generated a throwaway random string, hashed THAT, and
    returned an unrelated JWT — silently breaking reuse detection for any
    session created via login (vs rotation).
    """

    def test_login_stores_hash_of_returned_refresh_token(self, client, blueprint_app):
        """Refresh token hash stored in DB must match the one returned to the client.

        Uses an LDAP-authenticated user so the login goes straight to token
        issuance (LDAP users bypass MFA enrollment — their IdP handles MFA).
        """
        from src.auth.services.token_service import TokenService
        from src.auth.services.ldap_service import LdapUserInfo

        # Patch only the DB-touching collaborators. Leave TokenService and
        # create_refresh_token alone so the real hash flow runs end-to-end.
        with patch("src.api.blueprints.auth.UserRepository") as MockUserRepo, \
             patch("src.api.blueprints.auth.SessionRepository") as MockSessionRepo, \
             patch("src.api.blueprints.auth.AuditService"), \
             patch("src.api.blueprints.auth.PasswordService") as MockPwdSvc, \
             patch("src.api.blueprints.auth.UserMfaRepository") as MockMfaRepo, \
             patch("src.api.blueprints.auth.LdapService") as MockLdapSvc:

            user_repo = MockUserRepo.return_value
            session_repo = MockSessionRepo.return_value
            mfa_repo = MockMfaRepo.return_value
            ldap_svc = MockLdapSvc.return_value

            # LDAP authenticates the user — bypasses local MFA enforcement.
            ldap_svc.is_enabled.return_value = True
            ldap_svc.authenticate.return_value = LdapUserInfo(
                username='testuser', user_dn='CN=testuser,DC=corp,DC=com',
                email='test@example.com', full_name='Test User', role='user', groups=[],
            )
            user_repo.get_or_create_ldap_user.return_value = _make_db_user()
            mfa_repo.count_enabled_methods.return_value = 0  # no local MFA — LDAP user doesn't enroll local TOTP
            session_repo.create_session.return_value = "session-uuid-1"

            resp = client.post("/api/auth/login", json={
                "username": "testuser",
                "password": "TestPass123!",
            })

            assert resp.status_code == 200, resp.get_json()
            body = resp.get_json()
            returned_refresh = body["refresh_token"]

            # The hash that auth.py asked the repo to persist
            session_repo.store_refresh_token.assert_called_once()
            stored_hash = session_repo.store_refresh_token.call_args.kwargs[
                "refresh_token_hash"
            ]

            # MUST equal hash of the JWT actually handed to the client,
            # otherwise the reuse-detection callback will revoke the next
            # legitimate refresh.
            assert stored_hash == TokenService.hash_refresh_token(returned_refresh)


# =============================================================================
# User management -- auth / RBAC gates
# =============================================================================

class TestUserManagementAuth:
    """Admin-only user management endpoints enforce RBAC."""

    def test_list_users_requires_auth(self, client):
        resp = client.get("/api/auth/users")
        assert resp.status_code == 401

    def test_list_users_forbidden_for_user(self, client, auth_headers, _prime_regular_user):
        resp = client.get("/api/auth/users", headers=auth_headers("user"))
        assert resp.status_code == 403

    def test_create_user_requires_auth(self, client):
        resp = client.post("/api/auth/users", json={"username": "x"})
        assert resp.status_code == 401

    def test_create_user_forbidden_for_user(self, client, auth_headers, _prime_regular_user):
        resp = client.post(
            "/api/auth/users",
            json={"username": "x", "email": "x@x.com", "password": "P@ss1234"},
            headers=auth_headers("user"),
        )
        assert resp.status_code == 403

    def test_disable_user_requires_auth(self, client):
        resp = client.patch("/api/auth/users/some-id/disable")
        assert resp.status_code == 401

    def test_enable_user_requires_auth(self, client):
        resp = client.patch("/api/auth/users/some-id/enable")
        assert resp.status_code == 401

    def test_delete_user_requires_auth(self, client):
        resp = client.delete("/api/auth/users/some-id")
        assert resp.status_code == 401

    def test_user_stats_requires_auth(self, client):
        resp = client.get("/api/auth/users/stats")
        assert resp.status_code == 401


# =============================================================================
# List users (GET /api/auth/users) -- admin only
# =============================================================================

class TestListUsers:
    """GET /api/auth/users"""

    def test_list_users_success(self, client, auth_headers, auth_mocks):
        auth_mocks["user_repo"].list_users.return_value = {
            "users": [_make_db_user(created_at=datetime(2025, 1, 1))],
            "total": 1,
            "page": 1,
            "limit": 50,
            "pages": 1,
        }

        resp = client.get("/api/auth/users", headers=auth_headers("admin"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["total"] == 1
        assert len(body["users"]) == 1


# =============================================================================
# Create user (POST /api/auth/users) -- admin only
# =============================================================================

class TestCreateUser:
    """POST /api/auth/users"""

    def test_create_user_success(self, client, auth_headers, auth_mocks):
        auth_mocks["user_repo"].get_user_by_username.return_value = None
        auth_mocks["user_repo"].get_user_by_email.return_value = None
        auth_mocks["PasswordService"].validate_password_strength.return_value = {
            "valid": True, "errors": []
        }
        auth_mocks["PasswordService"].hash_password.return_value = "hashed"
        new_user = _make_db_user(
            id="new-user-1", username="newadmin", created_at=datetime(2025, 1, 1)
        )
        auth_mocks["user_repo"].create_user.return_value = new_user
        auth_mocks["user_repo"].get_user_by_id.return_value = new_user

        resp = client.post("/api/auth/users", json={
            "username": "newadmin",
            "email": "newadmin@example.com",
            "password": "StrongPass123!",
            "role": "admin",
        }, headers=auth_headers("admin"))

        assert resp.status_code == 201

    def test_create_user_audit_metadata_includes_role(self, client, auth_headers, auth_mocks):
        """#883: the manual create-user user_created audit row must carry the assigned
        role in metadata, so the seeded auth-privileged-account-created alert rule —
        which fires on metadata.role IN ('admin','super_admin') — covers manual
        privileged-account creation, not just SSO auto-provisioning. Without the role
        key the rule can never see a manually-created admin, so this pins it present."""
        auth_mocks["user_repo"].get_user_by_username.return_value = None
        auth_mocks["user_repo"].get_user_by_email.return_value = None
        auth_mocks["PasswordService"].validate_password_strength.return_value = {
            "valid": True, "errors": []
        }
        auth_mocks["PasswordService"].hash_password.return_value = "hashed"
        new_user = _make_db_user(
            id="new-admin-1", username="newadmin", role="admin",
            created_at=datetime(2025, 1, 1)
        )
        auth_mocks["user_repo"].create_user.return_value = new_user
        auth_mocks["user_repo"].get_user_by_id.return_value = new_user

        # An admin creating an admin is allowed (equal level, not an escalation).
        resp = client.post("/api/auth/users", json={
            "username": "newadmin",
            "email": "newadmin@example.com",
            "password": "StrongPass123!",
            "role": "admin",
        }, headers=auth_headers("admin"))

        assert resp.status_code == 201
        created = [c for c in auth_mocks["audit_svc"].log_auth.call_args_list
                   if c.kwargs.get("action") == "user_created"]
        assert len(created) == 1
        assert created[0].kwargs["metadata"]["role"] == "admin"

    def test_create_user_duplicate_username(self, client, auth_headers, auth_mocks):
        auth_mocks["user_repo"].get_user_by_username.return_value = _make_db_user()

        resp = client.post("/api/auth/users", json={
            "username": "testuser",
            "email": "new@example.com",
            "password": "StrongPass123!",
        }, headers=auth_headers("admin"))

        assert resp.status_code == 400
        assert resp.get_json()["error"] == "username_exists"

    def test_create_user_sends_welcome_email(self, client, auth_headers, auth_mocks, blueprint_app):
        mock_email = MagicMock()
        mock_email.send_welcome_email.return_value = True
        blueprint_app.email_service = mock_email

        auth_mocks["user_repo"].get_user_by_username.return_value = None
        auth_mocks["user_repo"].get_user_by_email.return_value = None
        auth_mocks["PasswordService"].validate_password_strength.return_value = {
            "valid": True, "errors": []
        }
        auth_mocks["PasswordService"].hash_password.return_value = "hashed"
        new_user = _make_db_user(
            id="new-user-2", username="newuser", email="new@example.com",
            created_at=datetime(2025, 1, 1)
        )
        auth_mocks["user_repo"].create_user.return_value = new_user
        auth_mocks["user_repo"].get_user_by_id.return_value = new_user

        resp = client.post("/api/auth/users", json={
            "username": "newuser",
            "email": "new@example.com",
            "password": "StrongPass!1",
        }, headers=auth_headers("admin"))

        assert resp.status_code == 201
        mock_email.send_welcome_email.assert_called_once()
        kwargs = mock_email.send_welcome_email.call_args.kwargs
        assert kwargs.get("to_email") == "new@example.com"
        assert kwargs.get("username") == "newuser"

    def test_create_user_succeeds_when_welcome_email_fails(self, client, auth_headers, auth_mocks, blueprint_app):
        mock_email = MagicMock()
        mock_email.send_welcome_email.side_effect = RuntimeError("SMTP exploded")
        blueprint_app.email_service = mock_email

        auth_mocks["user_repo"].get_user_by_username.return_value = None
        auth_mocks["user_repo"].get_user_by_email.return_value = None
        auth_mocks["PasswordService"].validate_password_strength.return_value = {
            "valid": True, "errors": []
        }
        auth_mocks["PasswordService"].hash_password.return_value = "hashed"
        new_user = _make_db_user(
            id="new-user-3", username="newuser", email="new@example.com",
            created_at=datetime(2025, 1, 1)
        )
        auth_mocks["user_repo"].create_user.return_value = new_user
        auth_mocks["user_repo"].get_user_by_id.return_value = new_user

        resp = client.post("/api/auth/users", json={
            "username": "newuser",
            "email": "new@example.com",
            "password": "StrongPass!1",
        }, headers=auth_headers("admin"))

        assert resp.status_code == 201  # user creation must not break

    def test_create_user_invite_only_omits_password(self, client, auth_mocks, auth_headers, blueprint_app):
        """invite_only=True must not require a password and must include invite_url in response."""
        with patch("src.api.blueprints.auth.InviteService") as MockInvite, \
             patch("src.api.blueprints.auth.PasswordResetRepository"):
            from datetime import datetime as _dt, timedelta as _td
            MockInvite.create_invite_token.return_value = {
                "token": "rawtoken",
                "token_id": "44444444-4444-4444-4444-444444444444",
                "expires_at": _dt.utcnow() + _td(hours=168),
                "user_email": "newuser@example.com",
                "username": "newuser",
            }
            MockInvite.DEFAULT_TTL_HOURS = 168
            user_uuid = "22222222-2222-2222-2222-222222222222"
            auth_mocks["user_repo"].get_user_by_username.return_value = None
            auth_mocks["user_repo"].get_user_by_email.return_value = None
            auth_mocks["user_repo"].create_user.return_value = {"id": user_uuid}
            auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
                id=user_uuid, username="newuser", email="newuser@example.com",
            )

            def _settings_get(category, key, default=None):
                if category == 'security' and key == 'public_base_url':
                    return 'https://example.test'
                if category == 'auth' and key == 'invite_token_ttl_hours':
                    return 168
                return default
            blueprint_app.settings_manager.get = _settings_get
            blueprint_app.email_service.send_invite_email.return_value = True

            resp = client.post(
                "/api/auth/users",
                json={
                    "username": "newuser",
                    "email": "newuser@example.com",
                    "full_name": "New User",
                    "role": "user",
                    "invite_only": True,
                },
                headers=auth_headers("admin"),
            )

            assert resp.status_code == 201
            body = resp.get_json()
            assert body["user"]["username"] == "newuser"
            assert body["invite_url"] == "https://example.test/onboarding?token=rawtoken"
            assert body["email_sent"] is True

    def test_create_user_invite_only_rejects_password_in_body(self, client, auth_mocks, auth_headers):
        """Pydantic validator: password must not be set when invite_only=True."""
        resp = client.post(
            "/api/auth/users",
            json={
                "username": "x",
                "email": "x@example.com",
                "password": "ShouldNotBeHere1!",
                "invite_only": True,
            },
            headers=auth_headers("admin"),
        )
        assert resp.status_code == 400

    def test_create_user_password_required_when_not_invite_only(self, client, auth_mocks, auth_headers):
        """Pydantic validator: password is required when invite_only is false."""
        resp = client.post(
            "/api/auth/users",
            json={
                "username": "x",
                "email": "x@example.com",
                "invite_only": False,
            },
            headers=auth_headers("admin"),
        )
        assert resp.status_code == 400


# =============================================================================
# Update user (PUT /api/auth/users/<id>)
# =============================================================================

class TestUpdateUser:
    """PUT /api/auth/users/<user_id> — admin updates a user's role/email/name."""

    def test_role_change_audit_records_old_and_new_role(self, client, auth_headers, auth_mocks):
        """A role change must record old_role AND new_role in the user_updated
        audit metadata — not merely THAT the role changed. This is what lets the
        seeded auth-admin-role-granted rule key an exact non-privileged→privileged
        escalation off the audit row itself, instead of joining the target's
        *current* role (which mis-fires and can't spot a genuine grant). If this
        metadata regresses to bare {'changes': ['role']}, the sharpened rule can
        no longer match and this test fails (#823)."""
        from unittest.mock import ANY
        target_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        # get_user_by_id is called twice: once BEFORE the update (old role) and
        # once after (the response payload / new role).
        auth_mocks["user_repo"].get_user_by_id.side_effect = [
            _make_db_user(id=target_id, username="bob", role="user"),
            _make_db_user(id=target_id, username="bob", role="admin"),
        ]
        auth_mocks["user_repo"].update_user.return_value = _make_db_user(
            id=target_id, username="bob", role="admin"
        )

        resp = client.put(
            f"/api/auth/users/{target_id}",
            json={"role": "admin"},
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        auth_mocks["audit_svc"].log_auth.assert_any_call(
            action="user_updated",
            success=True,
            user_id=ANY,
            username="bob",
            ip=ANY,
            user_agent=ANY,
            metadata={
                "updated_by": ANY,
                "changes": ["role"],
                "old_role": "user",
                "new_role": "admin",
            },
        )


# =============================================================================
# Disable user (PATCH /api/auth/users/<id>/disable)
# =============================================================================

class TestDisableUser:
    """PATCH /api/auth/users/<user_id>/disable"""

    def test_disable_user_success(self, client, auth_headers, auth_mocks):
        target_id = "11111111-1111-1111-1111-111111111111"
        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id=target_id, username="victim"
        )
        auth_mocks["session_repo"].deactivate_all_user_sessions.return_value = 2

        resp = client.patch(
            f"/api/auth/users/{target_id}/disable",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["sessions_revoked"] == 2

    def test_disable_self_fails(self, client, auth_headers, auth_mocks):
        """Admins cannot disable their own account."""
        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id=TEST_USER_UUID, username="testuser"
        )

        resp = client.patch(
            f"/api/auth/users/{TEST_USER_UUID}/disable",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 400
        assert resp.get_json()["error"] == "cannot_disable_self"

    def test_disable_nonexistent_user(self, client, auth_headers, auth_mocks):
        target_id = "22222222-2222-2222-2222-222222222222"
        auth_mocks["user_repo"].get_user_by_id.return_value = None

        resp = client.patch(
            f"/api/auth/users/{target_id}/disable",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404

    def test_disable_already_disabled(self, client, auth_headers, auth_mocks):
        target_id = "33333333-3333-3333-3333-333333333333"
        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id=target_id, is_active=False
        )

        resp = client.patch(
            f"/api/auth/users/{target_id}/disable",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 400
        assert resp.get_json()["error"] == "already_disabled"


# =============================================================================
# Enable user (PATCH /api/auth/users/<id>/enable)
# =============================================================================

class TestEnableUser:
    """PATCH /api/auth/users/<user_id>/enable"""

    def test_enable_user_success(self, client, auth_headers, auth_mocks):
        target_id = "11111111-1111-1111-1111-111111111111"
        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id=target_id, is_active=False
        )

        resp = client.patch(
            f"/api/auth/users/{target_id}/enable",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_enable_already_active(self, client, auth_headers, auth_mocks):
        target_id = "22222222-2222-2222-2222-222222222222"
        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id=target_id, is_active=True
        )

        resp = client.patch(
            f"/api/auth/users/{target_id}/enable",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 400
        assert resp.get_json()["error"] == "already_enabled"

    def test_enable_nonexistent_user(self, client, auth_headers, auth_mocks):
        target_id = "33333333-3333-3333-3333-333333333333"
        auth_mocks["user_repo"].get_user_by_id.return_value = None

        resp = client.patch(
            f"/api/auth/users/{target_id}/enable",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404


# =============================================================================
# Delete user (DELETE /api/auth/users/<id>)
# =============================================================================

class TestDeleteUser:
    """DELETE /api/auth/users/<user_id>"""

    def test_delete_user_success(self, client, auth_headers, auth_mocks):
        target_id = "11111111-1111-1111-1111-111111111111"
        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id=target_id, username="doomed"
        )
        auth_mocks["session_repo"].deactivate_all_user_sessions.return_value = 1
        auth_mocks["user_repo"].delete_user.return_value = True

        resp = client.delete(
            f"/api/auth/users/{target_id}",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["sessions_revoked"] == 1

    def test_delete_self_fails(self, client, auth_headers, auth_mocks):
        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id=TEST_USER_UUID
        )

        resp = client.delete(
            f"/api/auth/users/{TEST_USER_UUID}",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 400
        assert resp.get_json()["error"] == "cannot_delete_self"

    def test_delete_nonexistent_user(self, client, auth_headers, auth_mocks):
        target_id = "22222222-2222-2222-2222-222222222222"
        auth_mocks["user_repo"].get_user_by_id.return_value = None

        resp = client.delete(
            f"/api/auth/users/{target_id}",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404


# =============================================================================
# Get user stats (GET /api/auth/users/stats) -- admin only
# =============================================================================

class TestUserStats:
    """GET /api/auth/users/stats"""

    def test_user_stats_success(self, client, auth_headers, auth_mocks):
        auth_mocks["user_repo"].get_user_stats.return_value = {
            "total": 10, "active": 8, "inactive": 2, "by_role": {"user": 7, "admin": 3}
        }

        resp = client.get("/api/auth/users/stats", headers=auth_headers("admin"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["total"] == 10


# =============================================================================
# Login — MFA branch (POST /api/auth/login)
# =============================================================================

class TestResendInviteOnboardingGuard:
    """POST /api/auth/users/<id>/resend-invite must refuse already-onboarded
    users. Without this guard an admin could replay onboarding for a normal
    user — bypassing the password-reset audit action and the proof-of-old-
    password the self-service flow relies on."""

    def test_rejects_user_who_completed_onboarding(self, client, auth_headers, auth_mocks):
        target_id = "44444444-4444-4444-4444-444444444444"
        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id=target_id, onboarding_pending=False,
        )

        resp = client.post(
            f"/api/auth/users/{target_id}/resend-invite",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 400
        body = resp.get_json()
        assert body["error"] == "cannot_invite"


class TestLoginMfa:
    """2-step login: when MFA is enabled, /login returns a challenge token, not access tokens."""

    def test_login_returns_challenge_when_mfa_enabled(self, blueprint_app):
        """User with mfa_enabled=true gets {requires_mfa, mfa_challenge_token} instead of tokens."""
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.PasswordService') as PS, \
             patch('src.api.blueprints.auth.AuditService') as AS, \
             patch('src.api.blueprints.auth.SessionRepository'):
            UR.return_value.get_user_by_username.return_value = {
                'id': 'u1', 'username': 'alice', 'email': 'a@a.com', 'role': 'user',
                'is_active': True, 'password_hash': 'h', 'failed_login_attempts': 0,
            }
            MR.return_value.count_enabled_methods.return_value = 1
            PS.verify_password.return_value = True

            r = client.post('/api/auth/login', json={'username': 'alice', 'password': 'pw'})

        assert r.status_code == 200
        assert r.json.get('requires_mfa') is True
        assert 'mfa_challenge_token' in r.json
        assert r.json['expires_in'] > 0
        # No JWT issued at this stage
        assert 'access_token' not in r.json
        # Audit log records the partial-login event
        from unittest.mock import ANY
        AS.return_value.log_auth.assert_any_call(
            action='login_mfa_required',
            success=True,
            user_id='u1',
            username='alice',
            ip=ANY,
            user_agent=ANY,
        )

    def test_login_without_mfa_returns_enrollment_challenge(self, blueprint_app):
        """Local user without MFA enrolled gets enrollment challenge (not tokens).

        Under mandatory MFA enforcement, a local user with mfa_enabled=False
        (or no MFA row) is directed to the enrollment wizard instead of
        receiving full session tokens.
        """
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.PasswordService') as PS, \
             patch('src.api.blueprints.auth.AuditService'), \
             patch('src.api.blueprints.auth.SessionRepository'):
            UR.return_value.get_user_by_username.return_value = {
                'id': 'u1', 'username': 'alice', 'email': 'a@a.com', 'role': 'user',
                'is_active': True, 'password_hash': 'h', 'failed_login_attempts': 0,
                'created_at': datetime(2025, 1, 1),
            }
            MR.return_value.count_enabled_methods.return_value = 0
            PS.verify_password.return_value = True

            r = client.post('/api/auth/login', json={'username': 'alice', 'password': 'pw'})

        assert r.status_code == 200
        assert r.json.get('requires_mfa_enrollment') is True
        assert 'mfa_enrollment_token' in r.json
        assert 'access_token' not in r.json
        assert 'requires_mfa' not in r.json


# =============================================================================
# MFA enrollment-required (POST /api/auth/login when local user lacks MFA)
# =============================================================================

class TestMfaEnrollmentRequired:
    """Unconditional MFA enforcement for all local users at login.

    Every local user without an enrolled MFA row must be routed through the
    enrollment wizard. LDAP and OIDC users are never forced — their MFA is
    the IdP's responsibility.
    """

    def test_local_user_without_mfa_returns_enrollment_challenge(self, blueprint_app):
        """local user (any role) + no MFA row → enrollment challenge, no tokens."""
        from unittest.mock import ANY
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.PasswordService') as PS, \
             patch('src.api.blueprints.auth.AuditService') as AS, \
             patch('src.api.blueprints.auth.SessionRepository'):
            UR.return_value.get_user_by_username.return_value = {
                'id': 'u1', 'username': 'alice', 'email': 'a@a.com', 'role': 'user',
                'is_active': True, 'password_hash': 'h', 'failed_login_attempts': 0,
                'created_at': datetime(2025, 1, 1),
            }
            MR.return_value.count_enabled_methods.return_value = 0  # never enrolled
            PS.verify_password.return_value = True

            r = client.post('/api/auth/login', json={'username': 'alice', 'password': 'pw'})

        assert r.status_code == 200
        assert r.json.get('requires_mfa_enrollment') is True
        assert r.json.get('mfa_enrollment_token')
        assert r.json.get('expires_in') == 300
        # No JWT was issued
        assert 'access_token' not in r.json
        AS.return_value.log_auth.assert_any_call(
            action='login_mfa_enrollment_required',
            success=True,
            user_id='u1',
            username='alice',
            ip=ANY,
            user_agent=ANY,
        )

    def test_admin_local_without_mfa_returns_enrollment_challenge(self, blueprint_app):
        """admin role + local + no MFA row → enrollment challenge, no tokens."""
        from unittest.mock import ANY
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.PasswordService') as PS, \
             patch('src.api.blueprints.auth.AuditService') as AS, \
             patch('src.api.blueprints.auth.SessionRepository'):
            UR.return_value.get_user_by_username.return_value = {
                'id': 'u1', 'username': 'alice', 'email': 'a@a.com', 'role': 'admin',
                'is_active': True, 'password_hash': 'h', 'failed_login_attempts': 0,
                'created_at': datetime(2025, 1, 1),
            }
            MR.return_value.count_enabled_methods.return_value = 0  # never enrolled
            PS.verify_password.return_value = True

            r = client.post('/api/auth/login', json={'username': 'alice', 'password': 'pw'})

        assert r.status_code == 200
        assert r.json.get('requires_mfa_enrollment') is True
        assert 'access_token' not in r.json

    def test_local_user_with_mfa_enabled_uses_existing_challenge_path(self, blueprint_app):
        """local user + MFA already enrolled → existing MFA challenge (not enrollment)."""
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.PasswordService') as PS, \
             patch('src.api.blueprints.auth.AuditService'), \
             patch('src.api.blueprints.auth.SessionRepository'):
            UR.return_value.get_user_by_username.return_value = {
                'id': 'u1', 'username': 'alice', 'email': 'a@a.com', 'role': 'user',
                'is_active': True, 'password_hash': 'h', 'failed_login_attempts': 0,
                'created_at': datetime(2025, 1, 1),
            }
            MR.return_value.count_enabled_methods.return_value = 1
            PS.verify_password.return_value = True

            r = client.post('/api/auth/login', json={'username': 'alice', 'password': 'pw'})

        assert r.status_code == 200
        # Existing MFA-already-enabled path takes precedence
        assert r.json.get('requires_mfa') is True
        assert 'mfa_challenge_token' in r.json
        assert 'requires_mfa_enrollment' not in r.json

    def test_ldap_provisioned_password_hash_skips_enrollment(self, blueprint_app):
        """Belt-and-suspenders: a user whose password_hash starts with 'LDAP:'
        bypasses TOTP enrollment even on the local-fallback login path. (The
        primary auth_method='ldap' gate is exercised by TestLoginLdap.)"""
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.PasswordService') as PS, \
             patch('src.api.blueprints.auth.AuditService'), \
             patch('src.api.blueprints.auth.SessionRepository') as SR, \
             patch('src.api.blueprints.auth.TokenService') as TS:
            UR.return_value.get_user_by_username.return_value = {
                'id': 'u1', 'username': 'alice', 'email': 'a@a.com', 'role': 'admin',
                'is_active': True,
                'password_hash': 'LDAP:abc123', 'failed_login_attempts': 0,
                'created_at': datetime(2025, 1, 1),
            }
            MR.return_value.count_enabled_methods.return_value = 0
            PS.verify_password.return_value = True
            SR.return_value.create_session.return_value = 'sid'
            TS.generate_token_pair.return_value = {
                'access_token_jti': 'jti-1', 'user_claims': {'role': 'admin', 'username': 'alice'},
            }
            TS.hash_refresh_token.return_value = 'hashed'
            TS.calculate_token_expiration.return_value = datetime.utcnow() + timedelta(days=7)

            r = client.post('/api/auth/login', json={'username': 'alice', 'password': 'pw'})

        assert r.status_code == 200
        assert 'access_token' in r.json
        assert 'requires_mfa_enrollment' not in r.json

    def test_oidc_auth_method_bypasses_enrollment(self, blueprint_app):
        """The oidc_exchange call site passes auth_method='oidc'; the helper
        must return None (issue tokens) without checking MFA state. This guards
        against silent regression if the literal at the OIDC call site drifts."""
        from src.api.blueprints.auth import _maybe_require_mfa_enrollment
        with blueprint_app.app_context():
            with patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
                 patch('src.api.blueprints.auth.AuditService') as AS:
                user = {
                    'id': 'u1', 'username': 'alice', 'email': 'a@a.com',
                    'role': 'admin', 'password_hash': 'OIDC:placeholder',
                }
                result = _maybe_require_mfa_enrollment(
                    user,
                    auth_method='oidc',
                    mfa_repo=MR.return_value,
                    audit_service=AS.return_value,
                    ip_address='1.2.3.4',
                    user_agent='test',
                )
        assert result is None
        # Helper must short-circuit before counting MFA methods.
        MR.return_value.count_enabled_methods.assert_not_called()
        MR.return_value.list_enabled_methods.assert_not_called()


# =============================================================================
# MFA enroll-setup + enroll-verify (driven by enrollment token, no JWT)
# =============================================================================

class TestMfaEnrollmentEndpoints:
    """/mfa/enroll-setup and /mfa/enroll-verify accept the enrollment token
    issued by /login when a local user has no MFA enrolled. They drive the
    one-shot setup-and-verify flow that ends with full session tokens.
    """

    def test_enroll_setup_returns_secret_for_valid_token(self, blueprint_app):
        from src.api.utils.caching import cache as real_cache
        token = 'enroll-token-abc'
        with blueprint_app.app_context():
            real_cache.set(f'mfa_enrollment:{token}', {'user_id': 'u1'}, timeout=300)

        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.AuditService'):
            UR.return_value.get_user_by_id.return_value = {
                'id': 'u1', 'username': 'alice', 'email': 'a@a.com', 'role': 'admin',
                'is_active': True, 'created_at': datetime(2025, 1, 1),
            }

            r = client.post('/api/auth/mfa/enroll-setup',
                            json={'mfa_enrollment_token': token})

        assert r.status_code == 200, r.json
        assert 'secret' in r.json
        assert r.json['provisioning_uri'].startswith('otpauth://')
        # Verify the encrypted secret was persisted as a pending method
        # (atomic clear+insert under per-user advisory lock).
        MR.return_value.replace_pending_method.assert_called_once()

    def test_enroll_setup_rejects_invalid_token(self, blueprint_app):
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.AuditService'):
            r = client.post('/api/auth/mfa/enroll-setup',
                            json={'mfa_enrollment_token': 'no-such-token'})

        assert r.status_code == 401
        assert r.json['error'] == 'invalid_or_expired_enrollment'

    def test_enroll_verify_issues_full_tokens_on_correct_code(self, blueprint_app):
        from src.api.utils.caching import cache as real_cache
        from src.auth.services.mfa_service import MfaService
        import pyotp

        secret = MfaService.generate_secret()
        encrypted = MfaService.encrypt_secret(secret)
        good_code = pyotp.TOTP(secret).now()
        token = 'enroll-verify-token-xyz'
        with blueprint_app.app_context():
            real_cache.set(f'mfa_enrollment:{token}', {'user_id': 'u1'}, timeout=300)

        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.SessionRepository') as SR, \
             patch('src.api.blueprints.auth.TokenService') as TS, \
             patch('src.api.blueprints.auth.AuditService') as AS:
            UR.return_value.get_user_by_id.return_value = {
                'id': 'u1', 'username': 'alice', 'email': 'a@a.com', 'role': 'admin',
                'is_active': True, 'created_at': datetime(2025, 1, 1),
            }
            MR.return_value.get_pending_method.return_value = {
                'id': 'pending-id', 'mfa_enabled': False, 'mfa_secret': encrypted,
                'label': 'Authenticator app',
            }
            SR.return_value.create_session.return_value = 'sid'
            TS.generate_token_pair.return_value = {
                'access_token_jti': 'jti-1',
                'user_claims': {'role': 'admin', 'username': 'alice'},
            }
            TS.hash_refresh_token.return_value = 'hashed'
            TS.calculate_token_expiration.return_value = datetime.utcnow() + timedelta(days=7)

            r = client.post('/api/auth/mfa/enroll-verify',
                            json={'mfa_enrollment_token': token, 'code': good_code})

        assert r.status_code == 200, r.json
        assert 'access_token' in r.json
        assert 'refresh_token' in r.json
        assert r.json['user']['username'] == 'alice'
        # MFA method was activated
        MR.return_value.enable_method.assert_called_once_with('pending-id')
        # Token consumed (cache key dropped)
        with blueprint_app.app_context():
            assert real_cache.get(f'mfa_enrollment:{token}') is None

    def test_enroll_verify_rejects_bad_code(self, blueprint_app):
        from src.api.utils.caching import cache as real_cache
        from src.auth.services.mfa_service import MfaService

        secret = MfaService.generate_secret()
        encrypted = MfaService.encrypt_secret(secret)
        token = 'enroll-bad-code-token'
        with blueprint_app.app_context():
            real_cache.set(f'mfa_enrollment:{token}', {'user_id': 'u1'}, timeout=300)

        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.AuditService'):
            UR.return_value.get_user_by_id.return_value = {
                'id': 'u1', 'username': 'alice', 'email': 'a@a.com', 'role': 'admin',
                'is_active': True, 'created_at': datetime(2025, 1, 1),
            }
            MR.return_value.get_pending_method.return_value = {
                'id': 'pending-id', 'mfa_enabled': False, 'mfa_secret': encrypted,
                'label': 'Authenticator app',
            }

            r = client.post('/api/auth/mfa/enroll-verify',
                            json={'mfa_enrollment_token': token, 'code': '000000'})

        assert r.status_code == 400
        assert r.json['error'] == 'invalid_code'
        # MFA was NOT enabled on a bad code
        MR.return_value.enable_method.assert_not_called()

    def test_enroll_verify_rejects_invalid_token(self, blueprint_app):
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.AuditService'):
            r = client.post('/api/auth/mfa/enroll-verify',
                            json={'mfa_enrollment_token': 'gone', 'code': '123456'})

        assert r.status_code == 401
        assert r.json['error'] == 'invalid_or_expired_enrollment'


# =============================================================================
# Login — LDAP branch (POST /api/auth/login)
# =============================================================================

class TestLoginLdap:
    """LDAP authentication branch — runs BEFORE local password verify."""

    def test_ldap_success_provisions_user_and_issues_tokens(self, blueprint_app):
        """LDAP authenticates → user provisioned → tokens issued (MFA disabled)."""
        from unittest.mock import ANY
        client = blueprint_app.test_client()
        from src.auth.services.ldap_service import LdapUserInfo
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.SessionRepository') as SR, \
             patch('src.api.blueprints.auth.LdapService') as LdapSvcCls, \
             patch('src.api.blueprints.auth.AuditService'):
            ldap_svc = LdapSvcCls.return_value
            ldap_svc.is_enabled.return_value = True
            ldap_svc.allow_local_fallback.return_value = True
            ldap_svc.authenticate.return_value = LdapUserInfo(
                username='alice', user_dn='CN=alice,DC=corp,DC=com',
                email='alice@corp.com', full_name='Alice', role='admin', groups=[],
            )
            UR.return_value.get_or_create_ldap_user.return_value = {
                'id': 'u1', 'username': 'alice', 'email': 'alice@corp.com',
                'role': 'admin', 'is_active': True, 'created_at': '2025-01-01T00:00:00Z',
                'failed_login_attempts': 0,
            }
            MR.return_value.count_enabled_methods.return_value = 0

            r = client.post('/api/auth/login', json={'username': 'alice', 'password': 'pw'})

        assert r.status_code == 200
        assert 'access_token' in r.json
        assert r.json['user']['role'] == 'admin'
        ldap_svc.authenticate.assert_called_once_with('alice', 'pw')

    def test_ldap_rejection_falls_through_to_local_password(self, blueprint_app):
        """LDAP says no, allow_local_fallback=True → local bcrypt verify still tried."""
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.SessionRepository') as SR, \
             patch('src.api.blueprints.auth.LdapService') as LdapSvcCls, \
             patch('src.api.blueprints.auth.PasswordService') as PS, \
             patch('src.api.blueprints.auth.AuditService'):
            ldap_svc = LdapSvcCls.return_value
            ldap_svc.is_enabled.return_value = True
            ldap_svc.allow_local_fallback.return_value = True
            ldap_svc.authenticate.return_value = None  # LDAP rejected
            UR.return_value.get_user_by_username.return_value = {
                'id': 'u1', 'username': 'alice', 'email': 'a@a.com', 'role': 'user',
                'is_active': True, 'password_hash': 'hash', 'failed_login_attempts': 0,
                'created_at': '2025-01-01T00:00:00Z',
            }
            PS.verify_password.return_value = True
            MR.return_value.count_enabled_methods.return_value = 0

            r = client.post('/api/auth/login', json={'username': 'alice', 'password': 'pw'})

        assert r.status_code == 200  # local password succeeded
        PS.verify_password.assert_called_once()

    def test_ldap_rejection_blocks_when_fallback_disabled(self, blueprint_app):
        """LDAP rejects + allow_local_fallback=False → 401, no local check."""
        from unittest.mock import ANY
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.LdapService') as LdapSvcCls, \
             patch('src.api.blueprints.auth.PasswordService') as PS, \
             patch('src.api.blueprints.auth.AuditService') as AS:
            ldap_svc = LdapSvcCls.return_value
            ldap_svc.is_enabled.return_value = True
            ldap_svc.allow_local_fallback.return_value = False
            ldap_svc.authenticate.return_value = None

            r = client.post('/api/auth/login', json={'username': 'alice', 'password': 'pw'})

        assert r.status_code == 401
        PS.verify_password.assert_not_called()
        AS.return_value.log_auth.assert_any_call(
            action='login',
            success=False,
            user_id=None,
            username='alice',
            ip=ANY,
            user_agent=ANY,
            failure_reason='ldap_rejected_no_local_fallback',
        )

    def test_ldap_disabled_uses_local_password_only(self, blueprint_app):
        """Backward-compat: LDAP disabled → no LdapService call, local path runs."""
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.SessionRepository'), \
             patch('src.api.blueprints.auth.LdapService') as LdapSvcCls, \
             patch('src.api.blueprints.auth.PasswordService') as PS, \
             patch('src.api.blueprints.auth.AuditService'):
            ldap_svc = LdapSvcCls.return_value
            ldap_svc.is_enabled.return_value = False  # LDAP off
            UR.return_value.get_user_by_username.return_value = {
                'id': 'u1', 'username': 'alice', 'email': 'a@a.com', 'role': 'user',
                'is_active': True, 'password_hash': 'hash', 'failed_login_attempts': 0,
                'created_at': '2025-01-01T00:00:00Z',
            }
            PS.verify_password.return_value = True
            MR.return_value.count_enabled_methods.return_value = 0

            r = client.post('/api/auth/login', json={'username': 'alice', 'password': 'pw'})

        assert r.status_code == 200
        ldap_svc.authenticate.assert_not_called()

    def test_ldap_success_for_inactive_existing_user_returns_account_disabled(self, blueprint_app):
        """get_or_create_ldap_user raises on inactive → 401 account_disabled."""
        client = blueprint_app.test_client()
        from src.auth.services.ldap_service import LdapUserInfo
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.LdapService') as LdapSvcCls, \
             patch('src.api.blueprints.auth.AuditService'):
            ldap_svc = LdapSvcCls.return_value
            ldap_svc.is_enabled.return_value = True
            ldap_svc.authenticate.return_value = LdapUserInfo(
                username='alice', user_dn='CN=alice,DC=corp,DC=com',
                email='a@a.com', full_name='A', role='user', groups=[],
            )
            UR.return_value.get_or_create_ldap_user.side_effect = ValueError('inactive account')

            r = client.post('/api/auth/login', json={'username': 'alice', 'password': 'pw'})

        assert r.status_code == 401
        assert r.json['error'] == 'account_disabled'

    def test_ldap_success_blocked_when_existing_user_is_locally_locked(self, blueprint_app):
        """LDAP creds valid but local lockout still applies (no bypass)."""
        from unittest.mock import ANY
        client = blueprint_app.test_client()
        from src.auth.services.ldap_service import LdapUserInfo
        from datetime import datetime, timedelta
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.LdapService') as LdapSvcCls, \
             patch('src.api.blueprints.auth.AuditService') as AS:
            ldap_svc = LdapSvcCls.return_value
            ldap_svc.is_enabled.return_value = True
            ldap_svc.authenticate.return_value = LdapUserInfo(
                username='alice', user_dn='CN=alice,DC=corp,DC=com',
                email='a@a.com', full_name='A', role='user', groups=[],
            )
            UR.return_value.get_or_create_ldap_user.return_value = {
                'id': 'u1', 'username': 'alice', 'email': 'a@a.com', 'role': 'user',
                'is_active': True, 'created_at': '2025-01-01T00:00:00Z',
                'failed_login_attempts': 6,
                'locked_until': datetime.utcnow() + timedelta(minutes=10),
            }

            r = client.post('/api/auth/login', json={'username': 'alice', 'password': 'pw'})

        assert r.status_code == 401
        assert r.json['error'] == 'account_locked'
        AS.return_value.log_auth.assert_any_call(
            action='login', success=False, user_id='u1', username='alice',
            failure_reason='account_locked', ip=ANY, user_agent=ANY,
        )

    def test_ldap_success_emits_single_audit_row(self, blueprint_app):
        """LDAP path produces exactly one audit row (login_ldap), not also a login one."""
        client = blueprint_app.test_client()
        from src.auth.services.ldap_service import LdapUserInfo
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.SessionRepository'), \
             patch('src.api.blueprints.auth.LdapService') as LdapSvcCls, \
             patch('src.api.blueprints.auth.AuditService') as AS:
            ldap_svc = LdapSvcCls.return_value
            ldap_svc.is_enabled.return_value = True
            ldap_svc.authenticate.return_value = LdapUserInfo(
                username='alice', user_dn='CN=alice,DC=corp,DC=com',
                email='a@a.com', full_name='A', role='user', groups=[],
            )
            UR.return_value.get_or_create_ldap_user.return_value = {
                'id': 'u1', 'username': 'alice', 'email': 'a@a.com', 'role': 'user',
                'is_active': True, 'created_at': '2025-01-01T00:00:00Z',
                'failed_login_attempts': 0,
            }
            MR.return_value.count_enabled_methods.return_value = 0

            r = client.post('/api/auth/login', json={'username': 'alice', 'password': 'pw'})

        assert r.status_code == 200
        # Count success audit calls
        success_calls = [
            c for c in AS.return_value.log_auth.call_args_list
            if c.kwargs.get('success') is True
        ]
        actions = [c.kwargs.get('action') for c in success_calls]
        assert actions == ['login_ldap'], f"expected only login_ldap success row, got {actions}"


# =============================================================================
# super_admin local break-glass (POST /api/auth/login)
# =============================================================================

class TestSuperAdminBreakGlass:
    """Per epic decision #4, super_admin can ALWAYS log in locally so that a
    misconfigured IdP plus a no-fallback LDAP setting cannot brick the only
    super_admin account.
    """

    def test_super_admin_local_hash_break_glass_succeeds(self, blueprint_app):
        """LDAP rejects + super_admin has local hash + correct password → tokens issued."""
        from unittest.mock import ANY
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.SessionRepository') as SR, \
             patch('src.api.blueprints.auth.LdapService') as LdapSvcCls, \
             patch('src.api.blueprints.auth.PasswordService') as PS, \
             patch('src.api.blueprints.auth.TokenService') as TS, \
             patch('src.api.blueprints.auth.AuditService') as AS:
            ldap_svc = LdapSvcCls.return_value
            ldap_svc.is_enabled.return_value = True
            ldap_svc.allow_local_fallback.return_value = False  # NO fallback
            ldap_svc.authenticate.return_value = None  # not in LDAP
            UR.return_value.get_user_by_username.return_value = {
                'id': 'sa1', 'username': 'root', 'email': 'root@corp.com',
                'role': 'super_admin', 'is_active': True,
                'password_hash': '$2b$12$realbcrypthash', 'failed_login_attempts': 0,
                'created_at': '2025-01-01T00:00:00Z',
            }
            MR.return_value.count_enabled_methods.return_value = 0
            PS.verify_password.return_value = True
            SR.return_value.create_session.return_value = 'sid'
            TS.generate_token_pair.return_value = {
                'access_token_jti': 'jti-1',
                'user_claims': {'role': 'super_admin', 'username': 'root'},
            }
            TS.hash_refresh_token.return_value = 'h'
            TS.calculate_token_expiration.return_value = datetime.utcnow() + timedelta(days=7)

            r = client.post('/api/auth/login', json={'username': 'root', 'password': 'pw'})

        assert r.status_code == 200, r.json
        assert 'access_token' in r.json
        # Break-glass success audit row distinguishes this from a normal local login.
        AS.return_value.log_auth.assert_any_call(
            action='login_local_break_glass',
            success=True,
            user_id='sa1',
            username='root',
            ip=ANY,
            user_agent=ANY,
        )

    def test_super_admin_local_hash_break_glass_wrong_password(self, blueprint_app):
        """LDAP rejects + super_admin has local hash + wrong password → 401 + dedicated audit reason."""
        from unittest.mock import ANY
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.LdapService') as LdapSvcCls, \
             patch('src.api.blueprints.auth.PasswordService') as PS, \
             patch('src.api.blueprints.auth.AuditService') as AS:
            ldap_svc = LdapSvcCls.return_value
            ldap_svc.is_enabled.return_value = True
            ldap_svc.allow_local_fallback.return_value = False
            ldap_svc.authenticate.return_value = None
            UR.return_value.get_user_by_username.return_value = {
                'id': 'sa1', 'username': 'root', 'email': 'root@corp.com',
                'role': 'super_admin', 'is_active': True,
                'password_hash': '$2b$12$realbcrypthash', 'failed_login_attempts': 0,
                'created_at': '2025-01-01T00:00:00Z',
            }
            PS.verify_password.return_value = False  # wrong password

            r = client.post('/api/auth/login', json={'username': 'root', 'password': 'bad'})

        assert r.status_code == 401
        # Audit row uses the break-glass-specific failure reason and action so
        # SOC tooling can specifically alert on failed break-glass attempts.
        AS.return_value.log_auth.assert_any_call(
            action='login_local_break_glass',
            success=False,
            user_id='sa1',
            username='root',
            failure_reason='wrong_password_super_admin_break_glass',
            ip=ANY,
            user_agent=ANY,
        )

    def test_admin_role_no_break_glass(self, blueprint_app):
        """LDAP rejects + admin (not super_admin) with local hash → still 401, no break-glass."""
        from unittest.mock import ANY
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.LdapService') as LdapSvcCls, \
             patch('src.api.blueprints.auth.PasswordService') as PS, \
             patch('src.api.blueprints.auth.AuditService') as AS:
            ldap_svc = LdapSvcCls.return_value
            ldap_svc.is_enabled.return_value = True
            ldap_svc.allow_local_fallback.return_value = False
            ldap_svc.authenticate.return_value = None
            UR.return_value.get_user_by_username.return_value = {
                'id': 'a1', 'username': 'alice', 'role': 'admin', 'is_active': True,
                'password_hash': '$2b$12$realhash',
            }

            r = client.post('/api/auth/login', json={'username': 'alice', 'password': 'pw'})

        assert r.status_code == 401
        # Falls through to the standard ldap-no-fallback rejection path
        PS.verify_password.assert_not_called()
        AS.return_value.log_auth.assert_any_call(
            action='login',
            success=False,
            user_id=None,
            username='alice',
            failure_reason='ldap_rejected_no_local_fallback',
            ip=ANY,
            user_agent=ANY,
        )

    def test_super_admin_external_hash_no_break_glass(self, blueprint_app):
        """super_admin with OIDC-provisioned hash (no local password) → 401, no break-glass."""
        from unittest.mock import ANY
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.LdapService') as LdapSvcCls, \
             patch('src.api.blueprints.auth.PasswordService') as PS, \
             patch('src.api.blueprints.auth.AuditService') as AS:
            ldap_svc = LdapSvcCls.return_value
            ldap_svc.is_enabled.return_value = True
            ldap_svc.allow_local_fallback.return_value = False
            ldap_svc.authenticate.return_value = None
            UR.return_value.get_user_by_username.return_value = {
                'id': 'sa1', 'username': 'root', 'role': 'super_admin', 'is_active': True,
                # OIDC-provisioned: no usable local hash
                'password_hash': 'OIDC:abc123def',
            }

            r = client.post('/api/auth/login', json={'username': 'root', 'password': 'pw'})

        assert r.status_code == 401
        PS.verify_password.assert_not_called()
        AS.return_value.log_auth.assert_any_call(
            action='login',
            success=False,
            user_id=None,
            username='root',
            failure_reason='ldap_rejected_no_local_fallback',
            ip=ANY,
            user_agent=ANY,
        )

    def test_super_admin_password_login_when_oidc_enabled_only(self, blueprint_app):
        """OIDC is the only enabled SSO provider (LDAP off) → local password login still works
        for super_admin via the regular local-verify path. No special break-glass needed because
        /login does not consult OIDC. With mandatory MFA, the super_admin (who has MFA enrolled)
        proceeds to the standard MFA challenge step."""
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.LdapService') as LdapSvcCls, \
             patch('src.api.blueprints.auth.PasswordService') as PS, \
             patch('src.api.blueprints.auth.AuditService'):
            ldap_svc = LdapSvcCls.return_value
            ldap_svc.is_enabled.return_value = False  # LDAP off (OIDC-only mode)
            UR.return_value.get_user_by_username.return_value = {
                'id': 'sa1', 'username': 'root', 'email': 'root@corp.com',
                'role': 'super_admin', 'is_active': True,
                'password_hash': '$2b$12$realhash', 'failed_login_attempts': 0,
                'created_at': '2025-01-01T00:00:00Z',
            }
            MR.return_value.count_enabled_methods.return_value = 1
            PS.verify_password.return_value = True

            r = client.post('/api/auth/login', json={'username': 'root', 'password': 'pw'})

        # Password auth succeeded and super_admin reaches the MFA challenge step.
        assert r.status_code == 200, r.json
        assert r.json.get('requires_mfa') is True
        assert 'mfa_challenge_token' in r.json

    def test_super_admin_break_glass_bypasses_mfa_enrollment(self, blueprint_app):
        """super_admin without MFA + break-glass → tokens issued + warning audit."""
        from unittest.mock import ANY
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.SessionRepository') as SR, \
             patch('src.api.blueprints.auth.LdapService') as LdapSvcCls, \
             patch('src.api.blueprints.auth.PasswordService') as PS, \
             patch('src.api.blueprints.auth.TokenService') as TS, \
             patch('src.api.blueprints.auth.AuditService') as AS:
            ldap_svc = LdapSvcCls.return_value
            ldap_svc.is_enabled.return_value = True
            ldap_svc.allow_local_fallback.return_value = False
            ldap_svc.authenticate.return_value = None
            UR.return_value.get_user_by_username.return_value = {
                'id': 'sa1', 'username': 'root', 'email': 'root@corp.com',
                'role': 'super_admin', 'is_active': True,
                'password_hash': '$2b$12$realhash', 'failed_login_attempts': 0,
                'created_at': '2025-01-01T00:00:00Z',
            }
            MR.return_value.count_enabled_methods.return_value = 0  # no MFA enrolled
            PS.verify_password.return_value = True
            SR.return_value.create_session.return_value = 'sid'
            TS.generate_token_pair.return_value = {
                'access_token_jti': 'jti-1',
                'user_claims': {'role': 'super_admin', 'username': 'root'},
            }
            TS.hash_refresh_token.return_value = 'h'
            TS.calculate_token_expiration.return_value = datetime.utcnow() + timedelta(days=7)

            r = client.post('/api/auth/login', json={'username': 'root', 'password': 'pw'})

        assert r.status_code == 200, r.json
        assert 'access_token' in r.json
        # The bypass is recorded loudly so SOC tooling can investigate.
        AS.return_value.log_auth.assert_any_call(
            action='super_admin_break_glass_bypassed_mfa_enrollment',
            success=True,
            user_id='sa1',
            username='root',
            ip=ANY,
            user_agent=ANY,
        )


# =============================================================================
# JWT auth_method claim — Task 1 (PR 7)
# =============================================================================

class TestJwtAuthMethodClaim:
    """Verify that the auth_method claim is embedded in tokens at issuance
    and that protected endpoints read it from JWT instead of the DB."""

    def _decode_access_token(self, app, token_str):
        """Decode a Flask-JWT-Extended access token and return its claims."""
        from flask_jwt_extended import decode_token
        with app.app_context():
            return decode_token(token_str)

    def test_login_local_issues_mfa_challenge_for_enrolled_user(self, blueprint_app):
        """Successful local login for enrolled user returns MFA challenge (not direct tokens).

        Under mandatory MFA, local users with MFA enabled receive an MFA
        challenge token. The actual access token (with auth_method='local')
        is issued by /mfa/challenge — see test_mfa_challenge_jwt_has_auth_method_local.
        """
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.PasswordService') as PS, \
             patch('src.api.blueprints.auth.AuditService'), \
             patch('src.api.blueprints.auth.LdapService') as LdapSvcCls:
            LdapSvcCls.return_value.is_enabled.return_value = False
            UR.return_value.get_user_by_username.return_value = {
                'id': 'u1', 'username': 'alice', 'email': 'a@a.com', 'role': 'user',
                'is_active': True, 'password_hash': 'hash', 'failed_login_attempts': 0,
                'created_at': '2025-01-01T00:00:00Z',
            }
            MR.return_value.count_enabled_methods.return_value = 1
            PS.verify_password.return_value = True

            r = client.post('/api/auth/login', json={'username': 'alice', 'password': 'pw'})

        assert r.status_code == 200
        assert r.json.get('requires_mfa') is True
        assert 'mfa_challenge_token' in r.json
        assert 'access_token' not in r.json

    def test_login_ldap_jwt_has_auth_method_ldap(self, blueprint_app):
        """Successful LDAP login embeds auth_method='ldap' in the access token."""
        from src.auth.services.ldap_service import LdapUserInfo
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.SessionRepository') as SR, \
             patch('src.api.blueprints.auth.TokenService') as TS, \
             patch('src.api.blueprints.auth.LdapService') as LdapSvcCls, \
             patch('src.api.blueprints.auth.AuditService'):
            ldap_svc = LdapSvcCls.return_value
            ldap_svc.is_enabled.return_value = True
            ldap_svc.authenticate.return_value = LdapUserInfo(
                username='alice', user_dn='CN=alice,DC=corp,DC=com',
                email='alice@corp.com', full_name='Alice', role='user', groups=[],
            )
            UR.return_value.get_or_create_ldap_user.return_value = {
                'id': 'u1', 'username': 'alice', 'email': 'alice@corp.com',
                'role': 'user', 'is_active': True, 'created_at': '2025-01-01T00:00:00Z',
                'failed_login_attempts': 0,
            }
            MR.return_value.count_enabled_methods.return_value = 0
            SR.return_value.create_session.return_value = 'sid-2'
            TS.generate_token_pair.return_value = {
                'access_token_jti': 'jti-2',
                'user_claims': {'role': 'user', 'username': 'alice'},
            }
            TS.generate_refresh_token.return_value = 'raw-refresh'
            TS.hash_refresh_token.return_value = 'hashed'
            TS.calculate_token_expiration.return_value = datetime(2025, 12, 31)

            r = client.post('/api/auth/login', json={'username': 'alice', 'password': 'pw'})

        assert r.status_code == 200
        claims = self._decode_access_token(blueprint_app, r.json['access_token'])
        assert claims.get('auth_method') == 'ldap'
        # Response also reflects the LDAP user flag
        assert r.json['user']['is_ldap_user'] is True
        assert r.json['user']['is_oidc_user'] is False

    def test_mfa_challenge_jwt_has_auth_method_local(self, blueprint_app):
        """mfa_challenge issues tokens with auth_method='local' (MFA blocks external users)."""
        from src.api.utils.caching import cache
        from src.auth.services.mfa_service import MfaService
        import pyotp

        secret = MfaService.generate_secret()
        encrypted = MfaService.encrypt_secret(secret)
        good_code = pyotp.TOTP(secret).now()
        challenge_token = 'test-challenge-abc'

        client = blueprint_app.test_client()
        with blueprint_app.app_context():
            cache.set(f'mfa_challenge:{challenge_token}', {'user_id': 'u1', 'attempts': 0}, timeout=300)

        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.UserMfaRepository') as MR, \
             patch('src.api.blueprints.auth.SessionRepository') as SR, \
             patch('src.api.blueprints.auth.TokenService') as TS, \
             patch('src.api.blueprints.auth.AuditService'):
            UR.return_value.get_user_by_id.return_value = {
                'id': 'u1', 'username': 'alice', 'email': 'a@a.com', 'role': 'user',
                'is_active': True, 'created_at': '2025-01-01T00:00:00Z',
            }
            MR.return_value.count_enabled_methods.return_value = 1
            MR.return_value.list_enabled_methods.return_value = [
                {'id': 'm1', 'mfa_secret': encrypted, 'label': 'A'},
            ]
            SR.return_value.create_session.return_value = 'sid-3'
            TS.generate_token_pair.return_value = {
                'access_token_jti': 'jti-3',
                'user_claims': {'role': 'user', 'username': 'alice'},
            }
            TS.generate_refresh_token.return_value = 'raw-refresh'
            TS.hash_refresh_token.return_value = 'hashed'
            TS.calculate_token_expiration.return_value = datetime(2025, 12, 31)

            r = client.post('/api/auth/mfa/challenge',
                            json={'mfa_challenge_token': challenge_token, 'code': good_code})

        assert r.status_code == 200
        claims = self._decode_access_token(blueprint_app, r.json['access_token'])
        assert claims.get('auth_method') == 'local'

    def test_me_derives_is_ldap_user_from_jwt(self, client, blueprint_app):
        """GET /me reads auth_method from JWT — no get_user_by_username call."""
        from flask_jwt_extended import create_access_token
        with blueprint_app.app_context():
            # Use TEST_USER_UUID as identity so the autouse _prime_user_cache hit works.
            token = create_access_token(
                identity=TEST_USER_UUID,
                additional_claims={'role': 'admin', 'username': 'testuser', 'auth_method': 'ldap'},
            )
        headers = {'Authorization': f'Bearer {token}'}

        with patch('src.api.blueprints.auth.UserRepository') as UR:
            r = client.get('/api/auth/me', headers=headers)
            # get_user_by_username must NOT have been called
            UR.return_value.get_user_by_username.assert_not_called()

        assert r.status_code == 200, r.json
        assert r.json['is_ldap_user'] is True
        assert r.json['is_oidc_user'] is False

    def test_me_derives_is_oidc_user_from_jwt(self, client, blueprint_app):
        """GET /me with auth_method='oidc' returns is_oidc_user=True, no DB call."""
        from flask_jwt_extended import create_access_token
        with blueprint_app.app_context():
            token = create_access_token(
                identity=TEST_USER_UUID,
                additional_claims={'role': 'admin', 'username': 'testuser', 'auth_method': 'oidc'},
            )
        headers = {'Authorization': f'Bearer {token}'}

        with patch('src.api.blueprints.auth.UserRepository') as UR:
            r = client.get('/api/auth/me', headers=headers)
            UR.return_value.get_user_by_username.assert_not_called()

        assert r.status_code == 200, r.json
        assert r.json['is_ldap_user'] is False
        assert r.json['is_oidc_user'] is True

    def test_refresh_carries_auth_method_to_new_access_token(self, client, auth_mocks, blueprint_app):
        """Token refresh preserves auth_method from the refresh token into the new access token."""
        from flask_jwt_extended import create_refresh_token
        with blueprint_app.app_context():
            refresh_tok = create_refresh_token(
                identity=TEST_USER_UUID,
                additional_claims={'session_id': 'session-1', 'auth_method': 'ldap'},
            )

        auth_mocks['user_repo'].get_user_by_id.return_value = _make_db_user(id=TEST_USER_UUID)
        auth_mocks['session_repo'].get_session_by_id.return_value = {
            'id': 'session-1', 'is_active': True,
        }

        r = client.post(
            '/api/auth/refresh',
            headers={'Authorization': f'Bearer {refresh_tok}'},
        )

        assert r.status_code == 200
        claims = self._decode_access_token(blueprint_app, r.json['access_token'])
        assert claims.get('auth_method') == 'ldap'


# =============================================================================
# Cache invalidation on admin user-management actions
# =============================================================================

class TestCacheInvalidationOnAdminOps:
    """Admin actions that change a user's role/active state must drop the
    cached jwt_user record so the change takes effect inside the 60s TTL.
    """

    def test_disable_user_invalidates_jwt_user_cache(self, client, auth_headers, auth_mocks):
        target_id = "11111111-1111-1111-1111-111111111111"
        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id=target_id, username="victim", is_active=True
        )
        auth_mocks["session_repo"].deactivate_all_user_sessions.return_value = 1

        with patch("src.api.blueprints.auth.cache") as mock_cache:
            resp = client.patch(
                f"/api/auth/users/{target_id}/disable",
                headers=auth_headers("admin"),
            )

        assert resp.status_code == 200
        mock_cache.delete.assert_any_call(f"jwt_user:{target_id}")

    def test_enable_user_invalidates_cache(self, client, auth_headers, auth_mocks):
        target_id = "22222222-2222-2222-2222-222222222222"
        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id=target_id, is_active=False
        )

        with patch("src.api.blueprints.auth.cache") as mock_cache:
            resp = client.patch(
                f"/api/auth/users/{target_id}/enable",
                headers=auth_headers("admin"),
            )

        assert resp.status_code == 200
        mock_cache.delete.assert_any_call(f"jwt_user:{target_id}")

    def test_delete_user_invalidates_jwt_user_cache(self, client, auth_headers, auth_mocks):
        target_id = "33333333-3333-3333-3333-333333333333"
        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id=target_id, username="doomed"
        )
        auth_mocks["session_repo"].deactivate_all_user_sessions.return_value = 0
        auth_mocks["user_repo"].delete_user.return_value = True

        with patch("src.api.blueprints.auth.cache") as mock_cache:
            resp = client.delete(
                f"/api/auth/users/{target_id}",
                headers=auth_headers("admin"),
            )

        assert resp.status_code == 200
        mock_cache.delete.assert_any_call(f"jwt_user:{target_id}")

    def test_update_user_role_change_invalidates_cache(self, client, auth_headers, auth_mocks):
        target_id = "44444444-4444-4444-4444-444444444444"
        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id=target_id, username="demoted", role="super_admin"
        )

        with patch("src.api.blueprints.auth.cache") as mock_cache:
            resp = client.put(
                f"/api/auth/users/{target_id}",
                headers=auth_headers("admin"),
                json={"role": "admin"},
            )

        assert resp.status_code == 200
        # Use any_call because the update_user path may delete other keys too;
        # the contract is "jwt_user:<id> got dropped".
        mock_cache.delete.assert_any_call(f"jwt_user:{target_id}")

    def test_update_user_non_role_change_does_not_invalidate_cache(
        self, client, auth_headers, auth_mocks
    ):
        target_id = "55555555-5555-5555-5555-555555555555"
        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id=target_id, username="stable"
        )
        auth_mocks["user_repo"].get_user_by_email.return_value = None

        with patch("src.api.blueprints.auth.cache") as mock_cache:
            resp = client.put(
                f"/api/auth/users/{target_id}",
                headers=auth_headers("admin"),
                json={"email": "new@example.com"},
            )

        assert resp.status_code == 200
        # The cache must NOT be invalidated for benign edits like email change
        # — that would defeat the cache for unrelated PUTs.
        for call in mock_cache.delete.call_args_list:
            assert call.args[0] != f"jwt_user:{target_id}", (
                f"Non-role PUT must not invalidate jwt_user cache, got: {call}"
            )

class TestUpdateUserEmailConflict:
    """PUT /users/<id> with an email already used by a different user must
    return 409 Conflict (issue #614 acceptance criterion). 409 is the standard
    HTTP status for unique-constraint collisions."""

    def test_put_email_conflict_returns_409(self, client, auth_headers, auth_mocks):
        target_id = "66666666-6666-6666-6666-666666666666"
        other_id = "99999999-9999-9999-9999-999999999999"
        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id=target_id, username="target"
        )
        auth_mocks["user_repo"].get_user_by_email.return_value = _make_db_user(
            id=other_id, username="someone-else", email="taken@example.com"
        )

        resp = client.put(
            f"/api/auth/users/{target_id}",
            headers=auth_headers("admin"),
            json={"email": "taken@example.com"},
        )

        assert resp.status_code == 409
        assert resp.get_json()["error"] == "email_exists"

    def test_put_same_email_does_not_conflict(self, client, auth_headers, auth_mocks):
        """Re-submitting the user's own current email is a no-op, not a 409."""
        target_id = "66666666-6666-6666-6666-666666666666"
        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id=target_id, username="target", email="self@example.com"
        )
        auth_mocks["user_repo"].get_user_by_email.return_value = _make_db_user(
            id=target_id, username="target", email="self@example.com"
        )

        resp = client.put(
            f"/api/auth/users/{target_id}",
            headers=auth_headers("admin"),
            json={"email": "self@example.com"},
        )

        assert resp.status_code == 200


class TestUpdateUserRejectsIsActive:
    """PUT /users/<id> with is_active must return 400 — admin should use the
    dedicated /disable and /enable endpoints so the action gets its own
    user_disabled / user_enabled audit row."""

    def test_put_with_is_active_returns_400(self, client, auth_headers, auth_mocks):
        target_id = "77777777-7777-7777-7777-777777777777"
        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id=target_id, username="target"
        )

        resp = client.put(
            f"/api/auth/users/{target_id}",
            headers=auth_headers("admin"),
            json={"is_active": False},
        )

        assert resp.status_code == 400
        body = resp.get_json()
        assert body["error"] == "validation_error"
        # The error message must point operators at the right endpoint, not
        # just say "invalid request data".
        assert "/disable" in body["message"]
        assert "/enable" in body["message"]
        # The details list still flags the offending field for client-side handling.
        assert any("is_active" in (d.get("loc") or ()) for d in body["details"])

    def test_put_without_is_active_succeeds(self, client, auth_headers, auth_mocks):
        target_id = "88888888-8888-8888-8888-888888888888"
        auth_mocks["user_repo"].get_user_by_id.return_value = _make_db_user(
            id=target_id, username="target"
        )
        auth_mocks["user_repo"].get_user_by_email.return_value = None

        resp = client.put(
            f"/api/auth/users/{target_id}",
            headers=auth_headers("admin"),
            json={"email": "newaddr@example.com"},
        )

        assert resp.status_code == 200


# =============================================================================
# OIDC callback — auto_create_users + email-domain allowlist
# =============================================================================

class TestOidcCallbackEmailAllowlist:
    """GET /api/auth/oidc/callback — Task 3.3 (PR 3 v2026.17.33)

    Covers the interaction between `oidc.auto_create_users` and the new
    `oidc.allowed_email_domains` setting:

    - auto_create_users=true, allowlist empty       → user provisioned (legacy behavior)
    - auto_create_users=true, allowlist matches     → user provisioned
    - auto_create_users=true, allowlist mismatches  → 302 to /login?error=oidc_email_domain_not_allowed,
                                                       audit failure_reason='oidc_email_domain_not_allowed'
    - auto_create_users=false, existing user        → user logged in (allowlist not consulted)
    - auto_create_users=false, no existing user     → 302 to /login?error=oidc_user_not_provisioned
    """

    @staticmethod
    def _settings_factory(auto_create=True, allowed_domains=''):
        """Build a settings_manager.get side_effect that returns these OIDC values."""
        cfg = {
            'auto_create_users': auto_create,
            'allowed_email_domains': allowed_domains,
        }

        def _get(category, key, default=None):
            if category == 'oidc' and key in cfg:
                return cfg[key]
            return default

        return _get

    @staticmethod
    def _user_info(email='alice@example.com'):
        from src.auth.services.oidc_service import OidcUserInfo
        return OidcUserInfo(
            subject='sub-1', email=email, full_name='Alice',
            role='user', groups=[],
        )

    @staticmethod
    def _user_row(email='alice@example.com'):
        return {
            'id': 'u-1', 'username': email, 'email': email,
            'role': 'user', 'is_active': True,
        }

    def test_auto_create_true_empty_allowlist_provisions(self, client, blueprint_app):
        """auto_create_users=true and empty allowlist preserves legacy behavior."""
        blueprint_app.settings_manager.get = self._settings_factory(
            auto_create=True, allowed_domains='',
        )
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.OidcService') as OidcSvc, \
             patch('src.api.blueprints.auth.AuditService') as AS:
            OidcSvc.return_value.exchange_code.return_value = self._user_info()
            OidcSvc.return_value.frontend_complete_url.return_value = '/oidc-complete'
            UR.return_value.get_or_create_oidc_user.return_value = self._user_row()

            r = client.get('/api/auth/oidc/callback?code=c&state=s')

            UR.return_value.get_or_create_oidc_user.assert_called_once()
            UR.return_value.get_user_by_email.assert_not_called()
            audit = AS.return_value
            # Final audit event should be a success (no domain rejection in between).
            assert any(
                call.kwargs.get('success') is True
                and call.kwargs.get('action') == 'login_oidc'
                for call in audit.log_auth.call_args_list
            )
        assert r.status_code == 302
        assert '/oidc-complete' in r.headers['Location']

    def test_auto_create_true_allowlist_match_provisions(self, client, blueprint_app):
        """auto_create_users=true with matching domain provisions the user."""
        blueprint_app.settings_manager.get = self._settings_factory(
            auto_create=True, allowed_domains='example.com,corp.io',
        )
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.OidcService') as OidcSvc, \
             patch('src.api.blueprints.auth.AuditService'):
            OidcSvc.return_value.exchange_code.return_value = self._user_info(
                email='alice@example.com'
            )
            OidcSvc.return_value.frontend_complete_url.return_value = '/oidc-complete'
            UR.return_value.get_or_create_oidc_user.return_value = self._user_row()

            r = client.get('/api/auth/oidc/callback?code=c&state=s')

            UR.return_value.get_or_create_oidc_user.assert_called_once()
        assert r.status_code == 302
        assert '/oidc-complete' in r.headers['Location']

    def test_auto_create_true_allowlist_mismatch_rejects(self, client, blueprint_app):
        """auto_create_users=true with mismatched domain returns the error redirect
        and audits oidc_email_domain_not_allowed; user is NOT provisioned."""
        blueprint_app.settings_manager.get = self._settings_factory(
            auto_create=True, allowed_domains='example.com,corp.io',
        )
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.OidcService') as OidcSvc, \
             patch('src.api.blueprints.auth.AuditService') as AS:
            OidcSvc.return_value.exchange_code.return_value = self._user_info(
                email='alice@evil.com'
            )

            r = client.get('/api/auth/oidc/callback?code=c&state=s')

            UR.return_value.get_or_create_oidc_user.assert_not_called()
            UR.return_value.get_user_by_email.assert_not_called()
            audit = AS.return_value
            domain_rejections = [
                call for call in audit.log_auth.call_args_list
                if call.kwargs.get('failure_reason') == 'oidc_email_domain_not_allowed'
            ]
            assert len(domain_rejections) == 1
            kwargs = domain_rejections[0].kwargs
            assert kwargs['success'] is False
            assert kwargs['action'] == 'login_oidc'
            assert kwargs['username'] == 'alice@evil.com'
            assert kwargs['metadata']['attempted_domain'] == 'evil.com'
            assert kwargs['metadata']['allowed_domains'] == ['corp.io', 'example.com']

        assert r.status_code == 302
        assert 'oidc_email_domain_not_allowed' in r.headers['Location']

    def test_auto_create_false_existing_user_logs_in(self, client, blueprint_app):
        """auto_create_users=false with an existing user logs in regardless of allowlist."""
        # Allowlist is set but should NOT be consulted on the false-path.
        blueprint_app.settings_manager.get = self._settings_factory(
            auto_create=False, allowed_domains='only-this-domain.com',
        )
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.OidcService') as OidcSvc, \
             patch('src.api.blueprints.auth.AuditService'):
            OidcSvc.return_value.exchange_code.return_value = self._user_info(
                email='alice@example.com'
            )
            OidcSvc.return_value.frontend_complete_url.return_value = '/oidc-complete'
            UR.return_value.get_user_by_email.return_value = self._user_row()

            r = client.get('/api/auth/oidc/callback?code=c&state=s')

            UR.return_value.get_user_by_email.assert_called_once_with('alice@example.com')
            UR.return_value.get_or_create_oidc_user.assert_not_called()
        assert r.status_code == 302
        assert '/oidc-complete' in r.headers['Location']

    def test_auto_create_false_no_user_rejects(self, client, blueprint_app):
        """auto_create_users=false and no existing user → user_not_provisioned (existing behavior)."""
        blueprint_app.settings_manager.get = self._settings_factory(
            auto_create=False, allowed_domains='',
        )
        with patch('src.api.blueprints.auth.UserRepository') as UR, \
             patch('src.api.blueprints.auth.OidcService') as OidcSvc, \
             patch('src.api.blueprints.auth.AuditService') as AS:
            OidcSvc.return_value.exchange_code.return_value = self._user_info(
                email='alice@example.com'
            )
            UR.return_value.get_user_by_email.return_value = None

            r = client.get('/api/auth/oidc/callback?code=c&state=s')

            UR.return_value.get_or_create_oidc_user.assert_not_called()
            audit = AS.return_value
            failures = [
                call for call in audit.log_auth.call_args_list
                if call.kwargs.get('failure_reason') == 'user_not_provisioned'
            ]
            assert len(failures) == 1

        assert r.status_code == 302
        assert 'oidc_user_not_provisioned' in r.headers['Location']


# =============================================================================
# Admin endpoint guards — parametrized 403 coverage
# =============================================================================
#
# Catches regressions where an admin-only endpoint loses its @require_admin
# decorator. Every endpoint protected by @require_admin (or
# @require_self_or_admin where the path user_id is NOT the JWT user_id) MUST
# return 403 when hit with a regular user-role token.
#
# Fixture chain:
#   - ``_prime_regular_user`` overwrites the autouse jwt_user cache with role=user
#   - ``auth_headers("user")`` mints a JWT with role=user, identity=test-user-id
#   - The path ``user_id`` is intentionally a different UUID
#     ("aaaa...-1111") so require_self_or_admin's "self" branch doesn't grant
#     access — the test confirms the *role* gate, not the identity gate.
# =============================================================================

class TestRbacAdminEndpointGuards:
    """A user-role token must be rejected with 403 by every admin endpoint.

    If an admin endpoint silently loses its @require_admin decorator in the
    future, this parametrized test will fail. The list mirrors the
    @require_admin / @require_super_admin / @require_self_or_admin('user_id')
    sites in src/api/blueprints/auth.py.
    """

    # A path-level user_id that is DIFFERENT from the test user_id, so
    # require_self_or_admin treats it as a foreign-user access.
    _OTHER_USER_ID = "ffffffff-ffff-ffff-ffff-aaaaaaaa1111"

    @pytest.mark.parametrize("method,path,body", [
        # @require_admin endpoints
        ("POST",   "/api/auth/register",
         {"username": "x", "email": "x@y.com", "password": "Password123!"}),
        ("GET",    "/api/auth/users", None),
        ("GET",    "/api/auth/users/stats", None),
        ("POST",   "/api/auth/users",
         {"username": "x", "email": "x@y.com", "password": "Password123!"}),
        ("PUT",    f"/api/auth/users/{_OTHER_USER_ID}",
         {"email": "x@y.com"}),
        ("PATCH",  f"/api/auth/users/{_OTHER_USER_ID}/disable", None),
        ("PATCH",  f"/api/auth/users/{_OTHER_USER_ID}/enable", None),
        ("DELETE", f"/api/auth/users/{_OTHER_USER_ID}", None),
        ("POST",   f"/api/auth/users/{_OTHER_USER_ID}/resend-invite", None),
        ("POST",   "/api/auth/ldap/test",
         {"username": "u", "password": "p"}),
        ("GET",    "/api/auth/oidc/test-discovery", None),
        # @require_self_or_admin('user_id') endpoints — hit them with a path
        # user_id that doesn't match the JWT user_id so the "self" branch is
        # NOT taken and only the admin gate matters.
        ("GET",    f"/api/auth/users/{_OTHER_USER_ID}", None),
    ])
    def test_admin_endpoint_rejects_non_admin(
        self, client, auth_headers, _prime_regular_user, method, path, body
    ):
        headers = auth_headers("user")
        if body is not None:
            resp = client.open(path, method=method, headers=headers, json=body)
        else:
            resp = client.open(path, method=method, headers=headers)

        assert resp.status_code == 403, (
            f"Expected 403 for {method} {path} with user-role token, "
            f"got {resp.status_code}: {resp.get_data(as_text=True)[:200]}"
        )


class TestPasswordExpirationOnLogin:
    """_issue_session_tokens flips must_change_password=true for local users
    whose password has aged past auth.password_expiration_days. LDAP/OIDC
    are skipped; setting=0 disables the check entirely."""

    def test_local_path_invokes_expiration_check_when_setting_positive(
        self, client, auth_mocks, blueprint_app
    ):
        from src.api.blueprints.auth import _issue_session_tokens

        db_user = _make_db_user(must_change_password=False)

        def _expire_side_effect(user_id, days):
            db_user["must_change_password"] = True
            return True

        auth_mocks["user_repo"].mark_password_expired_if_old.side_effect = (
            _expire_side_effect
        )
        # session creation needs reasonable defaults
        auth_mocks["session_repo"].create_session.return_value = "session-uuid"
        auth_mocks["TokenService"].generate_token_pair.return_value = {
            "access_token_jti": "jti", "user_claims": {},
        }
        auth_mocks["TokenService"].calculate_token_expiration.return_value = None

        blueprint_app.settings_manager.get = (
            lambda cat, key, default=None:
                180 if (cat, key) == ("auth", "password_expiration_days") else default
        )

        with blueprint_app.test_request_context():
            _issue_session_tokens(
                db_user, "127.0.0.1", "ua/1.0", auth_method="local",
            )

        auth_mocks["user_repo"].mark_password_expired_if_old.assert_called_once_with(
            db_user["id"], 180
        )
        assert db_user["must_change_password"] is True

    def test_local_path_skips_when_setting_is_zero(
        self, client, auth_mocks, blueprint_app
    ):
        from src.api.blueprints.auth import _issue_session_tokens

        db_user = _make_db_user(must_change_password=False)
        auth_mocks["session_repo"].create_session.return_value = "session-uuid"
        auth_mocks["TokenService"].generate_token_pair.return_value = {
            "access_token_jti": "jti", "user_claims": {},
        }
        auth_mocks["TokenService"].calculate_token_expiration.return_value = None

        blueprint_app.settings_manager.get = (
            lambda cat, key, default=None:
                0 if (cat, key) == ("auth", "password_expiration_days") else default
        )

        with blueprint_app.test_request_context():
            _issue_session_tokens(
                db_user, "127.0.0.1", "ua/1.0", auth_method="local",
            )

        auth_mocks["user_repo"].mark_password_expired_if_old.assert_not_called()

    def test_ldap_path_never_invokes_expiration_check(
        self, client, auth_mocks, blueprint_app
    ):
        from src.api.blueprints.auth import _issue_session_tokens

        db_user = _make_db_user(must_change_password=False)
        auth_mocks["session_repo"].create_session.return_value = "session-uuid"
        auth_mocks["TokenService"].generate_token_pair.return_value = {
            "access_token_jti": "jti", "user_claims": {},
        }
        auth_mocks["TokenService"].calculate_token_expiration.return_value = None

        blueprint_app.settings_manager.get = (
            lambda cat, key, default=None:
                180 if (cat, key) == ("auth", "password_expiration_days") else default
        )

        with blueprint_app.test_request_context():
            _issue_session_tokens(
                db_user, "127.0.0.1", "ua/1.0", auth_method="ldap",
            )

        auth_mocks["user_repo"].mark_password_expired_if_old.assert_not_called()

    def test_local_path_no_op_when_repo_returns_false(
        self, client, auth_mocks, blueprint_app
    ):
        from src.api.blueprints.auth import _issue_session_tokens

        db_user = _make_db_user(must_change_password=False)
        auth_mocks["user_repo"].mark_password_expired_if_old.return_value = False
        auth_mocks["session_repo"].create_session.return_value = "session-uuid"
        auth_mocks["TokenService"].generate_token_pair.return_value = {
            "access_token_jti": "jti", "user_claims": {},
        }
        auth_mocks["TokenService"].calculate_token_expiration.return_value = None

        blueprint_app.settings_manager.get = (
            lambda cat, key, default=None:
                180 if (cat, key) == ("auth", "password_expiration_days") else default
        )

        with blueprint_app.test_request_context():
            _issue_session_tokens(
                db_user, "127.0.0.1", "ua/1.0", auth_method="local",
            )

        # Setting is positive → repo called once → returned False → flag stays
        auth_mocks["user_repo"].mark_password_expired_if_old.assert_called_once()
        assert db_user["must_change_password"] is False


class TestAccessTokenTtlRespectsSessionTimeout:
    """JWT access-token TTL must follow security.session_timeout_minutes.

    Regression: the setting used to control only the frontend idle-warning
    modal — the backend issued JWTs whose expiration was fixed at app boot
    from the JWT_ACCESS_TOKEN_EXPIRES_MINUTES env var, so admins changing
    the setting in the UI had no effect on actual session lifetime.
    """

    def _patch_settings(self, blueprint_app, value):
        blueprint_app.settings_manager.get = (
            lambda cat, key, default=None:
                value if (cat, key) == ("security", "session_timeout_minutes") else default
        )

    def test_issue_session_tokens_uses_setting(
        self, client, auth_mocks, blueprint_app
    ):
        from flask_jwt_extended import decode_token
        from src.api.blueprints.auth import _issue_session_tokens

        db_user = _make_db_user()
        auth_mocks["session_repo"].create_session.return_value = "session-uuid"
        auth_mocks["TokenService"].generate_token_pair.return_value = {
            "access_token_jti": "jti", "user_claims": {},
        }
        auth_mocks["TokenService"].calculate_token_expiration.return_value = None
        self._patch_settings(blueprint_app, 1440)

        with blueprint_app.test_request_context():
            access_token, _, expires_in, _ = _issue_session_tokens(
                db_user, "127.0.0.1", "ua/1.0", auth_method="local",
            )
            decoded = decode_token(access_token)

        assert expires_in == 1440 * 60
        assert abs((decoded["exp"] - decoded["iat"]) - 1440 * 60) < 5

    def test_issue_session_tokens_falls_back_when_setting_missing(
        self, client, auth_mocks, blueprint_app
    ):
        from flask_jwt_extended import decode_token
        from src.api.blueprints.auth import _issue_session_tokens

        db_user = _make_db_user()
        auth_mocks["session_repo"].create_session.return_value = "session-uuid"
        auth_mocks["TokenService"].generate_token_pair.return_value = {
            "access_token_jti": "jti", "user_claims": {},
        }
        auth_mocks["TokenService"].calculate_token_expiration.return_value = None
        # settings_manager.get default returns whatever's passed as `default`
        # (None here) — the helper must fall back to JWT_ACCESS_TOKEN_EXPIRES.
        fallback_seconds = int(
            blueprint_app.config["JWT_ACCESS_TOKEN_EXPIRES"].total_seconds()
        )

        with blueprint_app.test_request_context():
            access_token, _, expires_in, _ = _issue_session_tokens(
                db_user, "127.0.0.1", "ua/1.0", auth_method="local",
            )
            decoded = decode_token(access_token)

        assert expires_in == fallback_seconds
        assert abs((decoded["exp"] - decoded["iat"]) - fallback_seconds) < 5

    def test_issue_session_tokens_falls_back_when_setting_invalid(
        self, client, auth_mocks, blueprint_app
    ):
        from src.api.blueprints.auth import _issue_session_tokens

        db_user = _make_db_user()
        auth_mocks["session_repo"].create_session.return_value = "session-uuid"
        auth_mocks["TokenService"].generate_token_pair.return_value = {
            "access_token_jti": "jti", "user_claims": {},
        }
        auth_mocks["TokenService"].calculate_token_expiration.return_value = None
        self._patch_settings(blueprint_app, "not-a-number")
        fallback_seconds = int(
            blueprint_app.config["JWT_ACCESS_TOKEN_EXPIRES"].total_seconds()
        )

        with blueprint_app.test_request_context():
            _, _, expires_in, _ = _issue_session_tokens(
                db_user, "127.0.0.1", "ua/1.0", auth_method="local",
            )

        assert expires_in == fallback_seconds

    def test_issue_session_tokens_clamps_to_24h_when_setting_exceeds_max(
        self, client, auth_mocks, blueprint_app
    ):
        """Defensive fence: schema cap is 1440 but only enforced at write
        time. A stale or out-of-band DB value above 1440 must not produce a
        token longer than 24h.
        """
        from src.api.blueprints.auth import _issue_session_tokens

        db_user = _make_db_user()
        auth_mocks["session_repo"].create_session.return_value = "session-uuid"
        auth_mocks["TokenService"].generate_token_pair.return_value = {
            "access_token_jti": "jti", "user_claims": {},
        }
        auth_mocks["TokenService"].calculate_token_expiration.return_value = None
        self._patch_settings(blueprint_app, 99999)

        with blueprint_app.test_request_context():
            _, _, expires_in, _ = _issue_session_tokens(
                db_user, "127.0.0.1", "ua/1.0", auth_method="local",
            )

        assert expires_in == 1440 * 60


# =============================================================================
# Admin Password Reset (POST /api/auth/users/<id>/password-reset)
# =============================================================================

class TestAdminPasswordReset:
    """POST /api/auth/users/<id>/password-reset — admin-only.
    Sends a password-reset email and audit-logs `user_password_changed_by_admin`,
    distinct from the public `/password-reset/request` (which emits
    `password_reset_request`)."""

    def test_admin_password_reset_success_emits_admin_audit_action(
        self, client, auth_headers, auth_mocks, blueprint_app
    ):
        target_id = "33333333-3333-3333-3333-333333333333"
        target = _make_db_user(id=target_id, username="alice", email="alice@x.com")
        auth_mocks["user_repo"].get_user_by_id.return_value = target

        with patch("src.api.blueprints.auth.UserRepository.is_external_user", return_value=False), \
             patch("src.auth.services.password_reset_service.PasswordResetService.create_reset_token",
                   return_value={"token": "TOK"}), \
             patch("src.auth.services.password_reset_service.PasswordResetService.TOKEN_EXPIRATION_HOURS", 1), \
             patch.object(blueprint_app, "email_service") as mock_email_svc:
            mock_email_svc.send_password_reset_email.return_value = True
            resp = client.post(
                f"/api/auth/users/{target_id}/password-reset",
                headers=auth_headers("admin"),
            )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        actions = [
            c.kwargs.get("action") for c in auth_mocks["audit_svc"].log_auth.call_args_list
        ]
        assert "user_password_changed_by_admin" in actions

    def test_admin_password_reset_404_when_target_missing(
        self, client, auth_headers, auth_mocks
    ):
        auth_mocks["user_repo"].get_user_by_id.return_value = None
        target_id = "44444444-4444-4444-4444-444444444444"
        resp = client.post(
            f"/api/auth/users/{target_id}/password-reset",
            headers=auth_headers("admin"),
        )
        assert resp.status_code == 404

    def test_admin_password_reset_400_for_ldap_user(
        self, client, auth_headers, auth_mocks
    ):
        target_id = "66666666-6666-6666-6666-666666666666"
        target = _make_db_user(
            id=target_id, password_hash="LDAP:placeholder",
        )
        auth_mocks["user_repo"].get_user_by_id.return_value = target
        with patch("src.api.blueprints.auth.UserRepository.is_external_user", return_value=True):
            resp = client.post(
                f"/api/auth/users/{target_id}/password-reset",
                headers=auth_headers("admin"),
            )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "non_local_user"

    def test_admin_password_reset_403_when_caller_not_admin(
        self, client, auth_headers, _prime_regular_user
    ):
        target_id = "55555555-5555-5555-5555-555555555555"
        resp = client.post(
            f"/api/auth/users/{target_id}/password-reset",
            headers=auth_headers("user"),
        )
        assert resp.status_code == 403


class TestForcePasswordChange:
    """PATCH /api/auth/users/<id>/force-password-change — admin-only.
    Idempotent flip of must_change_password=true. No password change,
    no email, no session revocation. Audit: user_force_password_change_set."""

    def test_force_password_change_success(self, client, auth_headers, auth_mocks):
        target_id = "77777777-7777-7777-7777-777777777777"
        target = _make_db_user(id=target_id, username="bob")
        auth_mocks["user_repo"].get_user_by_id.return_value = target
        with patch(
            "src.api.blueprints.auth.UserRepository.is_external_user",
            return_value=False,
        ):
            resp = client.patch(
                f"/api/auth/users/{target_id}/force-password-change",
                headers=auth_headers("admin"),
                json={},
            )
        assert resp.status_code == 200
        auth_mocks["user_repo"].set_must_change_password.assert_called_once()
        actions = [
            c.kwargs.get("action") for c in auth_mocks["audit_svc"].log_auth.call_args_list
        ]
        assert "user_force_password_change_set" in actions

    def test_force_password_change_404(self, client, auth_headers, auth_mocks):
        auth_mocks["user_repo"].get_user_by_id.return_value = None
        target_id = "88888888-8888-8888-8888-888888888888"
        resp = client.patch(
            f"/api/auth/users/{target_id}/force-password-change",
            headers=auth_headers("admin"),
            json={},
        )
        assert resp.status_code == 404

    def test_force_password_change_400_for_ldap_user(
        self, client, auth_headers, auth_mocks
    ):
        target_id = "99999999-9999-9999-9999-999999999999"
        target = _make_db_user(id=target_id, password_hash="LDAP:placeholder")
        auth_mocks["user_repo"].get_user_by_id.return_value = target
        with patch(
            "src.api.blueprints.auth.UserRepository.is_external_user",
            return_value=True,
        ):
            resp = client.patch(
                f"/api/auth/users/{target_id}/force-password-change",
                headers=auth_headers("admin"),
                json={},
            )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "non_local_user"

    def test_force_password_change_403_when_caller_not_admin(
        self, client, auth_headers, _prime_regular_user
    ):
        target_id = "aaaaaaab-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        resp = client.patch(
            f"/api/auth/users/{target_id}/force-password-change",
            headers=auth_headers("user"),
            json={},
        )
        assert resp.status_code == 403

    def test_force_password_change_invalidates_jwt_user_cache(
        self, client, auth_headers, auth_mocks
    ):
        # Without cache invalidation, the target user's cached jwt_user
        # serves must_change_password=False for up to 60s, so the
        # ProtectedRoute force-change redirect doesn't fire and the
        # admin action is silently bypassed for that window.
        target_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        target = _make_db_user(id=target_id, username="bob")
        auth_mocks["user_repo"].get_user_by_id.return_value = target
        with patch(
            "src.api.blueprints.auth.UserRepository.is_external_user",
            return_value=False,
        ), patch("src.api.blueprints.auth.cache") as mock_cache:
            resp = client.patch(
                f"/api/auth/users/{target_id}/force-password-change",
                headers=auth_headers("admin"),
                json={},
            )
        assert resp.status_code == 200
        mock_cache.delete.assert_any_call(f"jwt_user:{target_id}")


class TestAdminResetMfa:
    """POST /api/auth/users/<id>/reset-mfa — admin-only.
    Deletes the user_mfa row. Self-target blocked.
    Audit: user_mfa_reset_by_admin."""

    def test_reset_mfa_success(self, client, auth_headers, auth_mocks):
        target_id = "ccccccaa-cccc-cccc-cccc-cccccccccccc"
        target = _make_db_user(id=target_id, username="carol")
        auth_mocks["user_repo"].get_user_by_id.return_value = target

        resp = client.post(
            f"/api/auth/users/{target_id}/reset-mfa",
            headers=auth_headers("admin"),
            json={},
        )
        assert resp.status_code == 200
        auth_mocks["mfa_repo"].delete_all_methods.assert_called_once()
        actions = [
            c.kwargs.get("action") for c in auth_mocks["audit_svc"].log_auth.call_args_list
        ]
        assert "user_mfa_reset_by_admin" in actions

    def test_reset_mfa_self_target_blocked(self, client, auth_headers, auth_mocks):
        # The auth_headers fixture issues TEST_USER_UUID as the caller.
        target_id = TEST_USER_UUID
        target = _make_db_user(id=target_id, username="testuser")
        auth_mocks["user_repo"].get_user_by_id.return_value = target

        resp = client.post(
            f"/api/auth/users/{target_id}/reset-mfa",
            headers=auth_headers("admin"),
            json={},
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["error"] == "cannot_reset_own_mfa"
        auth_mocks["mfa_repo"].delete_all_methods.assert_not_called()

    def test_reset_mfa_404_when_target_missing(self, client, auth_headers, auth_mocks):
        auth_mocks["user_repo"].get_user_by_id.return_value = None
        target_id = "ddddddaa-dddd-dddd-dddd-dddddddddddd"
        resp = client.post(
            f"/api/auth/users/{target_id}/reset-mfa",
            headers=auth_headers("admin"),
            json={},
        )
        assert resp.status_code == 404

    def test_reset_mfa_403_when_caller_not_admin(
        self, client, auth_headers, _prime_regular_user
    ):
        target_id = "eeeeeeaa-eeee-eeee-eeee-eeeeeeeeeeee"
        resp = client.post(
            f"/api/auth/users/{target_id}/reset-mfa",
            headers=auth_headers("user"),
            json={},
        )
        assert resp.status_code == 403

    def test_reset_mfa_invalidates_jwt_user_cache(
        self, client, auth_headers, auth_mocks
    ):
        # Without cache invalidation, MFA-state checks against the cached
        # jwt_user keep passing for up to 60s after the admin reset.
        target_id = "ffffffaa-ffff-ffff-ffff-ffffffffffff"
        target = _make_db_user(id=target_id, username="dora")
        auth_mocks["user_repo"].get_user_by_id.return_value = target

        with patch("src.api.blueprints.auth.cache") as mock_cache:
            resp = client.post(
                f"/api/auth/users/{target_id}/reset-mfa",
                headers=auth_headers("admin"),
                json={},
            )
        assert resp.status_code == 200
        mock_cache.delete.assert_any_call(f"jwt_user:{target_id}")


class TestUpdateUserAuditSplit:
    """PUT /api/auth/users/<id> emits one audit row per changed field
    (#614). Role changes keep emitting `user_updated`."""

    def _setup(self, auth_mocks, target_id, **overrides):
        target = _make_db_user(id=target_id, **overrides)
        auth_mocks["user_repo"].get_user_by_id.return_value = target
        auth_mocks["user_repo"].get_user_by_email.return_value = None
        auth_mocks["user_repo"].update_user.return_value = target
        return target

    def test_email_change_emits_email_audit(self, client, auth_headers, auth_mocks):
        target_id = "ffffff11-ffff-ffff-ffff-ffffffffffff"
        self._setup(auth_mocks, target_id)
        resp = client.put(
            f"/api/auth/users/{target_id}",
            headers=auth_headers("admin"),
            json={"email": "new@x.com"},
        )
        assert resp.status_code == 200
        actions = [
            c.kwargs.get("action") for c in auth_mocks["audit_svc"].log_auth.call_args_list
        ]
        assert "user_email_changed_by_admin" in actions
        assert "user_display_name_changed_by_admin" not in actions
        assert "user_updated" not in actions

    def test_full_name_change_emits_display_name_audit(
        self, client, auth_headers, auth_mocks
    ):
        target_id = "ffffff22-ffff-ffff-ffff-ffffffffffff"
        self._setup(auth_mocks, target_id)
        resp = client.put(
            f"/api/auth/users/{target_id}",
            headers=auth_headers("admin"),
            json={"full_name": "New Name"},
        )
        assert resp.status_code == 200
        actions = [
            c.kwargs.get("action") for c in auth_mocks["audit_svc"].log_auth.call_args_list
        ]
        assert "user_display_name_changed_by_admin" in actions
        assert "user_email_changed_by_admin" not in actions
        assert "user_updated" not in actions

    def test_role_change_keeps_user_updated_action(
        self, client, auth_headers, auth_mocks
    ):
        target_id = "ffffff33-ffff-ffff-ffff-ffffffffffff"
        self._setup(auth_mocks, target_id, role="user")
        resp = client.put(
            f"/api/auth/users/{target_id}",
            headers=auth_headers("admin"),
            json={"role": "admin"},
        )
        assert resp.status_code == 200
        actions = [
            c.kwargs.get("action") for c in auth_mocks["audit_svc"].log_auth.call_args_list
        ]
        assert "user_updated" in actions

    def test_combined_email_and_full_name_emits_both(
        self, client, auth_headers, auth_mocks
    ):
        target_id = "ffffff44-ffff-ffff-ffff-ffffffffffff"
        self._setup(auth_mocks, target_id)
        resp = client.put(
            f"/api/auth/users/{target_id}",
            headers=auth_headers("admin"),
            json={"email": "x@y.com", "full_name": "Y"},
        )
        assert resp.status_code == 200
        actions = [
            c.kwargs.get("action") for c in auth_mocks["audit_svc"].log_auth.call_args_list
        ]
        assert "user_email_changed_by_admin" in actions
        assert "user_display_name_changed_by_admin" in actions
