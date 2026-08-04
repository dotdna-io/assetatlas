# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for POST /api/auth/ldap/test"""
from unittest.mock import patch

import pytest


class TestLdapTestEndpoint:
    """Admin-only dry-run that exercises LDAP settings."""

    def test_returns_200_on_successful_test(self, blueprint_app, auth_headers):
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.LdapService') as LdapSvcCls:
            LdapSvcCls.return_value.is_enabled.return_value = True
            LdapSvcCls.return_value.test_connection.return_value = {
                'success': True,
                'user_dn': 'CN=alice,DC=corp,DC=com',
                'mapped_role': 'admin',
                'attributes': {'email': 'a@a.com', 'full_name': 'Alice', 'groups': []},
                'errors': [],
            }
            r = client.post(
                '/api/auth/ldap/test',
                headers=auth_headers(role='admin'),
                json={'username': 'alice', 'password': 'pw'},
            )
        assert r.status_code == 200
        assert r.json['success'] is True
        assert r.json['mapped_role'] == 'admin'

    def test_returns_200_with_errors_on_failure(self, blueprint_app, auth_headers):
        client = blueprint_app.test_client()
        with patch('src.api.blueprints.auth.LdapService') as LdapSvcCls:
            LdapSvcCls.return_value.is_enabled.return_value = True
            LdapSvcCls.return_value.test_connection.return_value = {
                'success': False, 'user_dn': None, 'mapped_role': None,
                'attributes': {}, 'errors': ['user not found'],
            }
            r = client.post(
                '/api/auth/ldap/test',
                headers=auth_headers(role='admin'),
                json={'username': 'nobody', 'password': 'pw'},
            )
        assert r.status_code == 200
        assert r.json['success'] is False
        assert 'user not found' in r.json['errors']

    def test_requires_admin(self, blueprint_app, auth_headers, _prime_regular_user):
        client = blueprint_app.test_client()
        r = client.post(
            '/api/auth/ldap/test',
            headers=auth_headers(role='user'),
            json={'username': 'alice', 'password': 'pw'},
        )
        assert r.status_code == 403

    def test_validation_error_on_missing_fields(self, blueprint_app, auth_headers):
        client = blueprint_app.test_client()
        r = client.post(
            '/api/auth/ldap/test',
            headers=auth_headers(role='admin'),
            json={'username': 'alice'},  # password missing
        )
        assert r.status_code == 400
        assert r.json['error'] == 'validation_error'
