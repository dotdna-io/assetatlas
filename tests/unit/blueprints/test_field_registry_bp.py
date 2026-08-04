# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for field registry settings endpoints."""
from types import SimpleNamespace
from unittest.mock import MagicMock
from datetime import datetime


def _make_field_dict(**overrides):
    base = {
        'id': 1, 'field_name': 'hostname', 'field_type': 'static',
        'db_path': 'hostname', 'display_name': 'Hostname',
        'description': 'hostname - computer name.', 'description_source': 'hardcoded',
        'data_source': None, 'searchable': True,
        'created_at': datetime.now(), 'updated_at': datetime.now(),
    }
    base.update(overrides)
    return base


class TestFieldRegistryList:
    """GET /api/settings/field-registry"""

    def test_list_fields(self, client, auth_headers, blueprint_app):
        from src.services.embedding.field_registry import FieldDefinition
        mock_fields = [FieldDefinition.from_dict(_make_field_dict())]
        blueprint_app.field_registry_service = MagicMock()
        blueprint_app.field_registry_service.get_all_fields.return_value = mock_fields
        blueprint_app.embedding_integration_service = MagicMock()
        blueprint_app.embedding_integration_service \
            .get_synonyms_by_api_field.return_value = {}
        response = client.get(
            '/api/settings/field-registry',
            headers=auth_headers('admin'),
        )
        assert response.status_code == 200
        data = response.get_json()
        assert len(data['data']['fields']) == 1

    def test_list_requires_admin(self, client, authed_headers):
        response = client.get(
            '/api/settings/field-registry',
            headers=authed_headers('user'),
        )
        assert response.status_code == 403


class TestFieldRegistrySynonyms:
    """GET /api/settings/field-registry — per-field synonyms."""

    def test_list_attaches_synonyms_matching_api_name(
            self, client, auth_headers, blueprint_app):
        from src.services.embedding.field_registry import FieldDefinition
        static_f = FieldDefinition.from_dict(_make_field_dict(
            field_name='hostname', field_type='static', data_source=None))
        ext_f = FieldDefinition.from_dict(_make_field_dict(
            id=2, field_name='intune_full_effectiveEncryption_s',
            field_type='extended', data_source='Intune Full',
            display_name='Encryption', description='disk encryption state'))
        blueprint_app.field_registry_service = MagicMock()
        blueprint_app.field_registry_service.get_all_fields.return_value = [
            static_f, ext_f]
        blueprint_app.embedding_integration_service = MagicMock()
        blueprint_app.embedding_integration_service \
            .get_synonyms_by_api_field.return_value = {
                'hostname': ['computer name', 'host name'],
                # NOTE: double underscore — the extended field's API name
                'intune_full__effectiveEncryption_s': [
                    'bitlocker', 'disk encryption'],
            }

        resp = client.get(
            '/api/settings/field-registry', headers=auth_headers('admin'))
        assert resp.status_code == 200
        fields = {f['field_name']: f for f in resp.get_json()['data']['fields']}
        assert fields['hostname']['synonyms'] == ['computer name', 'host name']
        # Extended field resolves via convert_field_name_to_api_format():
        assert fields['intune_full_effectiveEncryption_s']['synonyms'] == [
            'bitlocker', 'disk encryption']

    def test_list_synonyms_empty_when_service_absent(
            self, client, auth_headers, blueprint_app, monkeypatch):
        from src.services.embedding.field_registry import FieldDefinition
        blueprint_app.field_registry_service = MagicMock()
        blueprint_app.field_registry_service.get_all_fields.return_value = [
            FieldDefinition.from_dict(_make_field_dict())]
        # monkeypatch (not a bare `= None`) so the session-scoped blueprint_app
        # fixture is restored after this test. The per-test reset only
        # re-resets MagicMocks (conftest.py:163-166); it cannot restore a None,
        # so a bare assignment would leak into every later test that needs the
        # service (e.g. TestSynonymsRegen would then 503).
        monkeypatch.setattr(
            blueprint_app, 'embedding_integration_service', None)

        resp = client.get(
            '/api/settings/field-registry', headers=auth_headers('admin'))
        assert resp.status_code == 200
        assert resp.get_json()['data']['fields'][0]['synonyms'] == []


