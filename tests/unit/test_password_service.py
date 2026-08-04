# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for PasswordService from password_service.py.

Pure logic tests -- only depends on bcrypt (no Flask, DB, etc.).
"""

import pytest

from src.auth.services.password_service import PasswordService

# Every character the UI password meter
# (ui/prod/src/components/shared/PasswordStrengthMeter.tsx) treats as a valid
# "special character". The backend MUST accept all of them, otherwise the meter
# green-lights a password that the server then rejects with a 400 -- which is
# exactly what blocked onboarding on 2026-06-03 (a '_'-only special char).
UI_SPECIAL_CHARS = "!@#$%^&*()_+-=[]{};':\"\\|,.<>/?"


# ============================================================================
# hash_password / verify_password
# ============================================================================


class TestHashAndVerify:
    """Tests for hash_password() and verify_password()."""

    def test_hash_returns_string(self):
        h = PasswordService.hash_password("Secret1!")
        assert isinstance(h, str)

    def test_hash_is_not_plaintext(self):
        password = "MyP@ssw0rd"
        h = PasswordService.hash_password(password)
        assert h != password

    def test_correct_password_verifies(self):
        password = "Correct!Horse1"
        h = PasswordService.hash_password(password)
        assert PasswordService.verify_password(password, h) is True

    def test_wrong_password_fails(self):
        h = PasswordService.hash_password("RightPass1!")
        assert PasswordService.verify_password("WrongPass1!", h) is False

    def test_different_hashes_for_same_password(self):
        """bcrypt salting produces different hashes each time."""
        password = "SamePass1!"
        h1 = PasswordService.hash_password(password)
        h2 = PasswordService.hash_password(password)
        assert h1 != h2
        # Both should still verify
        assert PasswordService.verify_password(password, h1) is True
        assert PasswordService.verify_password(password, h2) is True

    def test_hash_empty_password_raises(self):
        with pytest.raises(ValueError, match="empty"):
            PasswordService.hash_password("")

    def test_verify_empty_password_raises(self):
        with pytest.raises(ValueError):
            PasswordService.verify_password("", "somehash")

    def test_verify_empty_hash_raises(self):
        with pytest.raises(ValueError):
            PasswordService.verify_password("password", "")


# ============================================================================
# validate_password_strength
# ============================================================================


class TestValidatePasswordStrength:
    """Tests for validate_password_strength()."""

    def test_strong_password_passes(self):
        result = PasswordService.validate_password_strength("Str0ng!Pass")
        assert result["valid"] is True
        assert result["errors"] == []
        assert result["score"] == 5

    def test_too_short_fails(self):
        result = PasswordService.validate_password_strength("Ab1!")
        assert result["valid"] is False
        assert any("at least" in e for e in result["errors"])

    def test_empty_password_fails(self):
        result = PasswordService.validate_password_strength("")
        assert result["valid"] is False
        assert result["score"] == 0

    def test_returns_expected_keys(self):
        result = PasswordService.validate_password_strength("anything")
        assert "valid" in result
        assert "errors" in result
        assert "score" in result

    def test_missing_uppercase(self):
        result = PasswordService.validate_password_strength("lowercase1!")
        assert result["valid"] is False
        assert any("uppercase" in e for e in result["errors"])

    def test_missing_lowercase(self):
        result = PasswordService.validate_password_strength("UPPERCASE1!")
        assert result["valid"] is False
        assert any("lowercase" in e for e in result["errors"])

    def test_missing_digit(self):
        result = PasswordService.validate_password_strength("NoDigits!Ab")
        assert result["valid"] is False
        assert any("digit" in e for e in result["errors"])

    def test_missing_special_char(self):
        result = PasswordService.validate_password_strength("NoSpecial1Ab")
        assert result["valid"] is False
        assert any("special" in e for e in result["errors"])

    def test_underscore_alone_satisfies_special_requirement(self):
        """Regression: an underscore is the only special character in this
        password. The UI meter accepted it but the backend rejected it,
        producing a 400 on /auth/onboarding/complete (victor.gomis,
        2026-06-03)."""
        result = PasswordService.validate_password_strength("Password1_")
        assert result["valid"] is True
        assert result["errors"] == []
        assert result["score"] == 5

    @pytest.mark.parametrize("special", list(UI_SPECIAL_CHARS))
    def test_every_ui_special_char_is_accepted(self, special):
        """The backend's special-char set must not be narrower than the UI's.
        'Passw0rd' already meets every other requirement, so validity here
        hinges solely on whether `special` counts as a special character."""
        result = PasswordService.validate_password_strength("Passw0rd" + special)
        assert result["valid"] is True, (
            f"{special!r} is a special character in the UI meter but the "
            f"backend rejects it -- the two validators have drifted"
        )

    def test_score_is_zero_to_five(self):
        # All requirements met
        assert PasswordService.validate_password_strength("Str0ng!Pass")["score"] == 5
        # Nothing met
        assert PasswordService.validate_password_strength("")["score"] == 0


# ============================================================================
# get_password_strength_label
# ============================================================================


class TestGetPasswordStrengthLabel:
    """Tests for get_password_strength_label()."""

    @pytest.mark.parametrize("score", [0, 1, 2, 3, 4, 5])
    def test_returns_non_empty_string(self, score):
        label = PasswordService.get_password_strength_label(score)
        assert isinstance(label, str)
        assert len(label) > 0

    def test_score_0_very_weak(self):
        assert PasswordService.get_password_strength_label(0) == "very weak"

    def test_score_1_very_weak(self):
        assert PasswordService.get_password_strength_label(1) == "very weak"

    def test_score_2_weak(self):
        assert PasswordService.get_password_strength_label(2) == "weak"

    def test_score_3_fair(self):
        assert PasswordService.get_password_strength_label(3) == "fair"

    def test_score_4_good(self):
        assert PasswordService.get_password_strength_label(4) == "good"

    def test_score_5_strong(self):
        assert PasswordService.get_password_strength_label(5) == "strong"
