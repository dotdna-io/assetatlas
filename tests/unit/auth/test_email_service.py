# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for EmailService — mocks smtplib.SMTP / SMTP_SSL.

Covers:
- send_password_reset_email: subject, To header, body contains reset link with token
- send_invite_email: body contains invite link with token, day-based expiry text
- send_password_changed_notification: basic delivery
- send_welcome_email: basic delivery, login URL conditional
- send_test_email: returns sentinel error strings when disabled / not configured
- _send: returns (False, error) on SMTP exception, (True, None) on success;
  exercises STARTTLS, SMTP_SSL, and plain SMTP branches; honors smtp_username
- Templating safety: user input containing HTML/script characters is delivered
  verbatim as plain text (no HTML rendering, no escaping leak)
- _load_config: decrypts ENC:-prefixed smtp_password
"""
import smtplib
from unittest.mock import MagicMock, patch

from src.auth.services.email_service import (
    EMAIL_DISABLED,
    EMAIL_NOT_CONFIGURED,
    EmailConfig,
    EmailService,
)


# ── helpers ────────────────────────────────────────────────────────────────

def _settings(**overrides):
    base = {
        'enabled': True,
        'smtp_host': 'smtp.example.com',
        'smtp_port': 587,
        'smtp_security': 'starttls',
        'smtp_username': 'mailer',
        'smtp_password': 'plaintext-password',
        'from_address': 'noreply@example.com',
        'from_display_name': 'AssetAtlas',
        'reply_to_address': 'support@example.com',
        'public_base_url': 'https://app.example.com',
    }
    base.update(overrides)
    return base


def _settings_manager(**overrides):
    cfg = _settings(**overrides)
    sm = MagicMock()
    sm.get.side_effect = lambda category, key, default=None: cfg.get(key, default)
    return sm


def _make_smtp_ctx_mock():
    """Returns (client_cls_mock, instance_mock). client_cls_mock(...) returns
    a context manager that yields instance_mock from __enter__."""
    instance = MagicMock()
    # smtplib.SMTP.send_message returns {} when every recipient is accepted (a
    # non-empty dict signals a partial refusal). Default the mock to success so
    # send_alert_email's partial-refusal check sees "all accepted".
    instance.send_message.return_value = {}
    cls_mock = MagicMock()
    cls_mock.return_value.__enter__.return_value = instance
    cls_mock.return_value.__exit__.return_value = False
    return cls_mock, instance


# ── send_password_reset_email ─────────────────────────────────────────────

class TestSendPasswordResetEmail:

    def test_returns_false_when_email_disabled(self):
        svc = EmailService(_settings_manager(enabled=False))
        with patch('src.auth.services.email_service.smtplib.SMTP') as smtp:
            assert svc.send_password_reset_email('alice@x.com', 'alice', 'TKN') is False
            smtp.assert_not_called()

    def test_returns_false_when_smtp_host_missing(self):
        svc = EmailService(_settings_manager(smtp_host=''))
        with patch('src.auth.services.email_service.smtplib.SMTP') as smtp:
            assert svc.send_password_reset_email('alice@x.com', 'alice', 'TKN') is False
            smtp.assert_not_called()

    def test_returns_false_when_from_address_missing(self):
        svc = EmailService(_settings_manager(from_address=''))
        with patch('src.auth.services.email_service.smtplib.SMTP') as smtp:
            assert svc.send_password_reset_email('alice@x.com', 'alice', 'TKN') is False
            smtp.assert_not_called()

    def test_sends_with_reset_link_and_correct_headers(self):
        svc = EmailService(_settings_manager())
        cls_mock, instance = _make_smtp_ctx_mock()

        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            ok = svc.send_password_reset_email(
                'alice@x.com', 'alice', 'reset-token-abc', expires_in_hours=2,
            )

        assert ok is True
        # SMTP constructor called with host/port/timeout
        cls_mock.assert_called_once()
        args, kwargs = cls_mock.call_args
        assert args[0] == 'smtp.example.com'
        assert args[1] == 587
        assert kwargs.get('timeout') == 10

        # STARTTLS branch: starttls() then login() then send_message()
        instance.starttls.assert_called_once()
        instance.login.assert_called_once_with('mailer', 'plaintext-password')
        instance.send_message.assert_called_once()

        msg = instance.send_message.call_args[0][0]
        assert msg['To'] == 'alice@x.com'
        assert msg['From'] == 'AssetAtlas <noreply@example.com>'
        assert msg['Reply-To'] == 'support@example.com'
        assert 'reset' in msg['Subject'].lower()

        body = msg.get_content()
        assert 'reset-token-abc' in body
        assert 'https://app.example.com/password-reset/verify?token=reset-token-abc' in body
        assert '2 hour(s)' in body
        assert 'alice' in body

    def test_strips_trailing_slash_from_public_base_url(self):
        svc = EmailService(_settings_manager(public_base_url='https://app.example.com/'))
        cls_mock, instance = _make_smtp_ctx_mock()

        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            svc.send_password_reset_email('a@x.com', 'alice', 'TKN')

        body = instance.send_message.call_args[0][0].get_content()
        # No double-slash before /password-reset
        assert 'https://app.example.com/password-reset/verify?token=TKN' in body
        assert '//password-reset' not in body

    def test_returns_false_on_smtp_failure(self):
        svc = EmailService(_settings_manager())
        cls_mock, instance = _make_smtp_ctx_mock()
        instance.send_message.side_effect = OSError('connection refused')

        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            ok = svc.send_password_reset_email('alice@x.com', 'alice', 'TKN')

        assert ok is False


# ── send_invite_email ──────────────────────────────────────────────────────

class TestSendInviteEmail:

    def test_sends_with_onboarding_link_and_token(self):
        svc = EmailService(_settings_manager())
        cls_mock, instance = _make_smtp_ctx_mock()

        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            ok = svc.send_invite_email('bob@x.com', 'bob', 'invite-token-xyz')

        assert ok is True
        msg = instance.send_message.call_args[0][0]
        assert msg['To'] == 'bob@x.com'
        assert 'AssetAtlas' in msg['Subject']
        body = msg.get_content()
        assert 'invite-token-xyz' in body
        assert 'https://app.example.com/onboarding?token=invite-token-xyz' in body
        # Default 168h → 7 days
        assert '7 day(s)' in body

    def test_custom_expiry_converts_hours_to_days(self):
        svc = EmailService(_settings_manager())
        cls_mock, instance = _make_smtp_ctx_mock()

        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            svc.send_invite_email('bob@x.com', 'bob', 'TKN', expires_in_hours=72)

        body = instance.send_message.call_args[0][0].get_content()
        assert '3 day(s)' in body

    def test_short_expiry_floors_to_at_least_one_day(self):
        """6 hours // 24 = 0 — service clamps to 1 day in the email body."""
        svc = EmailService(_settings_manager())
        cls_mock, instance = _make_smtp_ctx_mock()

        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            svc.send_invite_email('bob@x.com', 'bob', 'TKN', expires_in_hours=6)

        body = instance.send_message.call_args[0][0].get_content()
        assert '1 day(s)' in body

    def test_returns_false_when_disabled(self):
        svc = EmailService(_settings_manager(enabled=False))
        with patch('src.auth.services.email_service.smtplib.SMTP') as smtp:
            assert svc.send_invite_email('bob@x.com', 'bob', 'TKN') is False
            smtp.assert_not_called()

    def test_returns_false_when_not_configured(self):
        svc = EmailService(_settings_manager(smtp_host=''))
        with patch('src.auth.services.email_service.smtplib.SMTP') as smtp:
            assert svc.send_invite_email('bob@x.com', 'bob', 'TKN') is False
            smtp.assert_not_called()


