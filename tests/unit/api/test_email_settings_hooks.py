# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for EmailSettingsHooks — validation, pre-store encryption, audit redaction.
Pure-Python; no Flask app; no DB.
"""
import os
import pytest

# Intentionally do NOT set ASSETATLAS_ENCRYPTION_KEY — the encryption module
# has a deterministic SHA-256 fallback derived from an internal salt, which
# is stable and sufficient for tests. Setting a fake key here would fail
# Fernet's base64 length validation.

from src.api.blueprints.settings.category_hooks import EmailSettingsHooks, HookValidationError
from src.modules.integrations.shared.encryption import is_encrypted, decrypt


@pytest.fixture
def hooks() -> EmailSettingsHooks:
    return EmailSettingsHooks()


# ── validate ────────────────────────────────────────────────────────────────

class TestValidate:
    def test_smtp_port_accepts_valid(self, hooks):
        assert hooks.validate('smtp_port', 587) == 587
        assert hooks.validate('smtp_port', 25) == 25
        assert hooks.validate('smtp_port', 65535) == 65535

    def test_smtp_port_rejects_out_of_range(self, hooks):
        with pytest.raises(HookValidationError):
            hooks.validate('smtp_port', 0)
        with pytest.raises(HookValidationError):
            hooks.validate('smtp_port', 99999)

    def test_smtp_security_accepts_enum(self, hooks):
        for v in ('none', 'starttls', 'tls'):
            assert hooks.validate('smtp_security', v) == v

    def test_smtp_security_rejects_other(self, hooks):
        with pytest.raises(HookValidationError):
            hooks.validate('smtp_security', 'ssl')

    def test_from_address_accepts_valid_email(self, hooks):
        assert hooks.validate('from_address', 'no-reply@example.com') == 'no-reply@example.com'

    def test_from_address_accepts_empty_string(self, hooks):
        # Empty is a legitimate "not yet configured" state.
        assert hooks.validate('from_address', '') == ''

    def test_from_address_rejects_garbage(self, hooks):
        with pytest.raises(HookValidationError):
            hooks.validate('from_address', 'not-an-email')

    def test_reply_to_address_accepts_empty(self, hooks):
        assert hooks.validate('reply_to_address', '') == ''

    def test_reply_to_address_validates_nonempty(self, hooks):
        with pytest.raises(HookValidationError):
            hooks.validate('reply_to_address', 'not-an-email')

    def test_unknown_key_passthrough(self, hooks):
        # Keys without specific validation (e.g. smtp_host, from_display_name) pass through.
        assert hooks.validate('smtp_host', 'anything.example.com') == 'anything.example.com'
        assert hooks.validate('from_display_name', 'AssetAtlas') == 'AssetAtlas'


# ── pre_store ───────────────────────────────────────────────────────────────

class TestPreStore:
    def test_smtp_password_empty_stays_empty(self, hooks):
        # Empty = explicit clear; no ENC prefix.
        assert hooks.pre_store('smtp_password', '') == ''

    def test_smtp_password_nonempty_is_encrypted(self, hooks):
        result = hooks.pre_store('smtp_password', 'supersecret')
        assert is_encrypted(result)
        assert decrypt(result) == 'supersecret'

    def test_smtp_password_sentinel_is_treated_as_plaintext(self, hooks):
        # The "__SET__" sentinel is UI-only. Server must not special-case it.
        result = hooks.pre_store('smtp_password', '__SET__')
        assert is_encrypted(result)
        assert decrypt(result) == '__SET__'

    def test_other_keys_passthrough(self, hooks):
        assert hooks.pre_store('smtp_host', 'smtp.example.com') == 'smtp.example.com'
        assert hooks.pre_store('smtp_port', 587) == 587


# ── audit_redact ────────────────────────────────────────────────────────────

class TestAuditRedact:
    def test_password_key_is_redacted(self, hooks):
        assert hooks.audit_redact('smtp_password') is True

    def test_other_keys_not_redacted(self, hooks):
        for key in ('smtp_host', 'smtp_port', 'smtp_username', 'from_address',
                    'from_display_name', 'reply_to_address',
                    'enabled', 'smtp_security'):
            assert hooks.audit_redact(key) is False
