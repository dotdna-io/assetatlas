# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for LdapService — uses unittest.mock for ldap3."""
from unittest.mock import MagicMock, patch
import pytest

from src.auth.services.ldap_service import LdapService, LdapUserInfo


def _settings(**overrides):
    """Default LDAP settings dict; override fields per test."""
    base = {
        'enabled': True,
        'server_url': 'ldaps://dc01.corp.com:636',
        'use_ssl': True,
        'verify_cert': True,
        'connect_timeout_seconds': 10,
        'bind_dn': 'CN=svc,DC=corp,DC=com',
        'bind_password': 'svcpass',
        'base_dn': 'DC=corp,DC=com',
        'user_search_filter': '(&(objectClass=person)(|(sAMAccountName={username})(mail={username})))',
        'attribute_mail': 'mail',
        'attribute_full_name': 'displayName',
        'attribute_groups': 'memberOf',
        'group_to_role_mapping': {
            'CN=AssetAtlas Admins,OU=Groups,DC=corp,DC=com': 'admin',
            'CN=AssetAtlas Super,OU=Groups,DC=corp,DC=com': 'super_admin',
        },
        'default_role': 'user',
        'allow_local_fallback': True,
    }
    base.update(overrides)
    return base


@pytest.fixture
def settings_manager():
    sm = MagicMock()
    cfg = _settings()
    sm.get.side_effect = lambda category, key, default=None: cfg.get(key, default)
    return sm


# ----- is_enabled --------------------------------------------------------

class TestIsEnabled:
    def test_returns_true_when_enabled_and_url_set(self, settings_manager):
        svc = LdapService(settings_manager)
        assert svc.is_enabled() is True

    def test_returns_false_when_disabled(self):
        sm = MagicMock()
        cfg = _settings(enabled=False)
        sm.get.side_effect = lambda c, k, d=None: cfg.get(k, d)
        assert LdapService(sm).is_enabled() is False

    def test_returns_false_when_url_missing(self):
        sm = MagicMock()
        cfg = _settings(server_url='')
        sm.get.side_effect = lambda c, k, d=None: cfg.get(k, d)
        assert LdapService(sm).is_enabled() is False


# ----- escape_filter -----------------------------------------------------

class TestEscape:
    def test_escapes_ldap_special_chars(self):
        # ldap3.utils.conv.escape_filter_chars handles parens, asterisk, backslash, NUL
        from ldap3.utils.conv import escape_filter_chars
        assert escape_filter_chars('foo*bar') == 'foo\\2abar'
        # LdapService should use this internally — confirm by triggering via authenticate
        # (covered indirectly in test_authenticate_escapes_username below)


# ----- authenticate ------------------------------------------------------

