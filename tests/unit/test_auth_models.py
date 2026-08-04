# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for authentication Pydantic models.

Tests LoginRequest, RegisterRequest, ChangePasswordRequest,
and the UserRole enum.
"""

import pytest
from pydantic import ValidationError

from src.auth.models.auth import (
    LoginRequest,
    RegisterRequest,
    ChangePasswordRequest,
    UserRole,
)


# ---------------------------------------------------------------------------
# LoginRequest
# ---------------------------------------------------------------------------

class TestLoginRequest:

    def test_valid_login(self):
        req = LoginRequest(username="admin", password="secret123")
        assert req.username == "admin"
        assert req.password == "secret123"

    def test_missing_username(self):
        with pytest.raises(ValidationError):
            LoginRequest(password="secret123")

    def test_missing_password(self):
        with pytest.raises(ValidationError):
            LoginRequest(username="admin")

    def test_empty_username_rejected(self):
        with pytest.raises(ValidationError):
            LoginRequest(username="", password="secret123")

    def test_empty_password_rejected(self):
        with pytest.raises(ValidationError):
            LoginRequest(username="admin", password="")


# ---------------------------------------------------------------------------
# RegisterRequest
# ---------------------------------------------------------------------------

class TestRegisterRequest:

    def test_valid_registration(self):
        req = RegisterRequest(
            username="john.doe",
            email="john@example.com",
            password="SecurePass1!",
        )
        # username is normalised to lowercase
        assert req.username == "john.doe"
        assert req.email == "john@example.com"

    def test_valid_with_full_name(self):
        req = RegisterRequest(
            username="john.doe",
            email="john@example.com",
            password="SecurePass1!",
            full_name="John Doe",
        )
        assert req.full_name == "John Doe"

    def test_invalid_email_rejected(self):
        with pytest.raises(ValidationError):
            RegisterRequest(
                username="john.doe",
                email="not-an-email",
                password="SecurePass1!",
            )

    def test_short_password_rejected(self):
        with pytest.raises(ValidationError):
            RegisterRequest(
                username="john.doe",
                email="john@example.com",
                password="short",
            )

    def test_short_username_rejected(self):
        with pytest.raises(ValidationError):
            RegisterRequest(
                username="ab",
                email="john@example.com",
                password="SecurePass1!",
            )

    def test_username_with_invalid_chars_rejected(self):
        with pytest.raises(ValidationError):
            RegisterRequest(
                username="john doe",
                email="john@example.com",
                password="SecurePass1!",
            )

    def test_username_starting_with_special_char_rejected(self):
        with pytest.raises(ValidationError):
            RegisterRequest(
                username=".john",
                email="john@example.com",
                password="SecurePass1!",
            )

    def test_username_ending_with_special_char_rejected(self):
        with pytest.raises(ValidationError):
            RegisterRequest(
                username="john-",
                email="john@example.com",
                password="SecurePass1!",
            )

    def test_username_normalised_to_lowercase(self):
        req = RegisterRequest(
            username="John.Doe",
            email="john@example.com",
            password="SecurePass1!",
        )
        assert req.username == "john.doe"


# ---------------------------------------------------------------------------
# ChangePasswordRequest
# ---------------------------------------------------------------------------

class TestChangePasswordRequest:

    def test_valid_change(self):
        req = ChangePasswordRequest(
            current_password="OldPass123!",
            new_password="NewPass456!",
        )
        assert req.current_password == "OldPass123!"
        assert req.new_password == "NewPass456!"

    def test_missing_new_password(self):
        with pytest.raises(ValidationError):
            ChangePasswordRequest(current_password="OldPass123!")

    def test_missing_current_password(self):
        with pytest.raises(ValidationError):
            ChangePasswordRequest(new_password="NewPass456!")

    def test_same_passwords_rejected(self):
        with pytest.raises(ValidationError):
            ChangePasswordRequest(
                current_password="SamePass123!",
                new_password="SamePass123!",
            )

    def test_short_new_password_rejected(self):
        with pytest.raises(ValidationError):
            ChangePasswordRequest(
                current_password="OldPass123!",
                new_password="short",
            )


# ---------------------------------------------------------------------------
# UserRole
# ---------------------------------------------------------------------------

class TestUserRole:

    def test_user_value(self):
        assert UserRole.user.value == "user"

    def test_admin_value(self):
        assert UserRole.admin.value == "admin"

    def test_super_admin_value(self):
        assert UserRole.super_admin.value == "super_admin"

    def test_all_roles_are_strings(self):
        for role in UserRole:
            assert isinstance(role.value, str)

    def test_role_count(self):
        assert len(UserRole) == 3
