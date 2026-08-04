# SPDX-License-Identifier: AGPL-3.0-or-later
"""One-time import of legacy TEAMS_BOT_APP_ID/PASSWORD/TENANT_ID from env."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.api.utils.teams_env_import import import_legacy_teams_env_once
from src.modules.integrations.shared.encryption import is_encrypted


_APPID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
_TID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for key in (
        'TEAMS_BOT_APP_ID', 'TEAMS_BOT_APP_PASSWORD', 'TEAMS_BOT_APP_TENANT_ID',
    ):
        monkeypatch.delenv(key, raising=False)
    yield


def _sm_with_existing(app_id_in_db: str):
    sm = MagicMock()
    sm.get.side_effect = lambda c, k: (
        app_id_in_db if (c, k) == ('teams', 'bot_app_id') else ''
    )
    sm.set.return_value = (True, None)
    # Provide a no-op cursor context manager so the audit-redaction pass
    # doesn't crash when invoked.
    cur = MagicMock()
    conn = MagicMock()
    sm.connection_manager.get_cursor.return_value.__enter__.return_value = (
        cur, conn,
    )
    sm.connection_manager.get_cursor.return_value.__exit__.return_value = False
    return sm


class TestEnvImport:
    def test_no_env_no_action(self):
        sm = _sm_with_existing('')
        result = import_legacy_teams_env_once(sm)
        assert result is False
        sm.set.assert_not_called()

    def test_partial_env_no_action(self, monkeypatch):
        # Only app_id set; password + tenant missing → no write.
        # We deliberately avoid importing a half-configured state.
        monkeypatch.setenv('TEAMS_BOT_APP_ID', _APPID)
        sm = _sm_with_existing('')
        result = import_legacy_teams_env_once(sm)
        assert result is False
        sm.set.assert_not_called()

    def test_db_already_populated_no_action(self, monkeypatch):
        monkeypatch.setenv('TEAMS_BOT_APP_ID', _APPID)
        monkeypatch.setenv('TEAMS_BOT_APP_PASSWORD', 'secret')
        monkeypatch.setenv('TEAMS_BOT_APP_TENANT_ID', _TID)
        sm = _sm_with_existing(_APPID)
        result = import_legacy_teams_env_once(sm)
        assert result is False
        sm.set.assert_not_called()

    def test_full_env_db_empty_imports_with_encrypted_secret(self, monkeypatch):
        monkeypatch.setenv('TEAMS_BOT_APP_ID', _APPID)
        monkeypatch.setenv('TEAMS_BOT_APP_PASSWORD', 'plaintext-secret')
        monkeypatch.setenv('TEAMS_BOT_APP_TENANT_ID', _TID)
        sm = _sm_with_existing('')

        result = import_legacy_teams_env_once(sm)

        assert result is True
        # Exactly 4 set() calls — id, password (encrypted), tenant, enabled.
        assert sm.set.call_count == 4

        # Verify the password value is encrypted, not plaintext.
        password_call = next(
            c for c in sm.set.call_args_list
            if (c.args[1] if len(c.args) > 1 else c.kwargs['key']) == 'bot_app_password'
        )
        password_value = (
            password_call.args[2] if len(password_call.args) > 2
            else password_call.kwargs['value']
        )
        assert password_value != 'plaintext-secret'
        assert is_encrypted(password_value)

        # Audit-log redaction was attempted (cursor was used).
        assert sm.connection_manager.get_cursor.called

    def test_audit_redaction_failure_does_not_break_import(self, monkeypatch):
        # If the audit-log UPDATE blows up, the import still returns True —
        # the settings rows are written; only the audit cleanup failed.
        monkeypatch.setenv('TEAMS_BOT_APP_ID', _APPID)
        monkeypatch.setenv('TEAMS_BOT_APP_PASSWORD', 'plaintext-secret')
        monkeypatch.setenv('TEAMS_BOT_APP_TENANT_ID', _TID)
        sm = _sm_with_existing('')
        sm.connection_manager.get_cursor.side_effect = RuntimeError('boom')

        result = import_legacy_teams_env_once(sm)

        assert result is True
        assert sm.set.call_count == 4