class TestAuthenticate:
    """authenticate() returns LdapUserInfo on success, None on rejection."""

    @patch('src.auth.services.ldap_service.Connection')
    @patch('src.auth.services.ldap_service.Server')
    def test_successful_auth_returns_user_info(self, MockServer, MockConn, settings_manager):
        # First connection (service bind) finds user
        svc_conn = MagicMock()
        svc_conn.bind.return_value = True
        svc_conn.search.return_value = True
        svc_conn.entries = [
            MagicMock(
                entry_dn='CN=alice,OU=People,DC=corp,DC=com',
                entry_attributes_as_dict={
                    'mail': ['alice@corp.com'],
                    'displayName': ['Alice Smith'],
                    'memberOf': ['CN=AssetAtlas Admins,OU=Groups,DC=corp,DC=com'],
                },
            ),
        ]
        # Second connection (user bind) succeeds
        user_conn = MagicMock()
        user_conn.bind.return_value = True

        MockConn.side_effect = [svc_conn, user_conn]

        result = LdapService(settings_manager).authenticate('alice', 'userpw')

        assert result is not None
        assert result.user_dn == 'CN=alice,OU=People,DC=corp,DC=com'
        assert result.email == 'alice@corp.com'
        assert result.full_name == 'Alice Smith'
        assert result.role == 'admin'  # mapped from memberOf
        assert result.username == 'alice'

    @patch('src.auth.services.ldap_service.Connection')
    @patch('src.auth.services.ldap_service.Server')
    def test_user_not_found_returns_none(self, MockServer, MockConn, settings_manager):
        svc_conn = MagicMock()
        svc_conn.bind.return_value = True
        svc_conn.search.return_value = True
        svc_conn.entries = []  # no match
        MockConn.return_value = svc_conn

        result = LdapService(settings_manager).authenticate('nobody', 'pw')
        assert result is None

    @patch('src.auth.services.ldap_service.Connection')
    @patch('src.auth.services.ldap_service.Server')
    def test_wrong_password_returns_none(self, MockServer, MockConn, settings_manager):
        svc_conn = MagicMock()
        svc_conn.bind.return_value = True
        svc_conn.search.return_value = True
        svc_conn.entries = [MagicMock(
            entry_dn='CN=alice,OU=People,DC=corp,DC=com',
            entry_attributes_as_dict={'mail': ['a@a.com'], 'displayName': ['Alice'], 'memberOf': []},
        )]
        user_conn = MagicMock()
        user_conn.bind.return_value = False  # wrong password
        MockConn.side_effect = [svc_conn, user_conn]

        result = LdapService(settings_manager).authenticate('alice', 'wrongpw')
        assert result is None

    @patch('src.auth.services.ldap_service.Connection')
    @patch('src.auth.services.ldap_service.Server')
    def test_service_bind_failure_returns_none(self, MockServer, MockConn, settings_manager):
        svc_conn = MagicMock()
        svc_conn.bind.return_value = False  # service account creds wrong
        MockConn.return_value = svc_conn

        result = LdapService(settings_manager).authenticate('alice', 'pw')
        assert result is None

    @patch('src.auth.services.ldap_service.Connection')
    @patch('src.auth.services.ldap_service.Server')
    def test_ldap_exception_returns_none_and_logs(self, MockServer, MockConn, settings_manager, caplog):
        from ldap3.core.exceptions import LDAPException
        MockConn.side_effect = LDAPException('connection refused')

        result = LdapService(settings_manager).authenticate('alice', 'pw')
        assert result is None
        assert any('LDAP' in record.message for record in caplog.records)

    @patch('src.auth.services.ldap_service.Connection')
    @patch('src.auth.services.ldap_service.Server')
    def test_decrypt_value_error_returns_none_and_logs(self, MockServer, MockConn, settings_manager, caplog):
        # decrypt() inside _bind_password raises ValueError when the stored
        # bind_password ciphertext is corrupt or the encryption key rotated.
        # Login must fall through to local password verify, not 500.
        MockServer.side_effect = ValueError('Failed to decrypt value: invalid token')

        result = LdapService(settings_manager).authenticate('alice', 'pw')
        assert result is None
        assert any('decrypt failed' in record.message for record in caplog.records)

    @patch('src.auth.services.ldap_service.Connection')
    @patch('src.auth.services.ldap_service.Server')
    def test_unexpected_exception_returns_none_and_logs(self, MockServer, MockConn, settings_manager, caplog):
        # Any other unexpected error (DNS failure, cert validation, etc.)
        # must also fall through cleanly rather than escalating to 500.
        MockServer.side_effect = RuntimeError('unexpected boom')

        result = LdapService(settings_manager).authenticate('alice', 'pw')
        assert result is None
        assert any('unexpected error' in record.message for record in caplog.records)

    @patch('src.auth.services.ldap_service.Connection')
    @patch('src.auth.services.ldap_service.Server')
    def test_authenticate_escapes_username_in_filter(self, MockServer, MockConn, settings_manager):
        """A username containing LDAP wildcards must be escaped before substitution."""
        svc_conn = MagicMock()
        svc_conn.bind.return_value = True
        svc_conn.search.return_value = True
        svc_conn.entries = []
        MockConn.return_value = svc_conn

        LdapService(settings_manager).authenticate('alice*', 'pw')

        # Inspect the search call — the filter passed to ldap3 should have escaped the *
        _, kwargs = svc_conn.search.call_args
        assert 'search_filter' in kwargs
        assert 'alice\\2a' in kwargs['search_filter']
        assert 'alice*' not in kwargs['search_filter']  # raw asterisk must NOT survive


# ----- role mapping ------------------------------------------------------