class TestFieldRegistryUpdate:
    """PUT /api/settings/field-registry/<field_name>"""

    def test_update_description(self, client, auth_headers, blueprint_app):
        from src.services.embedding.field_registry import FieldDefinition
        updated = FieldDefinition.from_dict(_make_field_dict(
            description='Updated description', description_source='manual'
        ))
        blueprint_app.field_registry_service = MagicMock()
        blueprint_app.field_registry_service.update_description.return_value = updated
        blueprint_app.field_registry_service.get_field_by_name.return_value = updated

        response = client.put(
            '/api/settings/field-registry/hostname',
            json={'description': 'Updated description', 'display_name': 'Hostname'},
            headers=auth_headers('admin'),
        )
        assert response.status_code == 200

    def test_update_rejects_empty_description(self, client, auth_headers, blueprint_app):
        blueprint_app.field_registry_service = MagicMock()
        response = client.put(
            '/api/settings/field-registry/hostname',
            json={'description': '', 'display_name': 'Hostname'},
            headers=auth_headers('admin'),
        )
        assert response.status_code == 400


class TestFieldRegistryRegenerate:
    """POST /api/settings/field-registry/<field>/regenerate (description + synonyms)"""

    def _patch_generator(self, monkeypatch, *, available, success=True):
        from src.services.embedding.description_generator import GeneratedDescription
        gen = MagicMock()
        gen.is_available.return_value = available
        gen.generate_description.return_value = GeneratedDescription(
            field_name='beszel_cores',
            description='beszel_cores - CPU core count.', success=success,
            error=None if success else 'boom')
        # The route imports the helper function-locally, so patch it at the
        # SOURCE module (that's where the import resolves at call time).
        monkeypatch.setattr(
            "src.services.embedding.pipeline_llm.build_pipeline_description_generator",
            lambda db, svc: gen,
        )
        return gen

    def _extended_field(self, **overrides):
        base = dict(field_type='extended', data_source='Beszel',
                    description='beszel_cores - CPU core count.',
                    description_source='llm')
        base.update(overrides)
        return SimpleNamespace(**base)

    @staticmethod
    def _not_running(monkeypatch):
        # The route 409s if a full "Regenerate All" is in progress; make sure
        # the shared flag is clear (and restored) regardless of test ordering.
        import src.api.blueprints.settings.field_registry_routes as mod
        monkeypatch.setitem(mod._synonym_status, 'running', False)

    def test_regenerate_calls_llm_and_synonyms(
            self, client, auth_headers, blueprint_app, monkeypatch):
        self._not_running(monkeypatch)
        field = self._extended_field()
        blueprint_app.field_registry_service = MagicMock()
        blueprint_app.field_registry_service.get_field_by_name.return_value = field
        self._patch_generator(monkeypatch, available=True)

        svc = MagicMock()
        # api name for beszel_cores + "Beszel" is the double-underscore form
        svc.get_synonyms_by_api_field.return_value = {
            'beszel__cores': ['cpu cores', 'cores', 'processor count']}
        blueprint_app.embedding_integration_service = svc

        resp = client.post(
            '/api/settings/field-registry/beszel_cores/regenerate',
            headers=auth_headers('admin'))
        assert resp.status_code == 200
        data = resp.get_json()['data']
        assert data['description_source'] == 'llm'
        assert data['synonyms'] == ['cpu cores', 'cores', 'processor count']
        # Both description and synonyms were regenerated.
        blueprint_app.field_registry_service.update_description \
            .assert_called_once_with(
                'beszel_cores', 'beszel_cores - CPU core count.', 'llm')
        svc.regenerate_field_synonyms.assert_called_once_with('beszel_cores')

    def test_regenerate_rejects_core_field(
            self, client, auth_headers, blueprint_app, monkeypatch):
        # A static/core field must never be regenerated (400), and the LLM must
        # not even be consulted.
        blueprint_app.field_registry_service = MagicMock()
        blueprint_app.field_registry_service.get_field_by_name.return_value = \
            SimpleNamespace(field_type='static', data_source=None)
        gen = self._patch_generator(monkeypatch, available=True)

        resp = client.post(
            '/api/settings/field-registry/hostname/regenerate',
            headers=auth_headers('admin'))
        assert resp.status_code == 400
        gen.generate_description.assert_not_called()

    def test_regenerate_fails_when_llm_unavailable(
            self, client, auth_headers, blueprint_app, monkeypatch):
        self._not_running(monkeypatch)
        blueprint_app.field_registry_service = MagicMock()
        blueprint_app.field_registry_service.get_field_by_name.return_value = \
            self._extended_field()
        self._patch_generator(monkeypatch, available=False)

        resp = client.post(
            '/api/settings/field-registry/beszel_cores/regenerate',
            headers=auth_headers('admin'))
        assert resp.status_code == 503

    def test_regenerate_rejects_while_full_regen_running(
            self, client, auth_headers, blueprint_app, monkeypatch):
        # Per-field regen must refuse (409) while a full "Regenerate All" is in
        # progress, rather than race it on the shared synonym lookup file.
        import src.api.blueprints.settings.field_registry_routes as mod
        blueprint_app.field_registry_service = MagicMock()
        blueprint_app.field_registry_service.get_field_by_name.return_value = \
            self._extended_field()
        gen = self._patch_generator(monkeypatch, available=True)
        monkeypatch.setitem(mod._synonym_status, 'running', True)

        resp = client.post(
            '/api/settings/field-registry/beszel_cores/regenerate',
            headers=auth_headers('admin'))
        assert resp.status_code == 409
        gen.generate_description.assert_not_called()


