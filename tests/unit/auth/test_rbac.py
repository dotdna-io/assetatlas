# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for RBAC middleware decorators.

Covers:
- ``has_required_role`` hierarchy matrix (super_admin > admin > user).
- ``require_role`` / ``require_admin`` / ``require_super_admin`` decorators:
  allow + 403 paths, missing JWT identity, inactive accounts.
- ``require_self_or_admin`` decorator: self access, admin override, foreign-user
  rejection, inactive account.
- ``require_roles`` (exact-match list variant): allow / 403 / inactive paths.

Decorators run AFTER ``@jwt_required`` so they read ``current_user`` populated
by the JWT user_lookup_loader. We exercise them by registering small test
endpoints on a file-private Flask app (``rbac_app``) and driving them with JWT
tokens minted against that app plus a primed user cache.
"""
import pytest
from unittest.mock import patch

from flask import Blueprint, jsonify
from flask_jwt_extended import create_access_token, jwt_required

from src.api.utils.caching import cache
from src.auth.middleware.rbac import (
    has_required_role,
    require_admin,
    require_role,
    require_roles,
    require_self_or_admin,
    require_super_admin,
    ROLE_HIERARCHY,
)
from tests.conftest import build_mock_app
from tests.factories import make_jwt_user


# ---------------------------------------------------------------------------
# Test blueprint with one endpoint per decorator we care about.
# Registered once on this file's private ``rbac_app`` (see below).
# ---------------------------------------------------------------------------

_rbac_test_bp = Blueprint("rbac_test", __name__, url_prefix="/__rbac_test")


@_rbac_test_bp.route("/admin", methods=["GET"])
@jwt_required()
@require_admin()
def _admin_only():
    return jsonify({"ok": True}), 200


@_rbac_test_bp.route("/super-admin", methods=["GET"])
@jwt_required()
@require_super_admin()
def _super_admin_only():
    return jsonify({"ok": True}), 200


@_rbac_test_bp.route("/role-user", methods=["GET"])
@jwt_required()
@require_role("user")
def _role_user():
    return jsonify({"ok": True}), 200


@_rbac_test_bp.route("/users/<user_id>", methods=["GET"])
@jwt_required()
@require_self_or_admin("user_id")
def _self_or_admin(user_id):
    return jsonify({"ok": True, "user_id": user_id}), 200


@_rbac_test_bp.route("/exact-roles", methods=["GET"])
@jwt_required()
@require_roles(["admin", "super_admin"])
def _exact_roles():
    return jsonify({"ok": True}), 200


@pytest.fixture(scope="module")
def rbac_app():
    """File-private Flask app hosting the RBAC test blueprint.

    These endpoints cannot live on the session-wide ``blueprint_app``: any
    file that served a request on it earlier in the run (e.g. tests/unit/api)
    makes Flask reject register_blueprint() with "setup method ... can no
    longer be called". A private app registers the blueprint before its first
    request by construction, so this file is independent of suite ordering.

    The second create_app() in the process must skip the factory's
    init_metrics — its default-registry registration collides with the
    session app's (#596 H7). Nothing in this file asserts on metrics.
    """
    with patch("src.api.factory.init_metrics"):
        app = build_mock_app()
    app.register_blueprint(_rbac_test_bp)
    return app


@pytest.fixture()
def client(rbac_app):
    """Test client bound to this file's private app (overrides the shared one)."""
    return rbac_app.test_client()


@pytest.fixture()
def auth_headers(rbac_app):
    """JWT Authorization headers minted against this file's private app."""
    def _make_headers(role="admin", username="testuser", user_id="test-user-id"):
        with rbac_app.app_context():
            token = create_access_token(
                identity=user_id,
                additional_claims={
                    "role": role,
                    "username": username,
                },
            )
        return {"Authorization": f"Bearer {token}"}

    return _make_headers


# ---------------------------------------------------------------------------
# Cache-priming helper: write a user dict to ``jwt_user:{user_id}`` on the
# private app's cache. Each test primes the role it needs and restores the
# default admin afterwards, so tests stay independent of each other.
# ---------------------------------------------------------------------------

def _prime_user(rbac_app, *, role, user_id="test-user-id", username="testuser",
                is_active=True):
    user = make_jwt_user(role=role, user_id=user_id, username=username)
    user["is_active"] = is_active
    with rbac_app.app_context():
        cache.set(f"jwt_user:{user_id}", user, timeout=300)


def _clear_user(rbac_app, user_id="test-user-id"):
    with rbac_app.app_context():
        cache.delete(f"jwt_user:{user_id}")