class TestRoleMapping:
    """Role resolution from group DNs via case-insensitive match."""

    @patch('src.auth.services.ldap_service.Connection')
    @patch('src.auth.services.ldap_service.Server')
    def test_super_admin_group_takes_precedence_over_admin(self, MockServer, MockConn, settings_manager):
        """When a user is in both Admins and Super, the higher role wins."""
        svc_conn = MagicMock()
        svc_conn.bind.return_value = True
        svc_conn.search.return_value = True
        svc_conn.entries = [MagicMock(
            entry_dn='CN=alice,DC=corp,DC=com',
            entry_attributes_as_dict={
                'mail': ['a@a.com'], 'displayName': ['A'],
                'memberOf': [
                    'CN=AssetAtlas Admins,OU=Groups,DC=corp,DC=com',
                    'CN=AssetAtlas Super,OU=Groups,DC=corp,DC=com',
                ],
            },
        )]
        user_conn = MagicMock()
        user_conn.bind.return_value = True
        MockConn.side_effect = [svc_conn, user_conn]

        result = LdapService(settings_manager).authenticate('alice', 'pw')
        assert result.role == 'super_admin'

    @patch('src.auth.services.ldap_service.Connection')
    @patch('src.auth.services.ldap_service.Server')
    def test_no_group_match_uses_default_role(self, MockServer, MockConn, settings_manager):
        svc_conn = MagicMock()
        svc_conn.bind.return_value = True
        svc_conn.search.return_value = True
        svc_conn.entries = [MagicMock(
            entry_dn='CN=alice,DC=corp,DC=com',
            entry_attributes_as_dict={'mail': ['a@a.com'], 'displayName': ['A'],
                                      'memberOf': ['CN=Random Group,DC=corp,DC=com']},
        )]
        user_conn = MagicMock()
        user_conn.bind.return_value = True
        MockConn.side_effect = [svc_conn, user_conn]

        result = LdapService(settings_manager).authenticate('alice', 'pw')
        assert result.role == 'user'  # default

    @patch('src.auth.services.ldap_service.Connection')
    @patch('src.auth.services.ldap_service.Server')
    def test_group_match_is_case_insensitive(self, MockServer, MockConn, settings_manager):
        svc_conn = MagicMock()
        svc_conn.bind.return_value = True
        svc_conn.search.return_value = True
        svc_conn.entries = [MagicMock(
            entry_dn='CN=alice,DC=corp,DC=com',
            entry_attributes_as_dict={
                'mail': ['a@a.com'], 'displayName': ['A'],
                'memberOf': ['cn=assetatlas admins,ou=groups,dc=corp,dc=com'],  # lowercase
            },
        )]
        user_conn = MagicMock()
        user_conn.bind.return_value = True
        MockConn.side_effect = [svc_conn, user_conn]

        result = LdapService(settings_manager).authenticate('alice', 'pw')
        assert result.role == 'admin'

    @patch('src.auth.services.ldap_service.Connection')
    @patch('src.auth.services.ldap_service.Server')
    def test_invalid_default_role_falls_back_to_user(self, MockServer, MockConn):
        """An invalid default_role setting falls back to 'user' (not propagated as-is)."""
        sm = MagicMock()
        cfg = _settings(default_role='administrator', group_to_role_mapping={})
        sm.get.side_effect = lambda c, k, d=None: cfg.get(k, d)
        svc_conn = MagicMock()
        svc_conn.bind.return_value = True
        svc_conn.search.return_value = True
        svc_conn.entries = [MagicMock(
            entry_dn='CN=alice,DC=corp,DC=com',
            entry_attributes_as_dict={'mail': ['a@a.com'], 'displayName': ['A'], 'memberOf': []},
        )]
        user_conn = MagicMock()
        user_conn.bind.return_value = True
        MockConn.side_effect = [svc_conn, user_conn]
        result = LdapService(sm).authenticate('alice', 'pw')
        assert result.role == 'user'  # invalid 'administrator' → fallback to 'user'


# ----- test_connection ---------------------------------------------------

class TestTestConnection:
    """test_connection() is a dry-run for the admin "Test LDAP" button."""

    @patch('src.auth.services.ldap_service.Connection')
    @patch('src.auth.services.ldap_service.Server')
    def test_returns_full_diagnostic_on_success(self, MockServer, MockConn, settings_manager):
        svc_conn = MagicMock()
        svc_conn.bind.return_value = True
        svc_conn.search.return_value = True
        svc_conn.entries = [MagicMock(
            entry_dn='CN=alice,DC=corp,DC=com',
            entry_attributes_as_dict={'mail': ['a@a.com'], 'displayName': ['A'],
                                      'memberOf': ['CN=AssetAtlas Admins,OU=Groups,DC=corp,DC=com']},
        )]
        user_conn = MagicMock()
        user_conn.bind.return_value = True
        MockConn.side_effect = [svc_conn, user_conn]

        result = LdapService(settings_manager).test_connection('alice', 'pw')

        assert result['success'] is True
        assert result['user_dn'] == 'CN=alice,DC=corp,DC=com'
        assert result['mapped_role'] == 'admin'
        assert 'email' in result['attributes']
        assert result['errors'] == []

    @patch('src.auth.services.ldap_service.Connection')
    @patch('src.auth.services.ldap_service.Server')
    def test_returns_failure_with_error_when_user_not_found(self, MockServer, MockConn, settings_manager):
        svc_conn = MagicMock()
        svc_conn.bind.return_value = True
        svc_conn.search.return_value = True
        svc_conn.entries = []
        MockConn.return_value = svc_conn

        result = LdapService(settings_manager).test_connection('nobody', 'pw')
        assert result['success'] is False
        assert any('user not found' in e.lower() for e in result['errors'])
