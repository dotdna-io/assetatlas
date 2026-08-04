# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Category-specific hook registry for the settings blueprint.

Each hook object provides three callables:

- validate(key, value)      -> value (or raise HookValidationError)
- pre_store(key, value)     -> value to persist (may transform e.g. encrypt)
- audit_redact(key)         -> True if audit log should record "<redacted>"
                               instead of the real old/new values for this key.

The generic update_setting path in settings_routes.py looks up the registered
hook for the request's category; if none is registered, the request is handled
exactly as before.
"""
from __future__ import annotations

import re
from typing import Any, Optional, Protocol
from urllib.parse import urlparse

from flask import has_request_context, request
from pydantic import EmailStr, TypeAdapter, ValidationError as PydanticValidationError

from src.modules.integrations.shared.encryption import encrypt


class HookValidationError(Exception):
    """Raised by a hook's validate() to indicate a value is invalid."""


class SettingsCategoryHooks(Protocol):
    def validate(self, key: str, value: Any) -> Any: ...
    def pre_store(self, key: str, value: Any) -> Any: ...
    def audit_redact(self, key: str) -> bool: ...


# ── Email ───────────────────────────────────────────────────────────────────

# Shared Pydantic adapter for email validation (RFC-compliant, matches pydantic[email]).
_EMAIL_ADAPTER = TypeAdapter(EmailStr)

_ALLOWED_SECURITY = frozenset({'none', 'starttls', 'tls'})


class EmailSettingsHooks:
    """Hooks for the 'email' settings category."""

    # ── validate ────────────────────────────────────────────────────────────

    def validate(self, key: str, value: Any) -> Any:
        if key == 'smtp_port':
            return self._validate_port(value)
        if key == 'smtp_security':
            return self._validate_security(value)
        if key in ('from_address', 'reply_to_address'):
            return self._validate_email(key, value)
        # smtp_host, smtp_username, smtp_password, from_display_name, enabled:
        # rely on the generic value-type validation in SettingsManager.
        return value

    @staticmethod
    def _validate_port(value: Any) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise HookValidationError("smtp_port must be an integer")
        if not (1 <= value <= 65535):
            raise HookValidationError("smtp_port must be between 1 and 65535")
        return value

    @staticmethod
    def _validate_security(value: Any) -> str:
        if value not in _ALLOWED_SECURITY:
            raise HookValidationError(
                f"smtp_security must be one of {sorted(_ALLOWED_SECURITY)}"
            )
        return value

    @staticmethod
    def _validate_email(key: str, value: Any) -> str:
        if value == '':
            return ''  # Empty = not-yet-configured / cleared.
        try:
            return _EMAIL_ADAPTER.validate_python(value)
        except PydanticValidationError as exc:
            raise HookValidationError(f"{key} is not a valid email address: {exc}") from exc

    # ── pre_store ───────────────────────────────────────────────────────────

    def pre_store(self, key: str, value: Any) -> Any:
        if key == 'smtp_password':
            if value == '' or value is None:
                return ''  # Explicit clear.
            return encrypt(str(value))
        return value

    # ── audit_redact ────────────────────────────────────────────────────────

    def audit_redact(self, key: str) -> bool:
        return key == 'smtp_password'


# ── Shared base for single-secret categories ────────────────────────────────


class _SingleKeyEncryptHooks:
    """Base for hooks that encrypt exactly one secret key in a category.

    EmailSettingsHooks adds extra ``validate()`` logic on top, so it does NOT
    use this base. Only LDAP and OIDC fit cleanly today.
    """

    def __init__(self, secret_key: str):
        self._secret_key = secret_key

    def validate(self, key: str, value: Any) -> Any:
        # SettingsManager's generic value-type validation covers all keys.
        return value

    def pre_store(self, key: str, value: Any) -> Any:
        if key == self._secret_key:
            if value == '' or value is None:
                return ''
            return encrypt(str(value))
        return value

    def audit_redact(self, key: str) -> bool:
        return key == self._secret_key


# ── LDAP ────────────────────────────────────────────────────────────────────


class LdapSettingsHooks(_SingleKeyEncryptHooks):
    """Hooks for the 'ldap' settings category — encrypts bind_password."""

    def __init__(self):
        super().__init__('bind_password')


# ── OIDC ────────────────────────────────────────────────────────────────────