# ── send_password_changed_notification ─────────────────────────────────────

class TestSendPasswordChangedNotification:

    def test_basic_delivery(self):
        svc = EmailService(_settings_manager())
        cls_mock, instance = _make_smtp_ctx_mock()

        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            ok = svc.send_password_changed_notification('carol@x.com', 'carol')

        assert ok is True
        msg = instance.send_message.call_args[0][0]
        assert msg['To'] == 'carol@x.com'
        assert 'password' in msg['Subject'].lower()
        body = msg.get_content()
        assert 'carol' in body
        assert 'changed' in body.lower()

    def test_returns_false_when_disabled(self):
        svc = EmailService(_settings_manager(enabled=False))
        with patch('src.auth.services.email_service.smtplib.SMTP') as smtp:
            assert svc.send_password_changed_notification('c@x.com', 'c') is False
            smtp.assert_not_called()

    def test_returns_false_when_not_configured(self):
        svc = EmailService(_settings_manager(from_address=''))
        with patch('src.auth.services.email_service.smtplib.SMTP') as smtp:
            assert svc.send_password_changed_notification('c@x.com', 'c') is False
            smtp.assert_not_called()


# ── send_welcome_email ─────────────────────────────────────────────────────

class TestSendWelcomeEmail:

    def test_includes_login_url_when_public_base_url_set(self):
        svc = EmailService(_settings_manager())
        cls_mock, instance = _make_smtp_ctx_mock()

        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            ok = svc.send_welcome_email('dan@x.com', 'dan')

        assert ok is True
        body = instance.send_message.call_args[0][0].get_content()
        assert 'https://app.example.com/login' in body
        assert 'dan' in body

    def test_omits_login_url_when_public_base_url_blank(self):
        svc = EmailService(_settings_manager(public_base_url=''))
        cls_mock, instance = _make_smtp_ctx_mock()

        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            svc.send_welcome_email('dan@x.com', 'dan')

        body = instance.send_message.call_args[0][0].get_content()
        assert 'Login:' not in body

    def test_returns_false_when_disabled(self):
        svc = EmailService(_settings_manager(enabled=False))
        with patch('src.auth.services.email_service.smtplib.SMTP') as smtp:
            assert svc.send_welcome_email('d@x.com', 'd') is False
            smtp.assert_not_called()

    def test_returns_false_when_not_configured(self):
        svc = EmailService(_settings_manager(smtp_host=''))
        with patch('src.auth.services.email_service.smtplib.SMTP') as smtp:
            assert svc.send_welcome_email('d@x.com', 'd') is False
            smtp.assert_not_called()


