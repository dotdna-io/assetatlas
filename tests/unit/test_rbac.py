# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for has_required_role() and ROLE_HIERARCHY from rbac.py.

Pure logic tests -- no Flask, JWT, or request context needed.
"""

import pytest

from src.auth.middleware.rbac import has_required_role, ROLE_HIERARCHY


class TestRoleHierarchy:
    """Verify the role hierarchy constants."""

    def test_super_admin_is_highest(self):
        assert ROLE_HIERARCHY["super_admin"] < ROLE_HIERARCHY["admin"]
        assert ROLE_HIERARCHY["super_admin"] < ROLE_HIERARCHY["user"]

    def test_admin_above_user(self):
        assert ROLE_HIERARCHY["admin"] < ROLE_HIERARCHY["user"]

    def test_three_roles_defined(self):
        assert set(ROLE_HIERARCHY.keys()) == {"super_admin", "admin", "user"}


class TestHasRequiredRole:
    """Tests for has_required_role()."""

    # --- Same role passes ---
    @pytest.mark.parametrize("role", ["super_admin", "admin", "user"])
    def test_same_role_passes(self, role):
        assert has_required_role(role, role) is True

    # --- Higher privilege passes ---
    def test_super_admin_passes_admin(self):
        assert has_required_role("super_admin", "admin") is True

    def test_super_admin_passes_user(self):
        assert has_required_role("super_admin", "user") is True

    def test_admin_passes_user(self):
        assert has_required_role("admin", "user") is True

    # --- Lower privilege fails ---
    def test_user_fails_admin(self):
        assert has_required_role("user", "admin") is False

    def test_user_fails_super_admin(self):
        assert has_required_role("user", "super_admin") is False

    def test_admin_fails_super_admin(self):
        assert has_required_role("admin", "super_admin") is False

    # --- Unknown roles ---
    def test_unknown_user_role_fails(self):
        assert has_required_role("unknown", "user") is False

    def test_known_user_unknown_required_passes(self):
        # Unknown required role gets level 999; any known role (level < 999) passes
        assert has_required_role("admin", "unknown") is True

    def test_both_unknown_roles_passes(self):
        # Both get level 999 -> 999 <= 999 -> True
        assert has_required_role("unknown", "unknown") is True

    def test_empty_string_role_fails(self):
        assert has_required_role("", "user") is False

    def test_none_user_role_fails(self):
        # None is not in ROLE_HIERARCHY -> level 999
        assert has_required_role(None, "user") is False
