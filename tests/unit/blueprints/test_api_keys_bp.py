# SPDX-License-Identifier: AGPL-3.0-or-later
"""Blueprint tests for /api/api-keys endpoints."""

import pytest
from datetime import datetime, timezone, timedelta


class TestCreateApiKey:
    def test_admin_can_create(self, client, auth_headers, blueprint_app):
        blueprint_app.api_key_service.create_key.return_value = {
            'id': 'key-uuid',
            'name': 'Test Key',
            'key': 'aa_abc12345678901234567890abcdef1234567890',
            'key_prefix': 'aa_abc12',
            'expires_at': datetime(2027, 1, 1, tzinfo=timezone.utc),
            'created_at': datetime(2026, 4, 2, tzinfo=timezone.utc),
        }

        resp = client.post(
            '/api/api-keys',
            json={'name': 'Test Key', 'expires_at': '2027-01-01T00:00:00Z'},
            headers=auth_headers('admin'),
        )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body['success'] is True
        assert 'key' in body['data']

    def test_regular_user_forbidden(self, client, auth_headers, _prime_regular_user):
        resp = client.post(
            '/api/api-keys',
            json={'name': 'Test', 'expires_at': '2027-01-01T00:00:00Z'},
            headers=auth_headers('user'),
        )
        assert resp.status_code == 403

    def test_missing_name_returns_400(self, client, auth_headers):
        resp = client.post(
            '/api/api-keys',
            json={'expires_at': '2027-01-01T00:00:00Z'},
            headers=auth_headers('admin'),
        )
        assert resp.status_code == 400


class TestListApiKeys:
    def test_admin_can_list(self, client, auth_headers, blueprint_app):
        blueprint_app.api_key_service.list_keys.return_value = [
            {'id': 'k1', 'name': 'Key 1', 'key_prefix': 'aa_abc12',
             'expires_at': datetime(2027, 1, 1, tzinfo=timezone.utc),
             'is_active': True, 'last_used_at': None, 'last_used_ip': None,
             'created_at': datetime(2026, 4, 2, tzinfo=timezone.utc),
             'user_id': 'u1', 'created_by': 'admin'},
        ]

        resp = client.get('/api/api-keys', headers=auth_headers('admin'))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert len(body['data']['api_keys']) == 1

    def test_regular_user_forbidden(self, client, auth_headers, _prime_regular_user):
        resp = client.get('/api/api-keys', headers=auth_headers('user'))
        assert resp.status_code == 403


class TestDeleteApiKey:
    def test_admin_can_delete(self, client, auth_headers, blueprint_app):
        blueprint_app.api_key_service.delete_key.return_value = True
        resp = client.delete('/api/api-keys/key-uuid', headers=auth_headers('admin'))
        assert resp.status_code == 200

    def test_not_found_returns_404(self, client, auth_headers, blueprint_app):
        blueprint_app.api_key_service.delete_key.return_value = False
        resp = client.delete('/api/api-keys/key-uuid', headers=auth_headers('admin'))
        assert resp.status_code == 404


class TestUpdateApiKey:
    def test_admin_can_update_name(self, client, auth_headers, blueprint_app):
        blueprint_app.api_key_service.update_key.return_value = {
            'id': 'key-uuid', 'name': 'Renamed', 'key_prefix': 'aa_abc12',
            'expires_at': datetime(2027, 1, 1, tzinfo=timezone.utc),
            'is_active': True, 'last_used_at': None, 'last_used_ip': None,
            'created_at': datetime(2026, 4, 2, tzinfo=timezone.utc),
            'user_id': 'u1', 'created_by': 'admin',
        }
        resp = client.patch(
            '/api/api-keys/key-uuid',
            json={'name': 'Renamed'},
            headers=auth_headers('admin'),
        )
        assert resp.status_code == 200

    def test_not_found_returns_404(self, client, auth_headers, blueprint_app):
        blueprint_app.api_key_service.update_key.return_value = None
        resp = client.patch(
            '/api/api-keys/key-uuid',
            json={'name': 'X'},
            headers=auth_headers('admin'),
        )
        assert resp.status_code == 404
