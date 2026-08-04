# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Email service for authentication-related emails.

Uses SMTP via the Python stdlib (smtplib). Configuration lives in the
`email` category of system_settings and is loaded on every send (no cache)
so runtime changes take effect immediately.

The public base URL used to build links inside emails comes from
``security.public_base_url`` (with ``ASSETATLAS_PUBLIC_BASE_URL`` env var as
a bootstrap fallback) — see :mod:`src.api.utils.public_base_url`.

Password storage: the `smtp_password` setting is stored as a Fernet-encrypted
value (prefix "ENC:") using the project-wide ASSETATLAS_ENCRYPTION_KEY.
"""
from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Dict, Optional, Tuple, Type, Union

from src.api.utils.public_base_url import resolve_public_base_url
from src.modules.integrations.shared.encryption import decrypt, is_encrypted

logger = logging.getLogger(__name__)

SMTP_TIMEOUT_SECONDS = 10

# Sentinel error strings returned by send_test_email and matched by the HTTP
# route handler for 409 status mapping. Defined here so the producer and
# consumers share a single source of truth.
EMAIL_DISABLED = 'email_disabled'
EMAIL_NOT_CONFIGURED = 'email_not_configured'


@dataclass
class EmailConfig:
    enabled: bool
    smtp_host: str
    smtp_port: int
    smtp_security: str  # 'none' | 'starttls' | 'tls'
    smtp_username: str
    smtp_password: str  # plaintext after decryption
    from_address: str
    from_display_name: str
    reply_to_address: str
    public_base_url: str  # resolved from security.public_base_url


class EmailService:
    """
    Instance-based service for sending authentication-related emails.

    Created once at app startup with the application SettingsManager and
    assigned to current_app.email_service.
    """

    def __init__(self, settings_manager: Any):
        self._settings_manager = settings_manager

    # ── Message assembly ────────────────────────────────────────────────────

    def _is_configured(self, cfg: EmailConfig) -> bool:
        return bool(cfg.smtp_host and cfg.from_address)

    def _build_from_header(self, cfg: EmailConfig) -> str:
        if cfg.from_display_name:
            return f"{cfg.from_display_name} <{cfg.from_address}>"
        return cfg.from_address

    def _new_message(self, cfg: EmailConfig, to_email: str, subject: str, body: str) -> EmailMessage:
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = self._build_from_header(cfg)
        msg['To'] = to_email
        if cfg.reply_to_address:
            msg['Reply-To'] = cfg.reply_to_address
        msg.set_content(body)
        return msg

    # ── Public: send_* methods ──────────────────────────────────────────────

    def send_password_reset_email(
        self,
        to_email: str,
        username: str,
        reset_token: str,
        expires_in_hours: int = 1,
    ) -> bool:
        cfg = self._load_config()
        if not cfg.enabled:
            logger.info("email.send skipped (disabled): password_reset -> %s", to_email)
            return False
        if not self._is_configured(cfg):
            logger.warning("email.send skipped (not configured): password_reset -> %s", to_email)
            return False

        reset_link = f"{cfg.public_base_url.rstrip('/')}/password-reset/verify?token={reset_token}"
        body = (
            f"Hello {username},\n\n"
            f"We received a request to reset your password for AssetAtlas.\n\n"
            f"Reset your password here:\n{reset_link}\n\n"
            f"This link expires in {expires_in_hours} hour(s). "
            f"If you didn't request this, you can ignore this email.\n\n"
            f"— AssetAtlas"
        )
        msg = self._new_message(cfg, to_email, "AssetAtlas password reset", body)
        ok, _ = self._send(msg, cfg)
        logger.info("email.send type=password_reset to=%s success=%s", to_email, ok)
        return ok

    def send_invite_email(
        self,
        to_email: str,
        username: str,
        invite_token: str,
        expires_in_hours: int = 168,
    ) -> bool:
        cfg = self._load_config()
        if not cfg.enabled:
            logger.info("email.send skipped (disabled): invite -> %s", to_email)
            return False
        if not self._is_configured(cfg):
            logger.warning("email.send skipped (not configured): invite -> %s", to_email)
            return False

        invite_link = f"{cfg.public_base_url.rstrip('/')}/onboarding?token={invite_token}"
        days = max(1, expires_in_hours // 24)
        body = (
            f"Hello {username},\n\n"
            f"You've been invited to AssetAtlas. Activate your account by setting "
            f"your password:\n{invite_link}\n\n"
            f"This invitation expires in {days} day(s). "
            f"If you weren't expecting this invitation, you can ignore this email.\n\n"
            f"— AssetAtlas"
        )
        msg = self._new_message(cfg, to_email, "Welcome to AssetAtlas — set your password", body)
        ok, _ = self._send(msg, cfg)
        logger.info("email.send type=invite to=%s success=%s", to_email, ok)
        return ok

    def send_password_changed_notification(
        self,
        to_email: str,
        username: str,
    ) -> bool:
        cfg = self._load_config()
        if not cfg.enabled:
            logger.info("email.send skipped (disabled): password_changed -> %s", to_email)
            return False
        if not self._is_configured(cfg):
            logger.warning("email.send skipped (not configured): password_changed -> %s", to_email)
            return False

        body = (
            f"Hello {username},\n\n"
            f"Your AssetAtlas password was successfully changed.\n\n"
            f"If you made this change, you can ignore this email. "
            f"If you did NOT make this change, please contact your administrator "
            f"immediately — your account may be compromised.\n\n"
            f"— AssetAtlas Security"
        )
        msg = self._new_message(cfg, to_email, "AssetAtlas password changed", body)
        ok, _ = self._send(msg, cfg)
        logger.info("email.send type=password_changed to=%s success=%s", to_email, ok)
        return ok

    def send_welcome_email(
        self,
        to_email: str,
        username: str,
    ) -> bool:
        cfg = self._load_config()
        if not cfg.enabled:
            logger.info("email.send skipped (disabled): welcome -> %s", to_email)
            return False
        if not self._is_configured(cfg):
            logger.warning("email.send skipped (not configured): welcome -> %s", to_email)
            return False

        login_url = (
            f"{cfg.public_base_url.rstrip('/')}/login"
            if cfg.public_base_url else ''
        )
        body = (
            f"Hello {username},\n\n"
            f"Welcome to AssetAtlas. Your account has been created.\n\n"
            f"Username: {username}\n"
            + (f"Login: {login_url}\n\n" if login_url else "\n")
            + "Please contact your administrator if you need help signing in.\n\n"
            "— AssetAtlas"
        )
        msg = self._new_message(cfg, to_email, "Welcome to AssetAtlas", body)
        ok, _ = self._send(msg, cfg)
        logger.info("email.send type=welcome to=%s success=%s", to_email, ok)
        return ok

    def send_test_email(self, recipient: str) -> Tuple[bool, Optional[str]]:
        """Send the fixed test message using the currently-saved config.

        Deliberately does NOT check ``cfg.enabled``. This is the one send an
        admin asks for explicitly, and gating it on the master toggle inverts
        the natural setup order: you would have to arm live outbound mail —
        invitations, password resets, alerts — before being allowed to confirm
        the relay even works. Every other sender still honours the toggle;
        see ``send_alert_email`` below, whose ``cfg.enabled`` check is what
        makes "outbound email off" mean no mail leaves the system.
        """
        cfg = self._load_config()
        if not self._is_configured(cfg):
            return False, EMAIL_NOT_CONFIGURED

        body = (
            "This is a test email from AssetAtlas.\n\n"
            "If you received this, your SMTP settings are working correctly."
        )
        msg = self._new_message(cfg, recipient, "AssetAtlas test email", body)
        ok, err = self._send(msg, cfg)
        logger.info("email.send type=test to=%s success=%s", recipient, ok)
        return ok, err

    def send_alert_email(
        self, recipients: list[str], subject: str, body: str
    ) -> Tuple[bool, Optional[str], bool]:
        """Send one plaintext alert email to all `recipients` (To header).

        Returns (ok, error, retriable). `retriable` distinguishes a transient
        SMTP failure (timeout, connection drop, 4xx) worth a backoff retry from a
        permanent one (auth, 5xx, disabled/unconfigured). Returned as a plain
        bool so this service stays decoupled from alert_delivery's DeliveryResult.
        Never raises.
        """
        try:
            cfg = self._load_config()
            if not cfg.enabled:
                return False, EMAIL_DISABLED, False
            if not self._is_configured(cfg):
                return False, EMAIL_NOT_CONFIGURED, False
            msg = self._new_message(cfg, ", ".join(recipients), subject, body)
            refused = self._connect_and_send(msg, cfg)
            if refused:
                # PARTIAL refusal: smtplib accepted at least one recipient but
                # rejected these. It only raises SMTPRecipientsRefused when
                # *every* recipient is refused, so without this check a partial
                # drop reports a clean success. Retrying re-sends to the
                # already-accepted addresses, so fail permanently and surface the
                # refused set rather than silently losing recipients.
                return False, f"recipients partially refused: {refused}", False
            logger.info("email.send type=alert to=%s success=True", recipients)
            return True, None, False
        except smtplib.SMTPRecipientsRefused as exc:
            # Not an SMTPResponseException: carries a {recipient: (code, msg)}
            # dict, no single smtp_code. Retry only if every refusal is a
            # transient 4xx; a permanent 5xx (bad/unknown mailbox) is a config
            # error that retries can't fix.
            codes = [code for code, _ in exc.recipients.values()]
            retriable = bool(codes) and all(400 <= code < 500 for code in codes)
            return False, f"recipients refused: {exc.recipients}", retriable
        except smtplib.SMTPResponseException as exc:
            # 4xx = transient (greylisting, mailbox busy); 5xx = permanent (auth
            # denied, relay refused). SMTPSenderRefused / SMTPDataError /
            # SMTPConnectError / SMTPHeloError / SMTPAuthenticationError are all
            # SMTPResponseException subclasses and are classified here by code.
            retriable = 400 <= exc.smtp_code < 500
            detail = (exc.smtp_error.decode(errors='replace')
                      if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error))
            return False, f"{exc.smtp_code} {detail}", retriable
        except smtplib.SMTPServerDisconnected as exc:
            # Transport dropped mid-exchange — worth a backoff retry.
            return False, str(exc), True
        except smtplib.SMTPException as exc:
            # Any other SMTP protocol error (e.g. SMTPNotSupportedError) is a
            # permanent config/protocol issue, not transient. Must precede the
            # OSError branch because SMTPException subclasses OSError.
            return False, str(exc), False
        except (TimeoutError, ConnectionError, OSError) as exc:
            # Pure network/socket transport failure (not an SMTP protocol error).
            return False, str(exc), True
        except Exception as exc:  # unknown / config-load error — don't burn retries
            return False, str(exc), False

    def get_public_base_url(self) -> str:
        """The configured public base URL, '' when unset. Public because the alert
        delivery drain runs in the scheduler worker with no Flask app context and so
        cannot reach current_app; the email channel reads it through the injected
        service. Trailing slash already stripped by resolve_public_base_url."""
        return resolve_public_base_url(self._settings_manager)

    # ── Private: config loading ─────────────────────────────────────────────

    def _load_config(self) -> EmailConfig:
        """Called at the start of every send_*. No caching — email volume is
        tiny and freshness matters more than the ~1 ms we'd save."""
        g = self._settings_manager.get

        def _s(key: str, default: str = '') -> str:
            v = g('email', key, default)
            return v if isinstance(v, str) else str(v) if v is not None else default

        raw_password = g('email', 'smtp_password', '')
        if isinstance(raw_password, str) and is_encrypted(raw_password):
            try:
                password = decrypt(raw_password)
            except ValueError as exc:
                # Stored ciphertext can't be decrypted with the current
                # ENCRYPTION_KEY (key rotation, DB restored from a different
                # environment, etc.). Don't 500 the caller — fall through with
                # an empty password so SMTP auth fails cleanly and the public
                # endpoint returns email_sent=False instead.
                logger.warning(
                    "email.smtp_password decrypt failed (%s) — treating as unset",
                    exc,
                )
                password = ''
        elif isinstance(raw_password, str):
            password = raw_password
        else:
            password = ''

        return EmailConfig(
            enabled=bool(g('email', 'enabled', False)),
            smtp_host=_s('smtp_host'),
            smtp_port=int(g('email', 'smtp_port', 587) or 587),
            smtp_security=_s('smtp_security', 'starttls'),
            smtp_username=_s('smtp_username'),
            smtp_password=password,
            from_address=_s('from_address'),
            from_display_name=_s('from_display_name', 'AssetAtlas'),
            reply_to_address=_s('reply_to_address'),
            public_base_url=resolve_public_base_url(self._settings_manager),
        )

    # ── Private: transport ──────────────────────────────────────────────────

    def _connect_and_send(
        self, msg: EmailMessage, cfg: EmailConfig
    ) -> Dict[str, Tuple[int, bytes]]:
        """Open the configured SMTP connection and send `msg`. Raises on any
        transport/auth failure — callers decide how to report it. Shared by
        _send (auth flows) and send_alert_email (alert delivery).

        Returns smtplib's refused-recipients mapping: empty when every recipient
        was accepted, or ``{recipient: (code, msg)}`` for a PARTIAL refusal (some
        accepted, some rejected — smtplib only raises SMTPRecipientsRefused when
        *all* recipients are refused). _send ignores this (single recipient);
        send_alert_email inspects it so a partial drop isn't reported as success."""
        client_cls: Type[Union[smtplib.SMTP, smtplib.SMTP_SSL]]
        if cfg.smtp_security == 'tls':
            client_cls = smtplib.SMTP_SSL
        else:
            client_cls = smtplib.SMTP
        with client_cls(cfg.smtp_host, cfg.smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as client:
            if cfg.smtp_security == 'starttls':
                client.starttls()
            if cfg.smtp_username:
                client.login(cfg.smtp_username, cfg.smtp_password)
            return client.send_message(msg)

    def _send(self, msg: EmailMessage, cfg: EmailConfig) -> Tuple[bool, Optional[str]]:
        """
        Connect to the configured SMTP relay and send the given message.
        Returns (success, error_message). Never raises — all exceptions are
        caught and returned as (False, str(exc)).
        """
        try:
            self._connect_and_send(msg, cfg)
            return True, None
        except Exception as exc:  # smtplib.SMTPException, OSError, socket.timeout, etc.
            logger.warning(
                "email.send failure to %s:%s (security=%s): %s",
                cfg.smtp_host, cfg.smtp_port, cfg.smtp_security, exc,
            )
            return False, str(exc)