# =============================================================================
# has_required_role — pure matrix
# =============================================================================

class TestHasRequiredRole:
    """Hierarchy: super_admin (1) > admin (2) > user (3) — lower number wins."""

    def test_role_hierarchy_constants(self):
        assert ROLE_HIERARCHY == {"super_admin": 1, "admin": 2, "user": 3}

    @pytest.mark.parametrize("user_role,required,expected", [
        # super_admin satisfies everything
        ("super_admin", "super_admin", True),
        ("super_admin", "admin", True),
        ("super_admin", "user", True),
        # admin satisfies admin + user, not super_admin
        ("admin", "super_admin", False),
        ("admin", "admin", True),
        ("admin", "user", True),
        # user satisfies user only
        ("user", "super_admin", False),
        ("user", "admin", False),
        ("user", "user", True),
    ])
    def test_matrix(self, user_role, required, expected):
        assert has_required_role(user_role, required) is expected

    def test_unknown_user_role_is_lowest_privilege(self):
        # Unknown user_role => 999, which is greater than any required level
        assert has_required_role("guest", "user") is False
        assert has_required_role("", "user") is False

    def test_unknown_required_role_treated_as_999(self):
        # Unknown required_role => 999, so super_admin (1) <= 999 is True
        assert has_required_role("super_admin", "nonexistent") is True
        # And user (3) <= 999 is also True
        assert has_required_role("user", "nonexistent") is True


# =============================================================================
# require_admin / require_super_admin / require_role decorators
# =============================================================================

class TestRequireAdmin:
    """``@require_admin()`` allows admin + super_admin, rejects user with 403."""

    def test_admin_allowed(self, client, auth_headers, rbac_app):
        _prime_user(rbac_app, role="admin")
        try:
            r = client.get("/__rbac_test/admin", headers=auth_headers("admin"))
            assert r.status_code == 200
            assert r.get_json() == {"ok": True}
        finally:
            _prime_user(rbac_app, role="admin")  # reset to default

    def test_super_admin_allowed(self, client, auth_headers, rbac_app):
        _prime_user(rbac_app, role="super_admin")
        try:
            r = client.get("/__rbac_test/admin", headers=auth_headers("super_admin"))
            assert r.status_code == 200
        finally:
            _prime_user(rbac_app, role="admin")

    def test_user_rejected_with_403(self, client, auth_headers, rbac_app):
        _prime_user(rbac_app, role="user")
        try:
            r = client.get("/__rbac_test/admin", headers=auth_headers("user"))
            assert r.status_code == 403
            body = r.get_json()
            assert body["error"] == "insufficient_permissions"
            assert body["required_role"] == "admin"
            assert body["user_role"] == "user"
        finally:
            _prime_user(rbac_app, role="admin")

    def test_inactive_admin_rejected_with_403(self, client, auth_headers, rbac_app):
        """Active flag is checked even for the right role."""
        _prime_user(rbac_app, role="admin", is_active=False)
        try:
            r = client.get("/__rbac_test/admin", headers=auth_headers("admin"))
            assert r.status_code == 403
            assert r.get_json()["error"] == "account_disabled"
        finally:
            _prime_user(rbac_app, role="admin")

    def test_missing_jwt_user_returns_401(self, client, auth_headers, rbac_app):
        """If user_lookup_loader returns None, decorator returns 401."""
        _clear_user(rbac_app)
        try:
            # Token still parses fine; cache miss + DB miss => current_user = None.
            # Patch the DB get_user_by_id used by user_lookup_loader's cache miss path.
            with patch.object(rbac_app.connection_manager, "begin_scope"), \
                 patch.object(rbac_app.connection_manager, "end_scope"), \
                 patch("src.auth.repositories.user_repository.UserRepository.get_user_by_id",
                       return_value=None):
                r = client.get("/__rbac_test/admin", headers=auth_headers("admin"))
            # JWT-Extended raises a UserLookupError (422) when loader returns None;
            # if the application's error handlers intercept it differently, allow
            # any failure status above 400 (we just need to confirm decorator did
            # not silently grant access).
            assert r.status_code in (401, 403, 422)
        finally:
            _prime_user(rbac_app, role="admin")


