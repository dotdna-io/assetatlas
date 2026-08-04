# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Blueprint tests for the email settings endpoints.
Uses the `blueprint_app` fixture (mocked services — no live DB).
"""
from unittest.mock import MagicMock

import pytest

# Intentionally do NOT set ASSETATLAS_ENCRYPTION_KEY — the encryption module
# has a deterministic SHA-256 fallback derived from an internal salt, which
# is stable and sufficient for tests. Setting a fake key here would fail
# Fernet's base64 length validation.

from src.api.blueprints.settings.settings_routes import EMAIL_PASSWORD_SENTINEL
from src.auth.services.email_service import EMAIL_DISABLED, EMAIL_NOT_CONFIGURED
from src.modules.integrations.shared.encryption import is_encrypted, decrypt


@pytest.fixture
def mock_settings_manager(blueprint_app):
    """Replace the app's settings_manager with a MagicMock for per-test control."""
    sm = MagicMock()
    sm.set.return_value = (True, None)
    # Middleware calls settings_manager.get() for logging config; return default
    # to avoid a MagicMock flowing into getattr(logging, <mock>.upper(), ...).
    def _settings_get(_category, _key, default=None):
        return default
    sm.get = _settings_get
    blueprint_app.settings_manager = sm
    return sm


class TestUpdateSettingHooks:
    def test_put_smtp_password_encrypts_before_set(self, client, auth_headers, mock_settings_manager):
        resp = client.put(
            '/api/settings/email/smtp_password',
            headers=auth_headers(),
            json={'value': 'supersecret'},
        )
        assert resp.status_code == 200, resp.get_json()
        # settings_manager.set should have received the ENCRYPTED value.
        assert mock_settings_manager.set.called
        args, kwargs = mock_settings_manager.set.call_args
        # update_setting calls set() with KWARGS only; args is empty, value is in kwargs.
        stored_value = args[2] if len(args) > 2 else kwargs.get('value')
        assert is_encrypted(stored_value)
        assert decrypt(stored_value) == 'supersecret'

    def test_put_smtp_password_empty_stays_empty(self, client, auth_headers, mock_settings_manager):
        resp = client.put(
            '/api/settings/email/smtp_password',
            headers=auth_headers(),
            json={'value': ''},
        )
        assert resp.status_code == 200
        args, kwargs = mock_settings_manager.set.call_args
        stored = args[2] if len(args) > 2 else kwargs.get('value')
        assert stored == ''

    def test_put_smtp_password_sentinel_is_not_special_cased(self, client, auth_headers, mock_settings_manager):
        resp = client.put(
            '/api/settings/email/smtp_password',
            headers=auth_headers(),
            json={'value': EMAIL_PASSWORD_SENTINEL},
        )
        assert resp.status_code == 200
        args, kwargs = mock_settings_manager.set.call_args
        stored = args[2] if len(args) > 2 else kwargs.get('value')
        assert is_encrypted(stored)
        assert decrypt(stored) == EMAIL_PASSWORD_SENTINEL

    def test_put_smtp_port_out_of_range_is_400(self, client, auth_headers, mock_settings_manager):
        resp = client.put(
            '/api/settings/email/smtp_port',
            headers=auth_headers(),
            json={'value': 99999},
        )
        assert resp.status_code == 400
        mock_settings_manager.set.assert_not_called()

    def test_put_from_address_garbage_is_400(self, client, auth_headers, mock_settings_manager):
        resp = client.put(
            '/api/settings/email/from_address',
            headers=auth_headers(),
            json={'value': 'not-an-email'},
        )
        assert resp.status_code == 400
        mock_settings_manager.set.assert_not_called()

    def test_put_non_email_category_unchanged(self, client, auth_headers, mock_settings_manager):
        """Sanity check: settings outside the 'email' category bypass the hooks."""
        resp = client.put(
            '/api/settings/logging/enable_request_logging',
            headers=auth_headers(),
            json={'value': True},
        )
        assert resp.status_code == 200
        args, kwargs = mock_settings_manager.set.call_args
        stored = args[2] if len(args) > 2 else kwargs.get('value')
        assert stored is True

    def test_put_smtp_password_issues_audit_redact_update(self, client, auth_headers, mock_settings_manager, blueprint_app):
        """After set() returns, a targeted UPDATE redacts the latest audit row for the password key."""
        cursor_mock = MagicMock()
        conn_mock = MagicMock()
        blueprint_app.connection_manager.get_cursor.return_value.__enter__.return_value = (cursor_mock, conn_mock)
        blueprint_app.connection_manager.get_cursor.return_value.__exit__.return_value = False

        resp = client.put(
            '/api/settings/email/smtp_password',
            headers=auth_headers(),
            json={'value': 'supersecret'},
        )
        assert resp.status_code == 200
        executed_sqls = [c.args[0] for c in cursor_mock.execute.call_args_list if c.args]
        assert any('UPDATE settings_audit_log' in sql for sql in executed_sqls), executed_sqls