class OidcSettingsHooks(_SingleKeyEncryptHooks):
    """Hooks for the 'oidc' settings category.

    - Encrypts ``client_secret`` at rest (inherited from base).
    - Validates ``discovery_url`` is https + resolves to a public IP — defends
      against SSRF (cloud instance-metadata, internal services, file://) when
      a compromised admin sets the value.
    """

    def __init__(self):
        super().__init__('client_secret')

    def validate(self, key: str, value: Any) -> Any:
        if key == 'discovery_url':
            return self._validate_discovery_url(value)
        if key == 'frontend_complete_url':
            return self._validate_frontend_complete_url(value)
        return super().validate(key, value)

    @staticmethod
    def _validate_frontend_complete_url(value: Any) -> str:
        """Constrain frontend_complete_url to defeat open-redirect.

        Allowed shapes:
          - Relative path starting with '/' (e.g. '/oidc-complete')
          - Absolute https URL whose origin matches the admin's current
            request origin (the admin who's setting this is connected from
            the same hostname they're configuring)

        Rejected:
          - http:// (downgrade)
          - Other absolute https origins (open-redirect / phishing)
          - javascript:, data:, file:, etc.
          - Protocol-relative '//attacker.com/path'
        """
        if value == '' or value is None:
            return ''
        if not isinstance(value, str):
            raise HookValidationError("frontend_complete_url must be a string")

        # Protocol-relative URLs ('//host/path') are absolute in browser
        # interpretation but urlparse reports no scheme. Reject explicitly.
        if value.startswith('//'):
            raise HookValidationError(
                "frontend_complete_url must be a relative path starting with '/' "
                "or an absolute https URL on this server's origin "
                "(protocol-relative URLs not allowed)"
            )

        # Relative path: must start with single '/'.
        if value.startswith('/'):
            return value

        parsed = urlparse(value)
        if parsed.scheme != 'https' or not parsed.netloc:
            raise HookValidationError(
                "frontend_complete_url must be a relative path starting with '/' "
                "or an absolute https URL on this server's origin"
            )

        # Absolute https URL: origin must match the admin's request origin.
        if not has_request_context():
            # Programmatic write outside request context — be safe and refuse.
            raise HookValidationError(
                "frontend_complete_url absolute URLs can only be set inside "
                "an admin HTTP request context"
            )
        admin_origin = OidcSettingsHooks._request_origin()
        value_origin = f"{parsed.scheme}://{parsed.netloc}".lower()
        if value_origin != admin_origin:
            raise HookValidationError(
                f"frontend_complete_url origin {value_origin!r} does not match "
                f"this server's origin {admin_origin!r}; allowed shapes: relative "
                "path starting with '/' or absolute https URL on this origin"
            )
        return value

    @staticmethod
    def _request_origin() -> Optional[str]:
        """Return the admin request's origin (scheme://host[:port]) lowercase."""
        # request.host_url is "scheme://host[:port]/" — strip trailing slash.
        return request.host_url.rstrip('/').lower()

    @staticmethod
    def _validate_discovery_url(value: Any) -> str:
        # Empty = cleared / not yet configured. Allowed at write time so an
        # admin can disable OIDC by clearing the URL without other gymnastics;
        # is_enabled() guards against using a blank URL at runtime.
        if value == '' or value is None:
            return ''
        if not isinstance(value, str):
            raise HookValidationError("discovery_url must be a string")

        # Lazy import to avoid pulling authlib into modules that don't need it.
        from src.auth.services.oidc_service import (
            OidcSsrfError,
            _assert_https_url,
            _assert_public_host,
        )
        try:
            host = _assert_https_url(value)
            _assert_public_host(host)
        except OidcSsrfError as exc:
            raise HookValidationError(str(exc)) from exc
        return value


# ── Teams ──────────────────────────────────────────────────────────────────


_UUID_RE = re.compile(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
    r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
)


class TeamsSettingsHooks(_SingleKeyEncryptHooks):
    """Hooks for the 'teams' settings category — encrypts bot_app_password."""

    # The redaction sentinel the UI receives on read; must never be written.
    _PASSWORD_SENTINEL = '__SET__'

    def __init__(self):
        super().__init__('bot_app_password')

    def validate(self, key: str, value: Any) -> Any:
        if key in ('bot_app_id', 'bot_app_tenant_id'):
            return self._validate_uuid_or_empty(key, value)
        if key == 'messaging_endpoint':
            return self._validate_messaging_endpoint(value)
        return super().validate(key, value)

    def pre_store(self, key: str, value: Any) -> Any:
        if key == 'bot_app_password' and value == self._PASSWORD_SENTINEL:
            raise HookValidationError(
                "bot_app_password sentinel '__SET__' is read-only; the UI "
                "must omit the field when the operator hasn't entered a new "
                "secret."
            )
        return super().pre_store(key, value)

    @staticmethod
    def _validate_uuid_or_empty(key: str, value: Any) -> str:
        if value == '' or value is None:
            return ''
        if not isinstance(value, str) or not _UUID_RE.match(value):
            raise HookValidationError(f"{key} must be a UUID")
        return value

    @staticmethod
    def _validate_messaging_endpoint(value: Any) -> str:
        if value == '' or value is None:
            return ''
        if not isinstance(value, str):
            raise HookValidationError("messaging_endpoint must be a string")
        if not value.lower().startswith('https://'):
            raise HookValidationError(
                "messaging_endpoint must be an HTTPS URL (Teams rejects http://)"
            )
        return value


# ── Security ────────────────────────────────────────────────────────────────


class SecuritySettingsHooks:
    """Hooks for the 'security' settings category.

    Validates ``public_base_url``: must be HTTPS (Teams + IdPs reject http://),
    must parse with a netloc, and is normalized by stripping a trailing slash
    so consumers don't need to ``.rstrip('/')`` defensively. Empty is allowed
    (falls back to the ASSETATLAS_PUBLIC_BASE_URL env var at read time).
    """

    def validate(self, key: str, value: Any) -> Any:
        if key == 'public_base_url':
            return self._validate_public_base_url(value)
        return value

    def pre_store(self, key: str, value: Any) -> Any:
        return value

    def audit_redact(self, key: str) -> bool:
        return False

    @staticmethod
    def _validate_public_base_url(value: Any) -> str:
        if value == '' or value is None:
            return ''
        if not isinstance(value, str):
            raise HookValidationError("public_base_url must be a string")
        parsed = urlparse(value)
        if parsed.scheme != 'https':
            raise HookValidationError(
                "public_base_url must be an HTTPS URL (Teams, OIDC IdPs, "
                "and Bot Framework all reject http://)"
            )
        if not parsed.netloc:
            raise HookValidationError(
                "public_base_url must include a hostname "
                "(e.g., https://assetatlas.example.com)"
            )
        return value.rstrip('/')


# ── Registry ────────────────────────────────────────────────────────────────

CATEGORY_HOOKS: dict[str, SettingsCategoryHooks] = {
    'email': EmailSettingsHooks(),
    'ldap': LdapSettingsHooks(),
    'oidc': OidcSettingsHooks(),
    'security': SecuritySettingsHooks(),
    'teams': TeamsSettingsHooks(),
}
