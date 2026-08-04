# SPDX-License-Identifier: AGPL-3.0-or-later
"""MFA (TOTP) service.

Pure logic for TOTP secret generation/verification and Fernet encryption of stored
secrets. No DB dependencies — easily unit-testable.
"""
from typing import Optional

import pyotp

from src.modules.integrations.shared.encryption import (
    encrypt as _fernet_encrypt,
    decrypt as _fernet_decrypt,
)


class MfaService:
    """TOTP MFA helpers. All methods are stateless / static."""

    TOTP_VALID_WINDOW = 1  # accept codes from current ±1 30-second window (clock skew)
    ISSUER_NAME = 'AssetAtlas'

    # ----- secret generation / encryption ---------------------------------

    @staticmethod
    def generate_secret() -> str:
        """Generate a new base32-encoded TOTP secret (160 bits)."""
        return pyotp.random_base32()

    @staticmethod
    def encrypt_secret(secret: str) -> str:
        """Encrypt a TOTP secret for at-rest storage.

        Idempotent: values already prefixed with `ENC:` are returned unchanged
        (delegated to the Fernet helper). TOTP secrets are base32, which cannot
        produce that prefix, so this is safe for our use.
        """
        return _fernet_encrypt(secret)

    @staticmethod
    def decrypt_secret(encrypted_secret: str) -> str:
        """Decrypt a stored TOTP secret. Raises ValueError on tamper/corruption."""
        return _fernet_decrypt(encrypted_secret)

    # ----- provisioning URI -----------------------------------------------

    @staticmethod
    def provisioning_uri(secret: str, *, username: str, issuer: Optional[str] = None) -> str:
        """Build an otpauth:// URI for QR-code rendering by an authenticator app."""
        return pyotp.TOTP(secret).provisioning_uri(
            name=username,
            issuer_name=issuer or MfaService.ISSUER_NAME,
        )

    # ----- TOTP verification ----------------------------------------------

    @staticmethod
    def verify_totp(secret: str, code: str) -> bool:
        """Return True if `code` is a valid TOTP for `secret` within ±1 window."""
        if not code or not isinstance(code, str):
            return False
        normalized = code.strip().replace(' ', '')
        if not normalized.isdigit() or len(normalized) != 6:
            return False
        try:
            return pyotp.TOTP(secret).verify(normalized, valid_window=MfaService.TOTP_VALID_WINDOW)
        except Exception:
            return False

    # ----- enforcement -----------------------------------------------------

    @staticmethod
    def requires_mfa(*, auth_method: str) -> bool:
        """Return True if the user must enroll MFA. All local users are required; LDAP/OIDC are not."""
        return auth_method == 'local'
