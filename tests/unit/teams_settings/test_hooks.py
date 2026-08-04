# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the teams settings category schema + hooks."""
from __future__ import annotations

import pytest

from src.api.modules.settings.settings_schema import (
    APPLICATION_SETTINGS,
    get_setting_definition,
)
from src.api.blueprints.settings.category_hooks import (
    CATEGORY_HOOKS,
    HookValidationError,
)
from src.modules.integrations.shared.encryption import is_encrypted


class TestTeamsSchema:
    EXPECTED_KEYS = frozenset({
        'enabled',
        'bot_app_id',
        'bot_app_password',
        'bot_app_tenant_id',
        'messaging_endpoint',
    })

    def test_all_keys_present(self):
        from src.api.modules.settings.settings_schema import (
            get_category_settings,
        )
        actual = set(get_category_settings('teams').keys())
        assert actual == self.EXPECTED_KEYS, (
            f"Schema drift: missing={self.EXPECTED_KEYS - actual}, "
            f"unexpected={actual - self.EXPECTED_KEYS}"
        )

    def test_teams_category_exists(self):
        assert 'teams' in APPLICATION_SETTINGS

    def test_enabled_default_false(self):
        assert get_setting_definition('teams', 'enabled').default is False

    def test_credential_keys_default_empty_string(self):
        for key in ('bot_app_id', 'bot_app_password', 'bot_app_tenant_id'):
            assert get_setting_definition('teams', key).default == ''

    def test_all_credential_keys_are_strings(self):
        for key in ('bot_app_id', 'bot_app_password', 'bot_app_tenant_id'):
            assert get_setting_definition('teams', key).type == 'string'


_VALID_UUID = '11111111-1111-1111-1111-111111111111'


class TestTeamsHooks:
    def setup_method(self):
        self.hooks = CATEGORY_HOOKS['teams']

    def test_bot_app_id_accepts_uuid(self):
        assert self.hooks.validate('bot_app_id', _VALID_UUID) == _VALID_UUID

    def test_bot_app_id_rejects_non_uuid(self):
        with pytest.raises(HookValidationError, match='UUID'):
            self.hooks.validate('bot_app_id', 'not-a-uuid')

    def test_bot_app_id_accepts_empty_string(self):
        # Empty = cleared / not yet configured; runtime guard handles it.
        assert self.hooks.validate('bot_app_id', '') == ''

    def test_tenant_id_validation(self):
        assert self.hooks.validate('bot_app_tenant_id', _VALID_UUID) == _VALID_UUID
        with pytest.raises(HookValidationError):
            self.hooks.validate('bot_app_tenant_id', 'foo')

    def test_password_pre_store_encrypts(self):
        ciphertext = self.hooks.pre_store('bot_app_password', 'my-secret')
        assert ciphertext != 'my-secret'
        assert is_encrypted(ciphertext)

    def test_password_pre_store_passes_empty_through(self):
        assert self.hooks.pre_store('bot_app_password', '') == ''

    def test_password_pre_store_rejects_sentinel(self):
        # UI must not round-trip the redaction sentinel as a write.
        with pytest.raises(HookValidationError, match='sentinel'):
            self.hooks.pre_store('bot_app_password', '__SET__')

    def test_password_audit_redacted(self):
        assert self.hooks.audit_redact('bot_app_password') is True
        assert self.hooks.audit_redact('bot_app_id') is False

    def test_messaging_endpoint_accepts_https(self):
        url = 'https://acme.example.com/teams/api/messages'
        assert self.hooks.validate('messaging_endpoint', url) == url

    def test_messaging_endpoint_accepts_empty(self):
        assert self.hooks.validate('messaging_endpoint', '') == ''

    def test_messaging_endpoint_rejects_http(self):
        with pytest.raises(HookValidationError, match='HTTPS'):
            self.hooks.validate(
                'messaging_endpoint', 'http://example.com/teams/api/messages',
            )

    def test_messaging_endpoint_rejects_non_string(self):
        with pytest.raises(HookValidationError, match='string'):
            self.hooks.validate('messaging_endpoint', 12345)