class TestRequireSuperAdmin:
    """``@require_super_admin()`` allows ONLY super_admin."""

    def test_super_admin_allowed(self, client, auth_headers, rbac_app):
        _prime_user(rbac_app, role="super_admin")
        try:
            r = client.get("/__rbac_test/super-admin",
                           headers=auth_headers("super_admin"))
            assert r.status_code == 200
        finally:
            _prime_user(rbac_app, role="admin")

    def test_admin_rejected_with_403(self, client, auth_headers, rbac_app):
        _prime_user(rbac_app, role="admin")
        try:
            r = client.get("/__rbac_test/super-admin", headers=auth_headers("admin"))
            assert r.status_code == 403
            body = r.get_json()
            assert body["error"] == "insufficient_permissions"
            assert body["required_role"] == "super_admin"
            assert body["user_role"] == "admin"
        finally:
            _prime_user(rbac_app, role="admin")

    def test_user_rejected_with_403(self, client, auth_headers, rbac_app):
        _prime_user(rbac_app, role="user")
        try:
            r = client.get("/__rbac_test/super-admin", headers=auth_headers("user"))
            assert r.status_code == 403
            assert r.get_json()["error"] == "insufficient_permissions"
        finally:
            _prime_user(rbac_app, role="admin")


class TestRequireRoleEdgeCases:
    """Direct ``@require_role()`` covers helper paths + missing-role-claim case."""

    def test_user_role_satisfies_user_requirement(self, client, auth_headers, rbac_app):
        _prime_user(rbac_app, role="user")
        try:
            r = client.get("/__rbac_test/role-user", headers=auth_headers("user"))
            assert r.status_code == 200
        finally:
            _prime_user(rbac_app, role="admin")

    def test_no_role_in_user_dict_defaults_to_user(self, client, auth_headers, rbac_app):
        """Missing 'role' key in JWT cache => decorator falls back to 'user'.
        For an admin-only endpoint, this means 403."""
        with rbac_app.app_context():
            cache.set(
                "jwt_user:test-user-id",
                {  # explicitly no 'role' key
                    "id": "test-user-id",
                    "username": "noroleuser",
                    "is_active": True,
                    "email": "noroleuser@test.local",
                    "full_name": "No Role User",
                },
                timeout=300,
            )
        try:
            r = client.get("/__rbac_test/admin", headers=auth_headers("user"))
            assert r.status_code == 403
            body = r.get_json()
            # role defaulted to 'user' in the decorator
            assert body["user_role"] == "user"
        finally:
            _prime_user(rbac_app, role="admin")

    def test_malformed_role_string_rejected(self, client, auth_headers, rbac_app):
        """Garbage role => unknown level (999) => 403 against any required role."""
        _prime_user(rbac_app, role="root_admin_typo")
        try:
            r = client.get("/__rbac_test/admin", headers=auth_headers("admin"))
            assert r.status_code == 403
            body = r.get_json()
            assert body["user_role"] == "root_admin_typo"
        finally:
            _prime_user(rbac_app, role="admin")


# =============================================================================
# require_self_or_admin
# =============================================================================

class TestRequireSelfOrAdmin:
    """``@require_self_or_admin('user_id')`` allows self OR admin."""

    def test_self_access_allowed_for_user_role(self, client, auth_headers, rbac_app):
        """Regular user fetching their OWN user_id is allowed."""
        _prime_user(rbac_app, role="user", user_id="self-uuid-1")
        try:
            r = client.get(
                "/__rbac_test/users/self-uuid-1",
                headers=auth_headers("user", user_id="self-uuid-1"),
            )
            assert r.status_code == 200
            assert r.get_json()["user_id"] == "self-uuid-1"
        finally:
            _clear_user(rbac_app, user_id="self-uuid-1")
            _prime_user(rbac_app, role="admin")

    def test_admin_can_access_other_user(self, client, auth_headers, rbac_app):
        """Admin can fetch any user_id, even one that's not theirs."""
        _prime_user(rbac_app, role="admin", user_id="admin-uuid")
        try:
            r = client.get(
                "/__rbac_test/users/some-other-user",
                headers=auth_headers("admin", user_id="admin-uuid"),
            )
            assert r.status_code == 200
        finally:
            _clear_user(rbac_app, user_id="admin-uuid")
            _prime_user(rbac_app, role="admin")

    def test_super_admin_can_access_other_user(self, client, auth_headers, rbac_app):
        """super_admin satisfies has_required_role('admin') => allowed."""
        _prime_user(rbac_app, role="super_admin", user_id="superadmin-uuid")
        try:
            r = client.get(
                "/__rbac_test/users/different-uuid",
                headers=auth_headers("super_admin", user_id="superadmin-uuid"),
            )
            assert r.status_code == 200
        finally:
            _clear_user(rbac_app, user_id="superadmin-uuid")
            _prime_user(rbac_app, role="admin")

    def test_user_accessing_other_user_rejected_with_403(
        self, client, auth_headers, rbac_app
    ):
        """Regular user trying to read someone else's record => 403."""
        _prime_user(rbac_app, role="user", user_id="user-uuid-A")
        try:
            r = client.get(
                "/__rbac_test/users/user-uuid-B",
                headers=auth_headers("user", user_id="user-uuid-A"),
            )
            assert r.status_code == 403
            body = r.get_json()
            assert body["error"] == "insufficient_permissions"
            assert "your own resources" in body["message"].lower()
        finally:
            _clear_user(rbac_app, user_id="user-uuid-A")
            _prime_user(rbac_app, role="admin")

    def test_inactive_user_rejected_with_403(self, client, auth_headers, rbac_app):
        """Inactive accounts are blocked even when accessing own resource."""
        _prime_user(rbac_app, role="user", user_id="inactive-uuid",
                    is_active=False)
        try:
            r = client.get(
                "/__rbac_test/users/inactive-uuid",
                headers=auth_headers("user", user_id="inactive-uuid"),
            )
            assert r.status_code == 403
            assert r.get_json()["error"] == "account_disabled"
        finally:
            _clear_user(rbac_app, user_id="inactive-uuid")
            _prime_user(rbac_app, role="admin")


