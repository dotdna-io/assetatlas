# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the teams settings routes (custom GET, messaging endpoint, etc.)."""
from __future__ import annotations

from unittest.mock import patch


def _settings_with_public_url(blueprint_app, url: str, *, override: str = ''):
    """Stub settings_manager.get for security.public_base_url + teams.messaging_endpoint.

    Mirrors the resolution order used in production: the messaging endpoint
    is computed from security.public_base_url unless an override is set.
    Callers pass `override` to exercise the override path.
    """
    def _get(category, key, default=None):
        if (category, key) == ('security', 'public_base_url'):
            return url
        if (category, key) == ('teams', 'messaging_endpoint'):
            return override
        if (category, key) == ('teams', 'bot_app_id'):
            return ''
        return '' if default is None else default
    blueprint_app.settings_manager.get = _get


class TestTeamsGet:
    def test_get_redacts_password_when_set(self, blueprint_app, client, auth_headers):
        # Arrange: settings_manager.get_all returns ciphertext for the password.
        def _get_all(category=None):
            return {
                'teams': {
                    'enabled': {'value': True},
                    'bot_app_id': {'value': '11111111-1111-1111-1111-111111111111'},
                    'bot_app_password': {'value': 'ENC:abc123=='},
                    'bot_app_tenant_id': {'value': '22222222-2222-2222-2222-222222222222'},
                }
            }
        blueprint_app.settings_manager.get_all.side_effect = _get_all

        resp = client.get('/api/settings/teams', headers=auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        data = body.get('data', body)
        assert data['enabled'] is True
        assert data['bot_app_id'] == '11111111-1111-1111-1111-111111111111'
        assert data['bot_app_password'] == '__SET__'
        assert data['bot_app_tenant_id'] == '22222222-2222-2222-2222-222222222222'

    def test_get_redacts_password_when_empty(self, blueprint_app, client, auth_headers):
        def _get_all(category=None):
            return {
                'teams': {
                    'enabled': {'value': False},
                    'bot_app_id': {'value': ''},
                    'bot_app_password': {'value': ''},
                    'bot_app_tenant_id': {'value': ''},
                }
            }
        blueprint_app.settings_manager.get_all.side_effect = _get_all

        resp = client.get('/api/settings/teams', headers=auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        data = body.get('data', body)
        assert data['bot_app_password'] == ''


class TestMessagingEndpoint:
    def test_https_public_url_returns_https_endpoint(
        self, blueprint_app, client, auth_headers
    ):
        _settings_with_public_url(blueprint_app, 'https://assetatlas.acme.com')
        resp = client.get(
            '/api/settings/teams/messaging-endpoint', headers=auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.get_json()['data']
        assert data['url'] == (
            'https://assetatlas.acme.com/teams/api/messages'
        )
        assert data['https_ok'] is True
        assert data['is_override'] is False

    def test_env_var_fallback_when_setting_empty(
        self, blueprint_app, client, auth_headers, monkeypatch,
    ):
        # security.public_base_url empty in DB -> resolver falls back to env var.
        _settings_with_public_url(blueprint_app, '')
        monkeypatch.setenv('ASSETATLAS_PUBLIC_BASE_URL', 'http://localhost:5001')
        resp = client.get(
            '/api/settings/teams/messaging-endpoint', headers=auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.get_json()['data']
        assert data['url'] == 'http://localhost:5001/teams/api/messages'
        assert data['https_ok'] is False
        assert data['is_override'] is False

    def test_trailing_slash_stripped(self, blueprint_app, client, auth_headers):
        _settings_with_public_url(blueprint_app, 'https://assetatlas.acme.com/')
        resp = client.get(
            '/api/settings/teams/messaging-endpoint', headers=auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.get_json()['data']
        assert data['url'] == (
            'https://assetatlas.acme.com/teams/api/messages'
        )
        assert data['is_override'] is False

    def test_empty_public_url_returns_400(
        self, blueprint_app, client, auth_headers, monkeypatch,
    ):
        _settings_with_public_url(blueprint_app, '')
        monkeypatch.delenv('ASSETATLAS_PUBLIC_BASE_URL', raising=False)
        resp = client.get(
            '/api/settings/teams/messaging-endpoint', headers=auth_headers(),
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body.get('error') == 'validation_error'
        assert 'public_base_url' in body.get('message', '')

    def test_https_with_port(self, blueprint_app, client, auth_headers):
        # Covers deployments where the public URL exposes a non-standard HTTPS port.
        _settings_with_public_url(blueprint_app, 'https://192.0.2.1:8443')
        resp = client.get(
            '/api/settings/teams/messaging-endpoint', headers=auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.get_json()['data']
        assert data['url'] == 'https://192.0.2.1:8443/teams/api/messages'
        assert data['https_ok'] is True
        assert data['is_override'] is False

    def test_https_with_path_prefix(self, blueprint_app, client, auth_headers):
        # Mirrors path-based reverse-proxy deployments (e.g. /assetatlas).
        _settings_with_public_url(
            blueprint_app, 'https://shared.example.com/assetatlas',
        )
        resp = client.get(
            '/api/settings/teams/messaging-endpoint', headers=auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.get_json()['data']
        assert data['url'] == (
            'https://shared.example.com/assetatlas/teams/api/messages'
        )
        assert data['https_ok'] is True
        assert data['is_override'] is False

    def test_override_takes_precedence_over_public_url(
        self, blueprint_app, client, auth_headers,
    ):
        _settings_with_public_url(
            blueprint_app, 'https://internal.acme.com',
            override='https://external.acme.com/custom/path/messages',
        )
        resp = client.get(
            '/api/settings/teams/messaging-endpoint', headers=auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.get_json()['data']
        assert data['url'] == 'https://external.acme.com/custom/path/messages'
        assert data['https_ok'] is True
        assert data['is_override'] is True

    def test_empty_override_falls_back_to_computed(
        self, blueprint_app, client, auth_headers,
    ):
        _settings_with_public_url(blueprint_app, 'https://assetatlas.acme.com')
        resp = client.get(
            '/api/settings/teams/messaging-endpoint', headers=auth_headers(),
        )
        data = resp.get_json()['data']
        assert data['url'] == 'https://assetatlas.acme.com/teams/api/messages'
        assert data['is_override'] is False


class TestConnection:
    def test_uses_saved_creds_when_body_empty(
        self, blueprint_app, client, auth_headers,
    ):
        sm = blueprint_app.settings_manager
        def _saved_get(c, k):
            return {
                ('teams', 'bot_app_id'): 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
                ('teams', 'bot_app_password'): 'plain',
                ('teams', 'bot_app_tenant_id'): 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
            }[(c, k)]
        sm.get = _saved_get

        with patch(
            'src.api.blueprints.settings.teams_settings_routes._post_to_bridge',
            return_value={'ok': True},
        ) as mock_post:
            resp = client.post(
                '/api/settings/teams/test-connection',
                json={}, headers=auth_headers(),
            )
        assert resp.status_code == 200
        assert resp.get_json()['data'] == {'ok': True}
        # Bridge received the saved creds.
        sent = mock_post.call_args.args[0]
        assert sent['bot_app_id'].startswith('aaaa')
        assert sent['bot_app_password'] == 'plain'
        assert sent['bot_app_tenant_id'].startswith('bbbb')

    def test_overrides_with_body_when_provided(
        self, blueprint_app, client, auth_headers,
    ):
        sm = blueprint_app.settings_manager
        sm.get = lambda c, k: 'whatever'  # Saved values shouldn't be used.

        with patch(
            'src.api.blueprints.settings.teams_settings_routes._post_to_bridge',
            return_value={'ok': False, 'error': 'AADSTS7000215'},
        ) as mock_post:
            resp = client.post(
                '/api/settings/teams/test-connection',
                json={
                    'bot_app_id': 'cccccccc-cccc-cccc-cccc-cccccccccccc',
                    'bot_app_password': 'override',
                    'bot_app_tenant_id': 'dddddddd-dddd-dddd-dddd-dddddddddddd',
                },
                headers=auth_headers(),
            )
        assert resp.status_code == 200
        sent = mock_post.call_args.args[0]
        assert sent['bot_app_id'].startswith('cccc')
        assert sent['bot_app_password'] == 'override'
        assert sent['bot_app_tenant_id'].startswith('dddd')

    def test_partial_body_fills_missing_from_saved(
        self, blueprint_app, client, auth_headers,
    ):
        # Body provides id only; password and tenant fall back to saved.
        sm = blueprint_app.settings_manager
        def _saved_get(c, k):
            return {
                ('teams', 'bot_app_id'): 'saved-id',
                ('teams', 'bot_app_password'): 'saved-pw',
                ('teams', 'bot_app_tenant_id'): 'saved-tenant',
            }[(c, k)]
        sm.get = _saved_get

        with patch(
            'src.api.blueprints.settings.teams_settings_routes._post_to_bridge',
            return_value={'ok': True},
        ) as mock_post:
            client.post(
                '/api/settings/teams/test-connection',
                json={'bot_app_id': 'cccccccc-cccc-cccc-cccc-cccccccccccc'},
                headers=auth_headers(),
            )
        sent = mock_post.call_args.args[0]
        assert sent['bot_app_id'].startswith('cccc')
        assert sent['bot_app_password'] == 'saved-pw'
        assert sent['bot_app_tenant_id'] == 'saved-tenant'

    def test_bridge_unreachable_returns_ok_false(
        self, blueprint_app, client, auth_headers,
    ):
        import requests
        sm = blueprint_app.settings_manager
        sm.get = lambda c, k: 'x'

        with patch(
            'src.api.blueprints.settings.teams_settings_routes._post_to_bridge',
            side_effect=requests.ConnectionError('connect refused'),
        ):
            resp = client.post(
                '/api/settings/teams/test-connection',
                json={}, headers=auth_headers(),
            )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['data']['ok'] is False
        assert 'unreachable' in body['data']['error']

    def test_bridge_http_error_returns_bridge_error(
        self, blueprint_app, client, auth_headers,
    ):
        # The bridge is reachable (no ConnectionError) but returned non-2xx.
        # raise_for_status raises HTTPError, which is a RequestException
        # subclass. Our handler must distinguish this from "unreachable".
        import requests
        sm = blueprint_app.settings_manager
        sm.get = lambda c, k: 'x'

        with patch(
            'src.api.blueprints.settings.teams_settings_routes._post_to_bridge',
            side_effect=requests.HTTPError('500 Server Error'),
        ):
            resp = client.post(
                '/api/settings/teams/test-connection',
                json={}, headers=auth_headers(),
            )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['data']['ok'] is False
        assert body['data']['error'] == 'bridge error'


def _settings_for_manifest(blueprint_app, public_url: str, *, bot_app_id: str = ''):
    """Stub settings_manager.get for the manifest download route."""
    def _get(category, key, default=None):
        if (category, key) == ('security', 'public_base_url'):
            return public_url
        if (category, key) == ('teams', 'bot_app_id'):
            return bot_app_id
        return '' if default is None else default
    blueprint_app.settings_manager.get = _get


class TestManifestDownload:
    def test_returns_zip_with_correct_headers(
        self, blueprint_app, client, auth_headers,
    ):
        _settings_for_manifest(
            blueprint_app, 'https://assetatlas.acme.com',
            bot_app_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
        )

        resp = client.get(
            '/api/settings/teams/manifest.zip', headers=auth_headers(),
        )
        assert resp.status_code == 200
        assert resp.headers['Content-Type'] == 'application/zip'
        assert 'attachment' in resp.headers['Content-Disposition']
        assert 'assetatlas-teams.zip' in resp.headers['Content-Disposition']
        # Body is a real zip.
        import io, zipfile
        zf = zipfile.ZipFile(io.BytesIO(resp.data))
        assert set(zf.namelist()) == {'manifest.json', 'color.png', 'outline.png'}

    def test_returns_400_when_app_id_empty(
        self, blueprint_app, client, auth_headers,
    ):
        _settings_for_manifest(blueprint_app, 'https://assetatlas.acme.com')
        resp = client.get(
            '/api/settings/teams/manifest.zip', headers=auth_headers(),
        )
        assert resp.status_code == 400
        # Body is plain text, not JSON envelope (route bypasses wrapper).
        assert b'bot_app_id' in resp.data

    def test_returns_400_when_public_url_http(
        self, blueprint_app, client, auth_headers, monkeypatch,
    ):
        # Override resolver: setting empty, env var holds an http:// URL.
        _settings_for_manifest(
            blueprint_app, '',
            bot_app_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
        )
        monkeypatch.setenv('ASSETATLAS_PUBLIC_BASE_URL', 'http://localhost:5001')
        resp = client.get(
            '/api/settings/teams/manifest.zip', headers=auth_headers(),
        )
        assert resp.status_code == 400
        assert b'HTTPS' in resp.data

    def test_returns_500_text_when_template_unreadable(
        self, blueprint_app, client, auth_headers,
    ):
        # Simulate a misconfigured server where the manifest template file
        # is missing. The route must return plain text (per its contract),
        # not the JSON 500 envelope from Flask's default error handler.
        from unittest.mock import patch
        _settings_for_manifest(
            blueprint_app, 'https://assetatlas.acme.com',
            bot_app_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
        )

        with patch(
            'src.api.blueprints.settings.teams_settings_routes.'
            'build_teams_manifest_zip',
            side_effect=OSError('template missing'),
        ):
            resp = client.get(
                '/api/settings/teams/manifest.zip', headers=auth_headers(),
            )

        assert resp.status_code == 500
        assert resp.content_type == 'text/plain'
        assert b'OSError' in resp.data
