# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for SecuritySettingsHooks — public_base_url validation.
Pure-Python; no Flask app; no DB.
"""
import pytest

from src.api.blueprints.settings.category_hooks import (
    HookValidationError,
    SecuritySettingsHooks,
)


@pytest.fixture
def hooks() -> SecuritySettingsHooks:
    return SecuritySettingsHooks()


class TestValidatePublicBaseUrl:
    def test_accepts_https_url(self, hooks):
        assert (
            hooks.validate('public_base_url', 'https://assetatlas.example.com')
            == 'https://assetatlas.example.com'
        )

    def test_strips_trailing_slash(self, hooks):
        assert (
            hooks.validate('public_base_url', 'https://assetatlas.example.com/')
            == 'https://assetatlas.example.com'
        )

    def test_accepts_https_with_port(self, hooks):
        # Covers deployments where the public URL exposes a non-standard HTTPS port.
        assert (
            hooks.validate('public_base_url', 'https://192.0.2.1:8443')
            == 'https://192.0.2.1:8443'
        )

    def test_accepts_https_with_path_prefix(self, hooks):
        # Mirrors path-based reverse-proxy deployments.
        assert (
            hooks.validate(
                'public_base_url', 'https://shared.example.com/assetatlas',
            ) == 'https://shared.example.com/assetatlas'
        )

    def test_accepts_empty(self, hooks):
        # Empty = "use the env var fallback"; valid at write time.
        assert hooks.validate('public_base_url', '') == ''

    def test_accepts_none_as_empty(self, hooks):
        assert hooks.validate('public_base_url', None) == ''

    def test_rejects_http(self, hooks):
        with pytest.raises(HookValidationError, match='HTTPS'):
            hooks.validate('public_base_url', 'http://localhost:5001')

    def test_rejects_non_string(self, hooks):
        with pytest.raises(HookValidationError, match='must be a string'):
            hooks.validate('public_base_url', 12345)

    def test_rejects_url_without_hostname(self, hooks):
        with pytest.raises(HookValidationError, match='hostname'):
            hooks.validate('public_base_url', 'https://')

    def test_rejects_other_schemes(self, hooks):
        for value in ('ftp://example.com', 'javascript:alert(1)', 'file:///etc'):
            with pytest.raises(HookValidationError):
                hooks.validate('public_base_url', value)

    def test_unknown_key_passthrough(self, hooks):
        # session_timeout_minutes etc. fall through to SettingsManager validation.
        assert hooks.validate('session_timeout_minutes', 60) == 60


class TestPreStoreAndAudit:
    def test_pre_store_passthrough(self, hooks):
        # No encryption / transformation; the value lands in the DB as-is.
        assert hooks.pre_store('public_base_url', 'https://x.example.com') == (
            'https://x.example.com'
        )

    def test_audit_redact_returns_false(self, hooks):
        # Public URLs aren't secret; audit log must not redact them.
        assert hooks.audit_redact('public_base_url') is False