def test_get_field_descriptions_config(blueprint_app, auth_headers, monkeypatch):
    app = blueprint_app
    monkeypatch.setattr(app, "db_adapter", object())
    monkeypatch.setattr(
        app, "chat_providers_service",
        SimpleNamespace(get_active_provider=lambda: "anthropic"),
    )
    monkeypatch.setattr(app.settings_manager, "get", lambda cat, key, default=None: True)

    class _Gen:
        def is_available(self): return True
    # The route imports the helper function-locally, so patch it at the SOURCE
    # module (that's where `from ... import build_pipeline_description_generator`
    # resolves at call time).
    monkeypatch.setattr(
        "src.services.embedding.pipeline_llm.build_pipeline_description_generator",
        lambda db, svc: _Gen(),
    )
    resp = app.test_client().get(
        "/api/settings/field-descriptions", headers=auth_headers("admin"))
    assert resp.status_code == 200
    assert resp.get_json()["data"] == {
        "enabled": True, "provider_available": True, "active_provider": "anthropic"}


def test_put_field_descriptions_enabled(blueprint_app, auth_headers, monkeypatch):
    app = blueprint_app
    calls = {}
    def _set(cat, key, value, **kw):
        calls["set"] = (cat, key, value); return True, None
    monkeypatch.setattr(app.settings_manager, "set", _set)
    resp = app.test_client().put(
        "/api/settings/field-descriptions", json={"enabled": True},
        headers=auth_headers("admin"))
    assert resp.status_code == 200
    assert calls["set"] == ("embedding", "llm_descriptions_enabled", True)


