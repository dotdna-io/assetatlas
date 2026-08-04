# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for MfaService TOTP logic."""
import pytest


def test_pyotp_is_importable():
    """Smoke test: pyotp dep is installed."""
    import pyotp
    assert hasattr(pyotp, 'TOTP')
    assert hasattr(pyotp, 'random_base32')


import re
from src.auth.services.mfa_service import MfaService


class TestSecretGeneration:
    def test_generate_secret_returns_base32(self):
        secret = MfaService.generate_secret()
        # pyotp default is 32 chars (160 bits) base32
        assert len(secret) == 32
        assert re.match(r'^[A-Z2-7]+$', secret)

    def test_generate_secret_is_random(self):
        secrets = {MfaService.generate_secret() for _ in range(10)}
        assert len(secrets) == 10  # all unique


class TestProvisioningURI:
    def test_provisioning_uri_contains_username_and_issuer(self):
        secret = 'JBSWY3DPEHPK3PXP'  # known base32
        uri = MfaService.provisioning_uri(secret, username='alice', issuer='AssetAtlas')
        assert uri.startswith('otpauth://totp/AssetAtlas:alice?')
        assert 'secret=JBSWY3DPEHPK3PXP' in uri
        assert 'issuer=AssetAtlas' in uri


class TestVerifyTotp:
    def test_verify_correct_code(self):
        import pyotp
        secret = 'JBSWY3DPEHPK3PXP'
        code = pyotp.TOTP(secret).now()
        assert MfaService.verify_totp(secret, code) is True

    def test_verify_wrong_code(self):
        secret = 'JBSWY3DPEHPK3PXP'
        assert MfaService.verify_totp(secret, '000000') is False

    def test_verify_rejects_non_numeric(self):
        secret = 'JBSWY3DPEHPK3PXP'
        assert MfaService.verify_totp(secret, 'abcdef') is False

    def test_verify_rejects_wrong_length(self):
        secret = 'JBSWY3DPEHPK3PXP'
        assert MfaService.verify_totp(secret, '1234') is False
        assert MfaService.verify_totp(secret, '12345678') is False

    def test_verify_accepts_one_window_drift(self, monkeypatch):
        """Code from the previous 30s window is accepted (clock-skew tolerance)."""
        import datetime
        import pyotp
        secret = 'JBSWY3DPEHPK3PXP'
        totp = pyotp.TOTP(secret)

        # Generate a code at a fixed epoch then verify from 30s ahead.
        # pyotp.TOTP.verify accepts an explicit for_time; we patch the inner verify
        # call via monkeypatching pyotp.TOTP.verify to forward a fixed for_time.
        fixed_now = 1_700_000_000
        code_at_now = totp.at(fixed_now)
        for_time_ahead_one = datetime.datetime.fromtimestamp(fixed_now + 30)

        original_verify = pyotp.TOTP.verify

        def patched_verify(self, otp, for_time=None, valid_window=0):
            return original_verify(self, otp, for_time=for_time_ahead_one, valid_window=valid_window)

        monkeypatch.setattr(pyotp.TOTP, 'verify', patched_verify)
        assert MfaService.verify_totp(secret, code_at_now) is True

    def test_verify_rejects_two_window_drift(self, monkeypatch):
        """Code from two windows ago is rejected (outside ±1 tolerance)."""
        import datetime
        import pyotp
        secret = 'JBSWY3DPEHPK3PXP'
        totp = pyotp.TOTP(secret)
        fixed_now = 1_700_000_000
        code_at_now = totp.at(fixed_now)
        # 90s ahead = 3 windows forward — outside ±1 tolerance
        for_time_ahead_three = datetime.datetime.fromtimestamp(fixed_now + 90)

        original_verify = pyotp.TOTP.verify

        def patched_verify(self, otp, for_time=None, valid_window=0):
            return original_verify(self, otp, for_time=for_time_ahead_three, valid_window=valid_window)

        monkeypatch.setattr(pyotp.TOTP, 'verify', patched_verify)
        assert MfaService.verify_totp(secret, code_at_now) is False


class TestEncryption:
    def test_encrypt_then_decrypt_roundtrip(self):
        secret = 'JBSWY3DPEHPK3PXP'
        encrypted = MfaService.encrypt_secret(secret)
        assert encrypted != secret
        assert encrypted.startswith('ENC:')
        assert MfaService.decrypt_secret(encrypted) == secret

    def test_encrypt_secret_idempotent(self):
        # Already-encrypted values pass through
        encrypted = MfaService.encrypt_secret('JBSWY3DPEHPK3PXP')
        twice = MfaService.encrypt_secret(encrypted)
        assert MfaService.decrypt_secret(twice) == 'JBSWY3DPEHPK3PXP'


class TestRequiresMfa:
    def test_local_user_requires_mfa(self):
        assert MfaService.requires_mfa(auth_method='local') is True

    def test_ldap_user_does_not_require_mfa(self):
        assert MfaService.requires_mfa(auth_method='ldap') is False

    def test_oidc_user_does_not_require_mfa(self):
        assert MfaService.requires_mfa(auth_method='oidc') is False

    def test_unknown_method_does_not_require_mfa(self):
        assert MfaService.requires_mfa(auth_method='unknown') is False