class TestGetEmailSettings:
    def test_get_redacts_stored_password(self, client, auth_headers, blueprint_app):
        from src.modules.integrations.shared.encryption import encrypt

        encrypted = encrypt('mystoredsecret')
        blueprint_app.settings_manager = MagicMock()

        def _settings_get(_category, _key, default=None):
            return default
        blueprint_app.settings_manager.get = _settings_get

        blueprint_app.settings_manager.get_all.return_value = {
            'email': {
                'enabled': {'value': True},
                'smtp_host': {'value': 'smtp.example.com'},
                'smtp_port': {'value': 587},
                'smtp_security': {'value': 'starttls'},
                'smtp_username': {'value': 'user'},
                'smtp_password': {'value': encrypted},
                'from_address': {'value': 'no-reply@example.com'},
                'from_display_name': {'value': 'AssetAtlas'},
                'reply_to_address': {'value': ''},
            }
        }

        resp = client.get('/api/settings/email', headers=auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        data = body.get('data', body)
        assert data['smtp_password'] == EMAIL_PASSWORD_SENTINEL
        assert 'ENC:' not in str(data)
        assert 'mystoredsecret' not in str(data)

    def test_get_empty_password_is_empty_string(self, client, auth_headers, blueprint_app):
        blueprint_app.settings_manager = MagicMock()

        def _settings_get(_category, _key, default=None):
            return default
        blueprint_app.settings_manager.get = _settings_get

        blueprint_app.settings_manager.get_all.return_value = {
            'email': {
                'enabled': {'value': False},
                'smtp_host': {'value': ''},
                'smtp_port': {'value': 587},
                'smtp_security': {'value': 'starttls'},
                'smtp_username': {'value': ''},
                'smtp_password': {'value': ''},
                'from_address': {'value': ''},
                'from_display_name': {'value': 'AssetAtlas'},
                'reply_to_address': {'value': ''},
            }
        }

        resp = client.get('/api/settings/email', headers=auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        data = body.get('data', body)
        assert data['smtp_password'] == ''

    def test_get_returns_all_email_keys(self, client, auth_headers, blueprint_app):
        blueprint_app.settings_manager = MagicMock()

        def _settings_get(_category, _key, default=None):
            return default
        blueprint_app.settings_manager.get = _settings_get

        blueprint_app.settings_manager.get_all.return_value = {
            'email': {k: {'value': v} for k, v in {
                'enabled': False, 'smtp_host': '', 'smtp_port': 587,
                'smtp_security': 'starttls', 'smtp_username': '',
                'smtp_password': '', 'from_address': '',
                'from_display_name': 'AssetAtlas', 'reply_to_address': '',
            }.items()}
        }
        resp = client.get('/api/settings/email', headers=auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        data = body.get('data', body)
        expected_keys = {
            'enabled', 'smtp_host', 'smtp_port', 'smtp_security',
            'smtp_username', 'smtp_password', 'from_address',
            'from_display_name', 'reply_to_address',
        }
        assert set(data.keys()) == expected_keys


class TestSendTestEmail:
    @pytest.fixture(autouse=True)
    def _email_service(self, blueprint_app):
        svc = MagicMock()
        blueprint_app.email_service = svc
        return svc

    def test_success_returns_200(self, client, auth_headers, _email_service):
        _email_service.send_test_email.return_value = (True, None)
        resp = client.post(
            '/api/settings/email/test',
            headers=auth_headers(),
            json={'recipient': 'admin@example.com'},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        data = body.get('data', body)
        assert data.get('success') is True
        _email_service.send_test_email.assert_called_once_with('admin@example.com')

    def test_smtp_error_returns_400(self, client, auth_headers, _email_service):
        _email_service.send_test_email.return_value = (False, '550 relay rejected')
        resp = client.post(
            '/api/settings/email/test',
            headers=auth_headers(),
            json={'recipient': 'admin@example.com'},
        )
        assert resp.status_code == 400
        body = resp.get_json()
        data = body.get('data', body)
        assert data.get('success') is False
        assert '550 relay rejected' in data.get('error', '')

    def test_does_not_special_case_email_disabled(self, client, auth_headers, _email_service):
        """`email_disabled` is no longer part of this endpoint's contract.

        send_test_email() dropped its cfg.enabled check, so the sentinel cannot
        reach the route from the real service. This asserts the route stopped
        translating it to a 409 — if someone re-adds that mapping, the endpoint
        starts advertising a state it can no longer be in.
        """
        _email_service.send_test_email.return_value = (False, EMAIL_DISABLED)
        resp = client.post(
            '/api/settings/email/test',
            headers=auth_headers(),
            json={'recipient': 'admin@example.com'},
        )
        assert resp.status_code == 400

    def test_not_configured_returns_409(self, client, auth_headers, _email_service):
        _email_service.send_test_email.return_value = (False, EMAIL_NOT_CONFIGURED)
        resp = client.post(
            '/api/settings/email/test',
            headers=auth_headers(),
            json={'recipient': 'admin@example.com'},
        )
        assert resp.status_code == 409
        body = resp.get_json()
        data = body.get('data', body)
        assert data.get('error') == EMAIL_NOT_CONFIGURED

    def test_missing_recipient_is_400(self, client, auth_headers, _email_service):
        resp = client.post(
            '/api/settings/email/test',
            headers=auth_headers(),
            json={},
        )
        assert resp.status_code == 400
        _email_service.send_test_email.assert_not_called()
