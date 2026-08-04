# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for EmailService.
Uses the encryption module's deterministic SHA-256 fallback key (no env key set).
SMTP is always mocked — these tests must never open a real socket.
"""
import smtplib
from email.message import EmailMessage
from unittest.mock import MagicMock

import pytest

# Intentionally do NOT set ASSETATLAS_ENCRYPTION_KEY — the encryption module
# has a deterministic SHA-256 fallback derived from an internal salt, which
# is stable and sufficient for tests. Setting a fake key here would fail
# Fernet's base64 length validation.

from src.auth.services.email_service import EMAIL_DISABLED, EMAIL_NOT_CONFIGURED
from src.modules.integrations.shared.encryption import encrypt


def _settings_manager_with(values: dict) -> MagicMock:
    """Build a MagicMock SettingsManager whose .get(category, key) returns the given dict value."""
    sm = MagicMock()
    sm.get.side_effect = lambda category, key, default=None: values.get(key, default)
    return sm


def _mock_smtp(monkeypatch, attr: str = 'SMTP'):
    """Patch `smtplib.{attr}` with a MagicMock, return (instance, cls).

    `instance` is what `with client_cls(...) as client` yields; `cls` is the
    constructor — use `cls.assert_called_once_with(host, port, timeout=...)`
    to verify connection args.
    """
    instance = MagicMock()
    instance.__enter__ = MagicMock(return_value=instance)
    instance.__exit__ = MagicMock(return_value=False)
    cls = MagicMock(return_value=instance)
    monkeypatch.setattr(f'src.auth.services.email_service.smtplib.{attr}', cls)
    return instance, cls


DEFAULTS = {
    'enabled': True,
    'smtp_host': 'smtp.example.com',
    'smtp_port': 587,
    'smtp_security': 'starttls',
    'smtp_username': 'user@example.com',
    'smtp_password': encrypt('secret'),
    'from_address': 'no-reply@example.com',
    'from_display_name': 'AssetAtlas',
    'reply_to_address': '',
    'public_base_url': 'https://app.example.com',
}


class TestLoadConfig:
    def test_load_decrypts_password(self):
        from src.auth.services.email_service import EmailService
        sm = _settings_manager_with(DEFAULTS)
        svc = EmailService(sm)
        cfg = svc._load_config()
        assert cfg.smtp_password == 'secret'

    def test_load_carries_all_fields(self):
        from src.auth.services.email_service import EmailService
        sm = _settings_manager_with(DEFAULTS)
        svc = EmailService(sm)
        cfg = svc._load_config()
        assert cfg.enabled is True
        assert cfg.smtp_host == 'smtp.example.com'
        assert cfg.smtp_port == 587
        assert cfg.smtp_security == 'starttls'
        assert cfg.smtp_username == 'user@example.com'
        assert cfg.from_address == 'no-reply@example.com'
        assert cfg.from_display_name == 'AssetAtlas'
        assert cfg.reply_to_address == ''
        assert cfg.public_base_url == 'https://app.example.com'

    def test_load_handles_empty_password(self):
        from src.auth.services.email_service import EmailService
        sm = _settings_manager_with({**DEFAULTS, 'smtp_password': ''})
        svc = EmailService(sm)
        cfg = svc._load_config()
        assert cfg.smtp_password == ''


def _build_msg():
    m = EmailMessage()
    m['Subject'] = 'Test'
    m['From'] = 'no-reply@example.com'
    m['To'] = 'dest@example.com'
    m.set_content('body')
    return m


def _cfg(**overrides):
    base = dict(
        enabled=True, smtp_host='smtp.example.com', smtp_port=587,
        smtp_security='starttls', smtp_username='u', smtp_password='p',
        from_address='f@example.com', from_display_name='X',
        reply_to_address='', public_base_url='',
    )
    base.update(overrides)
    from src.auth.services.email_service import EmailConfig
    return EmailConfig(**base)


class TestSend:
    def test_starttls_flow(self, monkeypatch):
        from src.auth.services.email_service import EmailService
        instance, cls = _mock_smtp(monkeypatch)

        svc = EmailService(_settings_manager_with(DEFAULTS))
        ok, err = svc._send(_build_msg(), _cfg())
        assert ok is True
        assert err is None
        cls.assert_called_once_with('smtp.example.com', 587, timeout=10)
        instance.starttls.assert_called_once()
        instance.login.assert_called_once_with('u', 'p')
        instance.send_message.assert_called_once()

    def test_tls_flow_uses_smtp_ssl(self, monkeypatch):
        from src.auth.services.email_service import EmailService
        instance, cls = _mock_smtp(monkeypatch, attr='SMTP_SSL')

        svc = EmailService(_settings_manager_with(DEFAULTS))
        ok, err = svc._send(_build_msg(), _cfg(smtp_port=465, smtp_security='tls'))
        assert ok is True
        cls.assert_called_once_with('smtp.example.com', 465, timeout=10)
        instance.starttls.assert_not_called()
        instance.login.assert_called_once_with('u', 'p')

    def test_none_security_no_starttls(self, monkeypatch):
        from src.auth.services.email_service import EmailService
        instance, _cls = _mock_smtp(monkeypatch)

        svc = EmailService(_settings_manager_with(DEFAULTS))
        ok, _err = svc._send(_build_msg(), _cfg(smtp_port=25, smtp_security='none'))
        assert ok is True
        instance.starttls.assert_not_called()
        instance.login.assert_called_once_with('u', 'p')

    def test_blank_username_no_login(self, monkeypatch):
        from src.auth.services.email_service import EmailService
        instance, _cls = _mock_smtp(monkeypatch)

        svc = EmailService(_settings_manager_with(DEFAULTS))
        ok, _err = svc._send(
            _build_msg(),
            _cfg(smtp_port=25, smtp_security='none', smtp_username='', smtp_password=''),
        )
        assert ok is True
        instance.login.assert_not_called()

    def test_smtp_exception_returns_false_and_error(self, monkeypatch):
        from src.auth.services.email_service import EmailService
        instance, _cls = _mock_smtp(monkeypatch)
        instance.send_message.side_effect = smtplib.SMTPException("relay rejected")

        svc = EmailService(_settings_manager_with(DEFAULTS))
        ok, err = svc._send(_build_msg(), _cfg())
        assert ok is False
        assert 'relay rejected' in err


class TestSendPasswordReset:
    def test_builds_message_with_reset_link(self, monkeypatch):
        from src.auth.services.email_service import EmailService
        instance, _cls = _mock_smtp(monkeypatch)

        svc = EmailService(_settings_manager_with(DEFAULTS))
        ok = svc.send_password_reset_email(
            to_email='dest@example.com',
            username='bob',
            reset_token='tok-123',
            expires_in_hours=1,
        )
        assert ok is True
        sent_msg = instance.send_message.call_args.args[0]
        assert sent_msg['To'] == 'dest@example.com'
        assert 'AssetAtlas' in sent_msg['From']
        assert 'no-reply@example.com' in sent_msg['From']
        assert 'password-reset/verify?token=tok-123' in sent_msg.get_content()
        assert 'https://app.example.com' in sent_msg.get_content()

    def test_returns_false_when_disabled(self):
        from src.auth.services.email_service import EmailService
        sm = _settings_manager_with({**DEFAULTS, 'enabled': False})
        svc = EmailService(sm)
        ok = svc.send_password_reset_email(
            to_email='dest@example.com', username='bob', reset_token='t', expires_in_hours=1,
        )
        assert ok is False

    def test_returns_false_when_not_configured(self):
        from src.auth.services.email_service import EmailService
        sm = _settings_manager_with({**DEFAULTS, 'smtp_host': '', 'from_address': ''})
        svc = EmailService(sm)
        ok = svc.send_password_reset_email(
            to_email='dest@example.com', username='bob', reset_token='t', expires_in_hours=1,
        )
        assert ok is False

    def test_returns_false_on_smtp_failure(self, monkeypatch):
        from src.auth.services.email_service import EmailService
        instance, _cls = _mock_smtp(monkeypatch)
        instance.send_message.side_effect = smtplib.SMTPException("boom")

        svc = EmailService(_settings_manager_with(DEFAULTS))
        ok = svc.send_password_reset_email(
            to_email='dest@example.com', username='bob', reset_token='t', expires_in_hours=1,
        )
        assert ok is False


class TestSendPasswordChanged:
    def test_sends_notification(self, monkeypatch):
        from src.auth.services.email_service import EmailService
        instance, _cls = _mock_smtp(monkeypatch)

        svc = EmailService(_settings_manager_with(DEFAULTS))
        ok = svc.send_password_changed_notification(
            to_email='dest@example.com', username='bob',
        )
        assert ok is True
        sent_msg = instance.send_message.call_args.args[0]
        assert sent_msg['To'] == 'dest@example.com'
        assert 'password' in sent_msg['Subject'].lower()
        assert 'bob' in sent_msg.get_content()

    def test_disabled_returns_false(self):
        from src.auth.services.email_service import EmailService
        svc = EmailService(_settings_manager_with({**DEFAULTS, 'enabled': False}))
        assert svc.send_password_changed_notification('dest@example.com', 'bob') is False


class TestSendWelcome:
    def test_sends_welcome(self, monkeypatch):
        from src.auth.services.email_service import EmailService
        instance, _cls = _mock_smtp(monkeypatch)

        svc = EmailService(_settings_manager_with(DEFAULTS))
        ok = svc.send_welcome_email(to_email='new@example.com', username='newbob')
        assert ok is True
        sent_msg = instance.send_message.call_args.args[0]
        assert sent_msg['To'] == 'new@example.com'
        assert 'welcome' in sent_msg['Subject'].lower()
        assert 'newbob' in sent_msg.get_content()
        assert 'https://app.example.com' in sent_msg.get_content()

    def test_disabled_returns_false(self):
        from src.auth.services.email_service import EmailService
        svc = EmailService(_settings_manager_with({**DEFAULTS, 'enabled': False}))
        assert svc.send_welcome_email('new@example.com', 'newbob') is False


class TestSendTest:
    def test_success_returns_true_none(self, monkeypatch):
        from src.auth.services.email_service import EmailService
        _mock_smtp(monkeypatch)

        svc = EmailService(_settings_manager_with(DEFAULTS))
        ok, err = svc.send_test_email('admin@example.com')
        assert ok is True
        assert err is None

    def test_failure_returns_false_with_error(self, monkeypatch):
        from src.auth.services.email_service import EmailService
        instance, _cls = _mock_smtp(monkeypatch)
        instance.send_message.side_effect = smtplib.SMTPException('rejected')

        svc = EmailService(_settings_manager_with(DEFAULTS))
        ok, err = svc.send_test_email('admin@example.com')
        assert ok is False
        assert 'rejected' in err

    def test_disabled_still_sends_the_test_message(self, monkeypatch):
        """`Enable outbound email` off must not block the admin's own test send."""
        from src.auth.services.email_service import EmailService
        instance, _cls = _mock_smtp(monkeypatch)
        svc = EmailService(_settings_manager_with({**DEFAULTS, 'enabled': False}))
        ok, err = svc.send_test_email('admin@example.com')
        assert ok is True
        assert err is None
        instance.send_message.assert_called_once()

    def test_not_configured_returns_email_not_configured(self):
        from src.auth.services.email_service import EmailService
        svc = EmailService(_settings_manager_with({**DEFAULTS, 'smtp_host': ''}))
        ok, err = svc.send_test_email('admin@example.com')
        assert ok is False
        assert err == EMAIL_NOT_CONFIGURED