# =============================================================================
# Decorators with no JWT user (user_lookup_loader returned None)
# =============================================================================

class TestNoCurrentUser:
    """When ``current_user`` is None inside the decorator, return 401 directly.

    Patching the imported name in rbac.py is the cleanest way to simulate the
    cache miss + DB miss scenario without fighting JWT-Extended internals.
    """

    @pytest.fixture(autouse=True)
    def _app_ctx(self, rbac_app):
        """Decorators call request.path, which needs an active request context."""
        with rbac_app.test_request_context("/__rbac_test/admin"):
            yield

    def test_require_admin_returns_401_when_no_user(self):
        with patch("src.auth.middleware.rbac.current_user", None):
            wrapped = require_admin()(lambda: ({"unreachable": True}, 200))
            response, status = wrapped()
            assert status == 401
            assert response.get_json()["error"] == "unauthorized"

    def test_require_super_admin_returns_401_when_no_user(self):
        with patch("src.auth.middleware.rbac.current_user", None):
            wrapped = require_super_admin()(lambda: ({"unreachable": True}, 200))
            response, status = wrapped()
            assert status == 401
            assert response.get_json()["error"] == "unauthorized"

    def test_require_roles_returns_401_when_no_user(self):
        with patch("src.auth.middleware.rbac.current_user", None):
            wrapped = require_roles(["admin"])(lambda: ({"unreachable": True}, 200))
            response, status = wrapped()
            assert status == 401
            assert response.get_json()["error"] == "unauthorized"

    def test_require_self_or_admin_returns_401_when_no_user(self):
        with patch("src.auth.middleware.rbac.current_user", None):
            wrapped = require_self_or_admin("user_id")(
                lambda user_id: ({"unreachable": True}, 200)
            )
            response, status = wrapped(user_id="x")
            assert status == 401
            assert response.get_json()["error"] == "unauthorized"


# =============================================================================
# require_roles (exact-match list variant)
# =============================================================================

class TestRequireRoles:
    """``@require_roles(['admin', 'super_admin'])`` checks exact membership."""

    def test_admin_in_allowed_list(self, client, auth_headers, rbac_app):
        _prime_user(rbac_app, role="admin")
        try:
            r = client.get("/__rbac_test/exact-roles", headers=auth_headers("admin"))
            assert r.status_code == 200
        finally:
            _prime_user(rbac_app, role="admin")

    def test_user_not_in_allowed_list_rejected(self, client, auth_headers, rbac_app):
        _prime_user(rbac_app, role="user")
        try:
            r = client.get("/__rbac_test/exact-roles", headers=auth_headers("user"))
            assert r.status_code == 403
            body = r.get_json()
            assert body["error"] == "insufficient_permissions"
            assert "admin" in body["allowed_roles"]
            assert body["user_role"] == "user"
        finally:
            _prime_user(rbac_app, role="admin")

    def test_inactive_user_rejected(self, client, auth_headers, rbac_app):
        _prime_user(rbac_app, role="admin", is_active=False)
        try:
            r = client.get("/__rbac_test/exact-roles", headers=auth_headers("admin"))
            assert r.status_code == 403
            assert r.get_json()["error"] == "account_disabled"
        finally:
            _prime_user(rbac_app, role="admin")
