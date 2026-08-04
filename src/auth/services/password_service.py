# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Password hashing and validation service using bcrypt.

Provides secure password hashing with bcrypt (12 rounds) and
comprehensive password strength validation.
"""
import bcrypt
import re
from typing import Dict, List


class PasswordService:
    """Handles password hashing, verification, and strength validation."""

    BCRYPT_ROUNDS = 12
    MIN_LENGTH = 8
    MAX_LENGTH = 128  # DoS prevention

    @staticmethod
    def hash_password(password: str) -> str:
        """
        Hash password using bcrypt with 12 rounds.

        Args:
            password: Plaintext password

        Returns:
            Hashed password string (bcrypt hash)

        Raises:
            ValueError: If password is empty or exceeds max length
        """
        if not password:
            raise ValueError("Password cannot be empty")

        if len(password) > PasswordService.MAX_LENGTH:
            raise ValueError(
                f"Password exceeds maximum length of {PasswordService.MAX_LENGTH} characters"
            )

        password_bytes = password.encode('utf-8')
        salt = bcrypt.gensalt(rounds=PasswordService.BCRYPT_ROUNDS)
        return bcrypt.hashpw(password_bytes, salt).decode('utf-8')

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        """
        Verify password against bcrypt hash.

        Args:
            password: Plaintext password to verify
            password_hash: Stored bcrypt hash

        Returns:
            True if password matches hash, False otherwise

        Raises:
            ValueError: If password or hash is empty
        """
        if not password or not password_hash:
            raise ValueError("Password and hash cannot be empty")

        try:
            password_bytes = password.encode('utf-8')
            hash_bytes = password_hash.encode('utf-8')
            return bcrypt.checkpw(password_bytes, hash_bytes)
        except Exception as e:
            # Log the error but don't expose details to caller
            # In production, log to proper logging system
            return False

    @staticmethod
    def validate_password_strength(password: str) -> Dict[str, any]:
        """
        Validate password meets security requirements.

        Requirements:
        - 8-128 characters
        - At least one uppercase letter
        - At least one lowercase letter
        - At least one digit
        - At least one special character (!@#$%^&*()_+-=[]{};':"\\|,.<>/?)

        Args:
            password: Password to validate

        Returns:
            Dictionary with validation results:
            {
                'valid': bool,
                'errors': List[str],
                'score': int (0-5, based on requirements met)
            }
        """
        errors = []
        score = 0

        # Length check
        if len(password) < PasswordService.MIN_LENGTH:
            errors.append(
                f'Password must be at least {PasswordService.MIN_LENGTH} characters'
            )
        else:
            score += 1

        if len(password) > PasswordService.MAX_LENGTH:
            errors.append(
                f'Password must be at most {PasswordService.MAX_LENGTH} characters'
            )

        # Character requirements
        if not re.search(r'[A-Z]', password):
            errors.append('Password must contain at least one uppercase letter')
        else:
            score += 1

        if not re.search(r'[a-z]', password):
            errors.append('Password must contain at least one lowercase letter')
        else:
            score += 1

        if not re.search(r'\d', password):
            errors.append('Password must contain at least one digit')
        else:
            score += 1

        # Special-character set kept byte-for-byte in sync with the UI password
        # meter (ui/prod/src/components/shared/PasswordStrengthMeter.tsx). A
        # narrower set here than the UI shows silently rejects passwords the
        # meter marked valid (regression: onboarding 400s, 2026-06-03). Any
        # change here MUST be mirrored there, and vice versa.
        if not re.search(r"""[!@#$%^&*()_+\-=[\]{};':"\\|,.<>/?]""", password):
            errors.append('Password must contain at least one special character')
        else:
            score += 1

        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'score': score
        }

    @staticmethod
    def get_password_strength_label(score: int) -> str:
        """
        Get human-readable label for password strength score.

        Args:
            score: Password strength score (0-5)

        Returns:
            String label: 'very weak', 'weak', 'fair', 'good', 'strong'
        """
        if score <= 1:
            return 'very weak'
        elif score == 2:
            return 'weak'
        elif score == 3:
            return 'fair'
        elif score == 4:
            return 'good'
        else:
            return 'strong'