# ── send_test_email ────────────────────────────────────────────────────────

class TestSendTestEmail:

    def test_sends_even_when_outbound_email_is_disabled(self):
        """The master toggle must NOT gate the explicit admin test send.

        Otherwise you have to arm live outbound mail before you are allowed to
        check the relay works. The toggle still suppresses every other sender —
        see TestSendAlertEmail's disabled case, which is the guard for that.
        """
        cls_mock, instance = _make_smtp_ctx_mock()
        svc = EmailService(_settings_manager(enabled=False))

        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            ok, err = svc.send_test_email('admin@x.com')

        assert ok is True
        assert err is None
        instance.send_message.assert_called_once()

    def test_returns_not_configured_sentinel_when_host_missing(self):
        svc = EmailService(_settings_manager(smtp_host=''))
        ok, err = svc.send_test_email('admin@x.com')
        assert ok is False
        assert err == EMAIL_NOT_CONFIGURED

    def test_success_returns_true_and_none_error(self):
        svc = EmailService(_settings_manager())
        cls_mock, instance = _make_smtp_ctx_mock()

        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            ok, err = svc.send_test_email('admin@x.com')

        assert ok is True
        assert err is None
        body = instance.send_message.call_args[0][0].get_content()
        assert 'test email' in body.lower()

    def test_propagates_smtp_error_string(self):
        svc = EmailService(_settings_manager())
        cls_mock, instance = _make_smtp_ctx_mock()
        instance.send_message.side_effect = RuntimeError('relay denied')

        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            ok, err = svc.send_test_email('admin@x.com')

        assert ok is False
        assert 'relay denied' in err


# ── _send transport branches ───────────────────────────────────────────────

