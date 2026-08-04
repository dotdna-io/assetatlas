# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for GET /api/settings/security/effective.

Returns the resolved public_base_url along with the source the resolver
picked (DB setting, env-var fallback, or neither). The Settings UI uses
this to pre-fill the input box so the operator sees the install's
bootstrap default rather than a blank field.
"""
import pytest


def _stub_settings_manager(blueprint_app, *, db_value: str):
    def _get(category, key, default=None):
        if (category, key) == ('security', 'public_base_url'):
            return db_value
        return default
    blueprint_app.settings_manager.get = _get


class TestEffectiveAuth:
    def test_no_auth_returns_401(self, client):
        resp = client.get('/api/settings/security/effective')
        assert resp.status_code == 401

    def test_user_role_returns_403(self, client, authed_headers):
        resp = client.get(
            '/api/settings/security/effective',
            headers=authed_headers('user'),
        )
        assert resp.status_code == 403


class TestEffectivePublicBaseUrl:
    def test_db_value_wins(self, client, authed_headers, blueprint_app, monkeypatch):
        monkeypatch.setenv('ASSETATLAS_PUBLIC_BASE_URL', 'https://env.example.com')
        _stub_settings_manager(blueprint_app, db_value='https://db.example.com')
        resp = client.get(
            '/api/settings/security/effective',
            headers=authed_headers('admin'),
        )
        assert resp.status_code == 200
        body = resp.get_json()['data']
        assert body['public_base_url'] == {
            'value': 'https://db.example.com',
            'source': 'setting',
        }

    def test_falls_back_to_env(self, client, authed_headers, blueprint_app, monkeypatch):
        monkeypatch.setenv('ASSETATLAS_PUBLIC_BASE_URL', 'https://env.example.com')
        _stub_settings_manager(blueprint_app, db_value='')
        resp = client.get(
            '/api/settings/security/effective',
            headers=authed_headers('admin'),
        )
        assert resp.status_code == 200
        body = resp.get_json()['data']
        assert body['public_base_url'] == {
            'value': 'https://env.example.com',
            'source': 'env',
        }

    def test_returns_none_when_neither_configured(
        self, client, authed_headers, blueprint_app, monkeypatch,
    ):
        monkeypatch.delenv('ASSETATLAS_PUBLIC_BASE_URL', raising=False)
        _stub_settings_manager(blueprint_app, db_value='')
        resp = client.get(
            '/api/settings/security/effective',
            headers=authed_headers('admin'),
        )
        assert resp.status_code == 200
        body = resp.get_json()['data']
        assert body['public_base_url'] == {'value': '', 'source': 'none'}

    def test_strips_trailing_slash_from_resolved_value(
        self, client, authed_headers, blueprint_app, monkeypatch,
    ):
        monkeypatch.delenv('ASSETATLAS_PUBLIC_BASE_URL', raising=False)
        _stub_settings_manager(blueprint_app, db_value='https://db.example.com/')
        resp = client.get(
            '/api/settings/security/effective',
            headers=authed_headers('admin'),
        )
        body = resp.get_json()['data']
        assert body['public_base_url']['value'] == 'https://db.example.com'
