# SPDX-License-Identifier: AGPL-3.0-or-later
"""LDAP authentication service.

Wraps the `ldap3` library to authenticate end-users against a directory
(typically Active Directory). Pure logic — no Flask context, no DB. Settings
are read from the injected `settings_manager` (Flask `current_app.settings_manager`
in production, `MagicMock` in tests).

The service performs a 2-bind dance per authentication:
  1. Bind as the configured `bind_dn` service account, search for the user
     using the configured filter (with the submitted username substituted in
     after escaping).
  2. If found, bind as the user's DN with their submitted password to verify
     credentials.

Group-to-role mapping is computed from the user's `memberOf` attribute. The
service is intentionally stateless — every call opens a fresh `Connection`,
following the same conventions as the asset connector at
`src/modules/integrations/connectors/active_directory_connector.py`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.auth.services._role_utils import resolve_role
from src.modules.integrations.shared.encryption import decrypt, is_encrypted

try:
    from ldap3 import Server, Connection, Tls, ALL, SIMPLE
    from ldap3.core.exceptions import LDAPException
    from ldap3.utils.conv import escape_filter_chars
except ImportError:  # ldap3 is in requirements-prod.txt; this branch is for type-checkers
    Server = Connection = Tls = ALL = SIMPLE = None  # type: ignore
    LDAPException = Exception  # type: ignore

    def escape_filter_chars(value):  # type: ignore
        return value

logger = logging.getLogger(__name__)


@dataclass
class LdapUserInfo:
    """Information about a user successfully authenticated against LDAP."""
    username: str           # the original input the user typed (sAMAccountName / mail / UPN)
    user_dn: str            # the DN we bound as
    email: str
    full_name: str
    role: str               # 'user' | 'admin' | 'super_admin'
    groups: List[str] = field(default_factory=list)


class LdapService:
    """Authenticate users against an LDAP/AD directory."""

    SETTINGS_CATEGORY = 'ldap'

    def __init__(self, settings_manager):
        self._settings = settings_manager

    # ----- introspection -------------------------------------------------

    def is_enabled(self) -> bool:
        """Return True when LDAP auth is configured and enabled."""
        if not self._get('enabled', False):
            return False
        if not self._get('server_url', ''):
            return False
        return True

    def allow_local_fallback(self) -> bool:
        return bool(self._get('allow_local_fallback', True))

    # ----- public auth API -----------------------------------------------

    def authenticate(self, username: str, password: str) -> Optional[LdapUserInfo]:
        """Bind as the user; return LdapUserInfo on success, None on any failure.

        Failures (bad creds, missing user, LDAP errors) are logged but never raised
        — caller treats `None` as "LDAP rejected, fall through to local password".
        """
        if not username or not password:
            return None
        try:
            server = self._build_server()
            entry = self._lookup_user(server, username)
            if entry is None:
                logger.info("LDAP auth: user %r not found", username)
                return None

            if not self._bind_as_user(server, entry['user_dn'], password):
                logger.info("LDAP auth: user %r found but bind failed", username)
                return None

            return self._build_user_info(username, entry)
        except LDAPException as e:
            logger.warning("LDAP auth: directory error for %r: %s", username, e)
            return None
        except ValueError as e:
            # Raised by encryption.decrypt() when the bind_password ciphertext
            # is corrupt or the encryption key has rotated. Fall through to
            # local password verify rather than 500-ing the login.
            logger.warning("LDAP auth: bind_password decrypt failed for %r: %s", username, e)
            return None
        except Exception as e:
            # Defensive catch-all so a misconfigured directory (DNS failure,
            # cert validation error, etc.) never escalates to a 500.
            logger.warning("LDAP auth: unexpected error for %r: %s", username, e)
            return None

    def test_connection(self, username: str, password: str) -> Dict[str, Any]:
        """Diagnostic dry-run for the admin Test LDAP button.

        Returns a dict with diagnostic fields (does NOT touch the local user DB).
        """
        result: Dict[str, Any] = {
            'success': False,
            'user_dn': None,
            'mapped_role': None,
            'attributes': {},
            'errors': [],
        }
        if not username or not password:
            result['errors'].append('username and password are required for the dry-run')
            return result
        try:
            server = self._build_server()
            entry = self._lookup_user(server, username)
            if entry is None:
                result['errors'].append('user not found in directory under the configured base DN/filter')
                return result

            if not self._bind_as_user(server, entry['user_dn'], password):
                result['errors'].append('user found but bind with the supplied password failed')
                return result

            info = self._build_user_info(username, entry)
            result['success'] = True
            result['user_dn'] = info.user_dn
            result['mapped_role'] = info.role
            result['attributes'] = {
                'email': info.email,
                'full_name': info.full_name,
                'groups': info.groups,
            }
        except LDAPException as e:
            result['errors'].append(f'LDAP error: {e}')
        return result

    # ----- internals -----------------------------------------------------

    def _get(self, key: str, default: Any = None) -> Any:
        return self._settings.get(self.SETTINGS_CATEGORY, key, default)

    def _bind_password(self) -> str:
        raw = self._get('bind_password', '') or ''
        if is_encrypted(raw):
            return decrypt(raw)
        return raw

    def _build_server(self) -> Any:
        url = self._get('server_url', '')
        use_ssl = bool(self._get('use_ssl', True))
        verify_cert = bool(self._get('verify_cert', True))
        timeout = int(self._get('connect_timeout_seconds', 10))

        tls = None
        if use_ssl:
            import ssl as _ssl
            tls = Tls(validate=_ssl.CERT_REQUIRED if verify_cert else _ssl.CERT_NONE)

        return Server(url, use_ssl=use_ssl, get_info=ALL, tls=tls, connect_timeout=timeout)

    def _lookup_user(self, server, username: str) -> Optional[Dict[str, Any]]:
        """Bind as the service account, search for the user."""
        bind_dn = self._get('bind_dn', '')
        bind_pw = self._bind_password()
        timeout = int(self._get('connect_timeout_seconds', 10))

        # `connect_timeout_seconds` is reused as the per-op receive_timeout — both
        # the TCP connect (Server-level, set above) and the LDAP bind/search response
        # wait should be bounded by the same operator-configured value.
        conn = Connection(
            server,
            user=bind_dn,
            password=bind_pw,
            authentication=SIMPLE,
            auto_bind=False,
            receive_timeout=timeout,
        )
        if not conn.bind():
            logger.warning("LDAP service-account bind failed for %s", bind_dn)
            return None

        try:
            base_dn = self._get('base_dn', '')
            filter_template = self._get(
                'user_search_filter',
                '(&(objectClass=person)(sAMAccountName={username}))',
            )
            search_filter = filter_template.replace('{username}', escape_filter_chars(username))

            attrs = [
                self._get('attribute_mail', 'mail'),
                self._get('attribute_full_name', 'displayName'),
                self._get('attribute_groups', 'memberOf'),
            ]
            conn.search(search_base=base_dn, search_filter=search_filter, attributes=attrs)
            if not conn.entries:
                return None
            entry = conn.entries[0]
            return {
                'user_dn': entry.entry_dn,
                'attributes': dict(entry.entry_attributes_as_dict),
            }
        finally:
            try:
                conn.unbind()
            except Exception:
                pass

    def _bind_as_user(self, server, user_dn: str, password: str) -> bool:
        timeout = int(self._get('connect_timeout_seconds', 10))
        conn = Connection(
            server,
            user=user_dn,
            password=password,
            authentication=SIMPLE,
            auto_bind=False,
            receive_timeout=timeout,
        )
        try:
            return bool(conn.bind())
        finally:
            try:
                conn.unbind()
            except Exception:
                pass

    def _build_user_info(self, username: str, entry: Dict[str, Any]) -> LdapUserInfo:
        attrs = entry['attributes']
        mail_attr = self._get('attribute_mail', 'mail')
        name_attr = self._get('attribute_full_name', 'displayName')
        group_attr = self._get('attribute_groups', 'memberOf')

        email = self._first(attrs.get(mail_attr)) or ''
        full_name = self._first(attrs.get(name_attr)) or ''
        groups = list(attrs.get(group_attr) or [])
        role = self._resolve_role(groups)
        return LdapUserInfo(
            username=username,
            user_dn=entry['user_dn'],
            email=email,
            full_name=full_name,
            role=role,
            groups=groups,
        )

    @staticmethod
    def _first(value) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, list):
            return value[0] if value else None
        return value

    def _resolve_role(self, groups: List[str]) -> str:
        return resolve_role(groups, self._get, 'LDAP')