class TestSendTransport:

    def test_smtp_ssl_used_when_security_is_tls(self):
        svc = EmailService(_settings_manager(smtp_security='tls', smtp_port=465))
        cls_mock, instance = _make_smtp_ctx_mock()

        with patch('src.auth.services.email_service.smtplib.SMTP_SSL', cls_mock):
            ok = svc.send_password_reset_email('a@x.com', 'a', 'TKN')

        assert ok is True
        cls_mock.assert_called_once()
        # No starttls() on SMTP_SSL connections
        instance.starttls.assert_not_called()
        instance.login.assert_called_once()
        instance.send_message.assert_called_once()

    def test_plain_smtp_no_starttls_no_login(self):
        svc = EmailService(_settings_manager(
            smtp_security='none', smtp_username='', smtp_password='',
        ))
        cls_mock, instance = _make_smtp_ctx_mock()

        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            ok = svc.send_password_reset_email('a@x.com', 'a', 'TKN')

        assert ok is True
        instance.starttls.assert_not_called()
        instance.login.assert_not_called()
        instance.send_message.assert_called_once()

    def test_returns_false_on_constructor_exception(self):
        svc = EmailService(_settings_manager())

        # Constructor itself raises (e.g. DNS failure / connection refused).
        with patch('src.auth.services.email_service.smtplib.SMTP',
                   side_effect=OSError('connection refused')):
            ok = svc.send_password_reset_email('a@x.com', 'a', 'TKN')

        assert ok is False

    def test_returns_false_on_starttls_exception(self):
        svc = EmailService(_settings_manager())
        cls_mock, instance = _make_smtp_ctx_mock()

        import smtplib as _smtplib
        instance.starttls.side_effect = _smtplib.SMTPException('STARTTLS failed')

        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            ok = svc.send_password_reset_email('a@x.com', 'a', 'TKN')

        assert ok is False
        instance.send_message.assert_not_called()

    def test_returns_false_on_login_exception(self):
        svc = EmailService(_settings_manager())
        cls_mock, instance = _make_smtp_ctx_mock()

        import smtplib as _smtplib
        instance.login.side_effect = _smtplib.SMTPAuthenticationError(535, b'auth failed')

        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            ok, err = svc.send_test_email('a@x.com')

        assert ok is False
        assert err  # non-empty error string
        instance.send_message.assert_not_called()

    def test_send_invokes_direct_with_synthetic_message(self):
        """Cover _send directly with an EmailMessage, both success and failure."""
        from email.message import EmailMessage
        svc = EmailService(_settings_manager())
        cfg = EmailConfig(
            enabled=True, smtp_host='h', smtp_port=25, smtp_security='none',
            smtp_username='', smtp_password='',
            from_address='f@x.com', from_display_name='', reply_to_address='',
            public_base_url='',
        )
        msg = EmailMessage()
        msg['Subject'] = 'x'
        msg['From'] = 'f@x.com'
        msg['To'] = 't@x.com'
        msg.set_content('body')

        cls_mock, _ = _make_smtp_ctx_mock()
        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            ok, err = svc._send(msg, cfg)
        assert ok is True
        assert err is None

        with patch('src.auth.services.email_service.smtplib.SMTP',
                   side_effect=ValueError('boom')):
            ok, err = svc._send(msg, cfg)
        assert ok is False
        assert 'boom' in err


# ── from-header assembly ───────────────────────────────────────────────────

class TestFromHeader:

    def test_falls_back_to_address_when_display_name_blank(self):
        svc = EmailService(_settings_manager(from_display_name=''))
        cls_mock, instance = _make_smtp_ctx_mock()

        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            svc.send_password_reset_email('a@x.com', 'a', 'TKN')

        msg = instance.send_message.call_args[0][0]
        assert msg['From'] == 'noreply@example.com'

    def test_omits_reply_to_when_blank(self):
        svc = EmailService(_settings_manager(reply_to_address=''))
        cls_mock, instance = _make_smtp_ctx_mock()

        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            svc.send_password_reset_email('a@x.com', 'a', 'TKN')

        msg = instance.send_message.call_args[0][0]
        assert msg['Reply-To'] is None


# ── templating safety ──────────────────────────────────────────────────────

class TestTemplatingSafety:
    """Service builds plain-text bodies via EmailMessage.set_content().

    User input is included verbatim — no HTML rendering, so no XSS surface.
    These tests pin that contract: a script tag in the username appears as
    literal text in the body and is announced as text/plain by the message.
    """

    def test_html_in_username_is_plain_text_not_html_message(self):
        svc = EmailService(_settings_manager())
        cls_mock, instance = _make_smtp_ctx_mock()

        username = '<script>alert(1)</script>'
        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            svc.send_password_reset_email('a@x.com', username, 'TKN')

        msg = instance.send_message.call_args[0][0]
        # Message is plain text, not HTML.
        assert msg.get_content_type() == 'text/plain'
        # No HTML alternative attached.
        assert msg.get_payload(decode=False) is not None
        body = msg.get_content()
        # The literal characters survive to the body — they are not rendered
        # as HTML because the content type is text/plain.
        assert username in body

    def test_html_in_token_is_url_embedded_verbatim(self):
        """A token containing reserved characters is embedded as-is into the
        link. The point of the test is that the body remains plain text and
        the dangerous chars are not stripped silently — the upstream caller
        is responsible for token shape (the service itself does not URL-encode)."""
        svc = EmailService(_settings_manager())
        cls_mock, instance = _make_smtp_ctx_mock()

        token = '<script>x</script>'
        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            svc.send_invite_email('a@x.com', 'alice', token)

        msg = instance.send_message.call_args[0][0]
        assert msg.get_content_type() == 'text/plain'
        body = msg.get_content()
        assert token in body

    def test_no_html_alternative_attached(self):
        svc = EmailService(_settings_manager())
        cls_mock, instance = _make_smtp_ctx_mock()

        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            svc.send_password_changed_notification('a@x.com', 'alice')

        msg = instance.send_message.call_args[0][0]
        # walk() over a non-multipart message yields just the message itself.
        parts = list(msg.walk())
        content_types = [p.get_content_type() for p in parts]
        assert 'text/html' not in content_types