class TestSynonymsRegen:
    """POST /api/settings/synonyms/regenerate (async) + GET /synonyms/status"""

    def test_regenerate_starts_background_job(self, client, auth_headers, monkeypatch):
        import src.api.blueprints.settings.field_registry_routes as mod
        mod._synonym_status['running'] = False
        spawned = {}

        class _FakeThread:
            def __init__(self, target, args, daemon):
                spawned['target'] = target
                spawned['args'] = args

            def start(self):
                spawned['started'] = True

        monkeypatch.setattr(mod.threading, 'Thread', _FakeThread)
        try:
            resp = client.post(
                '/api/settings/synonyms/regenerate', headers=auth_headers('admin'))
            assert resp.status_code == 200
            assert resp.get_json()['data']['status'] == 'started'
            assert spawned['started'] is True
        finally:
            # The fake thread never runs the finally-block that clears the flag.
            mod._synonym_status['running'] = False

    def test_regenerate_rejects_when_already_running(self, client, auth_headers):
        import src.api.blueprints.settings.field_registry_routes as mod
        mod._synonym_status['running'] = True
        try:
            resp = client.post(
                '/api/settings/synonyms/regenerate', headers=auth_headers('admin'))
            assert resp.status_code == 200
            assert resp.get_json()['data']['status'] == 'already_running'
        finally:
            mod._synonym_status['running'] = False

    def test_status_endpoint_reports_state(self, client, auth_headers):
        import src.api.blueprints.settings.field_registry_routes as mod
        mod._synonym_status['running'] = False
        mod._synonym_status['last_result'] = {'ok': True, 'message': 'done'}
        resp = client.get(
            '/api/settings/synonyms/status', headers=auth_headers('admin'))
        assert resp.status_code == 200
        assert resp.get_json()['data'] == {
            'running': False, 'last_result': {'ok': True, 'message': 'done'}}

    def test_background_runner_records_success(self):
        import src.api.blueprints.settings.field_registry_routes as mod
        from contextlib import contextmanager
        ran = {}

        class _Svc:
            def run_full_refresh(self):
                ran['called'] = True
                return {'mode': 'full', 'errors': []}

        class _App:
            embedding_integration_service = _Svc()

            @contextmanager
            def app_context(self):
                yield

        mod._synonym_status['running'] = True
        mod._run_synonyms_regen_in_background(_App())
        assert ran['called'] is True
        assert mod._synonym_status['running'] is False
        assert mod._synonym_status['last_result'] == {
            'ok': True, 'message': 'Descriptions and synonyms regenerated'}

    def test_background_runner_surfaces_partial_errors(self):
        # A non-fatal error (e.g. the synonym step failed) must surface via the
        # `error` key the UI inspects — NOT as a green success toast.
        import src.api.blueprints.settings.field_registry_routes as mod
        from contextlib import contextmanager

        class _Svc:
            def run_full_refresh(self):
                return {'mode': 'full', 'errors': ['synonyms: boom']}

        class _App:
            embedding_integration_service = _Svc()

            @contextmanager
            def app_context(self):
                yield

        mod._synonym_status['running'] = True
        mod._run_synonyms_regen_in_background(_App())
        res = mod._synonym_status['last_result']
        assert 'ok' not in res
        assert 'boom' in res['error']

    def test_background_runner_records_error(self):
        import src.api.blueprints.settings.field_registry_routes as mod
        from contextlib import contextmanager

        class _Svc:
            def run_full_refresh(self):
                raise RuntimeError('boom')

        class _App:
            embedding_integration_service = _Svc()

            @contextmanager
            def app_context(self):
                yield

        mod._synonym_status['running'] = True
        mod._run_synonyms_regen_in_background(_App())
        assert mod._synonym_status['running'] is False
        assert 'boom' in mod._synonym_status['last_result']['error']

    def test_regenerate_resets_running_when_thread_spawn_fails(self, client, auth_headers, monkeypatch):
        import src.api.blueprints.settings.field_registry_routes as mod
        mod._synonym_status['running'] = False

        class _BoomThread:
            def __init__(self, target, args, daemon):
                pass

            def start(self):
                raise RuntimeError('cannot spawn')

        monkeypatch.setattr(mod.threading, 'Thread', _BoomThread)
        try:
            resp = client.post(
                '/api/settings/synonyms/regenerate', headers=auth_headers('admin'))
            assert resp.status_code == 500
            # A spawn failure must not leave the job wedged 'running' forever.
            assert mod._synonym_status['running'] is False
        finally:
            mod._synonym_status['running'] = False

    def test_background_runner_resets_running_when_context_entry_fails(self):
        import src.api.blueprints.settings.field_registry_routes as mod

        class _Ctx:
            def __enter__(self):
                raise RuntimeError('no context')

            def __exit__(self, *a):
                return False

        class _App:
            def app_context(self):
                return _Ctx()

        mod._synonym_status['running'] = True
        mod._run_synonyms_regen_in_background(_App())
        assert mod._synonym_status['running'] is False
        assert 'no context' in mod._synonym_status['last_result']['error']