# ── _load_config (decryption) ──────────────────────────────────────────────

class TestLoadConfig:

    def test_decrypts_encrypted_smtp_password(self):
        svc = EmailService(_settings_manager(smtp_password='ENC:fake-blob'))

        with patch('src.auth.services.email_service.is_encrypted',
                   return_value=True), \
             patch('src.auth.services.email_service.decrypt',
                   return_value='decrypted-secret') as mock_decrypt:
            cfg = svc._load_config()

        mock_decrypt.assert_called_once_with('ENC:fake-blob')
        assert cfg.smtp_password == 'decrypted-secret'

    def test_decrypt_failure_yields_empty_password(self):
        # Stored ciphertext can't be decrypted with the current key (e.g. key
        # rotation, env mismatch). Must not raise — empty password lets the
        # SMTP send fail cleanly so the admin password-reset endpoint returns
        # email_sent=False instead of 500.
        svc = EmailService(_settings_manager(smtp_password='ENC:stale-blob'))

        with patch('src.auth.services.email_service.is_encrypted',
                   return_value=True), \
             patch('src.auth.services.email_service.decrypt',
                   side_effect=ValueError('Failed to decrypt value: ...')):
            cfg = svc._load_config()

        assert cfg.smtp_password == ''

    def test_plaintext_password_passes_through(self):
        svc = EmailService(_settings_manager(smtp_password='plaintext'))
        with patch('src.auth.services.email_service.decrypt') as mock_decrypt:
            cfg = svc._load_config()
        # Not encrypted → no call to decrypt
        mock_decrypt.assert_not_called()
        assert cfg.smtp_password == 'plaintext'

    def test_non_string_password_becomes_empty(self):
        svc = EmailService(_settings_manager(smtp_password=None))
        cfg = svc._load_config()
        assert cfg.smtp_password == ''

    def test_invalid_smtp_port_falls_back_to_587(self):
        svc = EmailService(_settings_manager(smtp_port=0))
        cfg = svc._load_config()
        assert cfg.smtp_port == 587

    def test_loads_all_fields_into_dataclass(self):
        svc = EmailService(_settings_manager())
        cfg = svc._load_config()
        assert isinstance(cfg, EmailConfig)
        assert cfg.enabled is True
        assert cfg.smtp_host == 'smtp.example.com'
        assert cfg.smtp_port == 587
        assert cfg.smtp_security == 'starttls'
        assert cfg.smtp_username == 'mailer'
        assert cfg.from_address == 'noreply@example.com'
        assert cfg.from_display_name == 'AssetAtlas'
        assert cfg.reply_to_address == 'support@example.com'
        assert cfg.public_base_url == 'https://app.example.com'


# ── send_alert_email ────────────────────────────────────────────────────────

class TestSendAlertEmail:

    def test_disabled_returns_permanent(self):
        svc = EmailService(_settings_manager(enabled=False))
        with patch('src.auth.services.email_service.smtplib.SMTP') as smtp:
            ok, err, retriable = svc.send_alert_email(['a@x.com'], 'S', 'B')
            assert ok is False and err == EMAIL_DISABLED and retriable is False
            smtp.assert_not_called()

    def test_not_configured_returns_permanent(self):
        svc = EmailService(_settings_manager(smtp_host=''))
        ok, err, retriable = svc.send_alert_email(['a@x.com'], 'S', 'B')
        assert ok is False and err == EMAIL_NOT_CONFIGURED and retriable is False

    def test_success_puts_all_recipients_in_to_header(self):
        svc = EmailService(_settings_manager())
        cls_mock, instance = _make_smtp_ctx_mock()
        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            ok, err, retriable = svc.send_alert_email(
                ['a@x.com', 'b@y.com'], 'Subj', 'Body')
        assert ok is True and err is None and retriable is False
        sent_msg = instance.send_message.call_args[0][0]
        assert sent_msg['To'] == 'a@x.com, b@y.com'
        assert sent_msg['Subject'] == 'Subj'

    def test_partial_recipient_refusal_is_reported_as_failure(self):
        # smtplib raises only when ALL recipients are refused; a partial refusal
        # returns a non-empty dict from send_message. Without inspecting it, the
        # rejected recipient is silently dropped and the send looks successful.
        svc = EmailService(_settings_manager())
        cls_mock, instance = _make_smtp_ctx_mock()
        instance.send_message.return_value = {'b@y.com': (550, b'no such user')}
        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            ok, err, retriable = svc.send_alert_email(
                ['a@x.com', 'b@y.com'], 'Subj', 'Body')
        assert ok is False and retriable is False
        assert 'b@y.com' in err

    def test_config_load_error_never_raises(self):
        # send_alert_email documents "Never raises"; a corrupt setting (e.g. a
        # non-numeric smtp_port) must surface as a permanent failure, not a 500.
        svc = EmailService(_settings_manager(smtp_port='not-a-number'))
        ok, err, retriable = svc.send_alert_email(['a@x.com'], 'S', 'B')
        assert ok is False and retriable is False

    def test_smtp_4xx_is_retriable(self):
        svc = EmailService(_settings_manager())
        exc = smtplib.SMTPResponseException(451, b'try later')
        with patch.object(EmailService, '_connect_and_send', side_effect=exc):
            ok, err, retriable = svc.send_alert_email(['a@x.com'], 'S', 'B')
        assert ok is False and retriable is True and '451' in err

    def test_smtp_5xx_is_permanent(self):
        svc = EmailService(_settings_manager())
        exc = smtplib.SMTPResponseException(550, b'no such mailbox')
        with patch.object(EmailService, '_connect_and_send', side_effect=exc):
            ok, err, retriable = svc.send_alert_email(['a@x.com'], 'S', 'B')
        assert ok is False and retriable is False and '550' in err

    def test_auth_error_is_permanent(self):
        svc = EmailService(_settings_manager())
        exc = smtplib.SMTPAuthenticationError(535, b'bad creds')
        with patch.object(EmailService, '_connect_and_send', side_effect=exc):
            ok, err, retriable = svc.send_alert_email(['a@x.com'], 'S', 'B')
        assert ok is False and retriable is False

    def test_timeout_is_retriable(self):
        svc = EmailService(_settings_manager())
        with patch.object(EmailService, '_connect_and_send', side_effect=TimeoutError('slow')):
            ok, err, retriable = svc.send_alert_email(['a@x.com'], 'S', 'B')
        assert ok is False and retriable is True

    def test_connection_error_is_retriable(self):
        svc = EmailService(_settings_manager())
        with patch.object(EmailService, '_connect_and_send',
                          side_effect=smtplib.SMTPServerDisconnected('gone')):
            ok, err, retriable = svc.send_alert_email(['a@x.com'], 'S', 'B')
        assert ok is False and retriable is True

    def test_unknown_error_is_permanent(self):
        svc = EmailService(_settings_manager())
        with patch.object(EmailService, '_connect_and_send', side_effect=ValueError('weird')):
            ok, err, retriable = svc.send_alert_email(['a@x.com'], 'S', 'B')
        assert ok is False and retriable is False

    def test_recipients_refused_all_5xx_is_permanent(self):
        svc = EmailService(_settings_manager())
        exc = smtplib.SMTPRecipientsRefused({'a@x.com': (550, b'no such user')})
        with patch.object(EmailService, '_connect_and_send', side_effect=exc):
            ok, err, retriable = svc.send_alert_email(['a@x.com'], 'S', 'B')
        assert ok is False and retriable is False

    def test_recipients_refused_all_4xx_is_retriable(self):
        svc = EmailService(_settings_manager())
        exc = smtplib.SMTPRecipientsRefused({'a@x.com': (451, b'greylisted')})
        with patch.object(EmailService, '_connect_and_send', side_effect=exc):
            ok, err, retriable = svc.send_alert_email(['a@x.com'], 'S', 'B')
        assert ok is False and retriable is True

    def test_smtp_not_supported_is_permanent(self):
        svc = EmailService(_settings_manager())
        exc = smtplib.SMTPNotSupportedError('SMTPUTF8 not supported')
        with patch.object(EmailService, '_connect_and_send', side_effect=exc):
            ok, err, retriable = svc.send_alert_email(['a@x.com'], 'S', 'B')
        assert ok is False and retriable is False

    def test_send_still_returns_two_tuple_after_extraction(self):
        # Regression: extracting _connect_and_send must not change _send's contract.
        svc = EmailService(_settings_manager())
        cls_mock, _ = _make_smtp_ctx_mock()
        with patch('src.auth.services.email_service.smtplib.SMTP', cls_mock):
            result = svc.send_test_email('a@x.com')
        assert result == (True, None)
