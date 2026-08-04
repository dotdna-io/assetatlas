# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for incremental embedding integration."""
import json
import threading
from unittest.mock import MagicMock


class TestFieldRegistryExtensions:
    """Test new field registry methods."""

    def test_has_description_returns_true_when_description_exists(self):
        from src.services.embedding.field_registry import FieldRegistry
        registry = FieldRegistry.__new__(FieldRegistry)
        registry._execute_query = MagicMock(return_value=[{'exists': True}])
        assert registry.has_description('hostname') is True

    def test_has_description_returns_false_when_no_description(self):
        from src.services.embedding.field_registry import FieldRegistry
        registry = FieldRegistry.__new__(FieldRegistry)
        registry._execute_query = MagicMock(return_value=[{'exists': False}])
        assert registry.has_description('new_field') is False

    def test_get_fields_needing_descriptions(self):
        from src.services.embedding.field_registry import FieldRegistry
        registry = FieldRegistry.__new__(FieldRegistry)
        registry._execute_query = MagicMock(return_value=[
            {'field_name': 'field_a', 'data_source': 'crowdstrike'},
            {'field_name': 'field_b', 'data_source': 'intune'},
        ])
        result = registry.get_fields_needing_descriptions()
        assert len(result) == 2
        assert result[0]['field_name'] == 'field_a'


class TestBatchDescriptionGeneration:
    """Test single-call batch description generation."""

    def test_generate_batch_single_call_returns_dict(self):
        from src.services.embedding.description_generator import DescriptionGenerator
        gen = DescriptionGenerator.__new__(DescriptionGenerator)
        gen.llm_client = MagicMock()
        gen.llm_client.generate.return_value = (
            "field_a - Description for field A.\n"
            "field_b - Description for field B."
        )
        result = gen.generate_batch_single_call([
            {'field_name': 'field_a', 'data_source': 'crowdstrike'},
            {'field_name': 'field_b', 'data_source': 'intune'},
        ])
        assert isinstance(result, dict)
        assert 'field_a' in result
        assert 'field_b' in result
        assert gen.llm_client.generate.call_count == 1

    def test_generate_batch_single_call_empty_list(self):
        from src.services.embedding.description_generator import DescriptionGenerator
        gen = DescriptionGenerator.__new__(DescriptionGenerator)
        gen.llm_client = MagicMock()
        result = gen.generate_batch_single_call([])
        assert result == {}
        gen.llm_client.generate.assert_not_called()

    def test_generate_batch_single_call_llm_unavailable_returns_empty(self):
        from src.services.embedding.description_generator import DescriptionGenerator
        from src.services.embedding.pipeline_llm import LLMClientError
        gen = DescriptionGenerator.__new__(DescriptionGenerator)
        gen.llm_client = MagicMock()
        gen.llm_client.generate.side_effect = LLMClientError("unavailable")
        result = gen.generate_batch_single_call([
            {'field_name': 'field_a', 'data_source': 'test'},
        ])
        assert result == {}


class TestSynonymRebuildIntegration:
    """Test that synonym rebuild is triggered during embedding updates."""

    def test_incremental_synonym_update_called_on_new_fields(self):
        # New fields → an INCREMENTAL synonym update for just those fields
        # (not a full save_lookup_table rebuild of the whole population).
        from src.modules.asset_generation.services.embedding_integration import (
            EmbeddingIntegrationService,
        )
        svc = EmbeddingIntegrationService.__new__(EmbeddingIntegrationService)
        svc.db = MagicMock()
        svc.db.execute_query.return_value = [
            {'field_name': 'test_field', 'data_source': 'Test'},
        ]
        svc.field_registry = MagicMock()
        svc.field_registry.has_description.return_value = False
        svc.description_generator = MagicMock()
        svc.description_generator.is_available.return_value = False
        svc._injected_description_generator = svc.description_generator
        svc.field_registry.register_extended_field.return_value = MagicMock()
        svc._chromadb_store = None
        svc._embedding_service = None
        svc._synonym_generator = MagicMock()
        svc._heavy_loaded = True

        svc.run_incremental_update()

        svc._synonym_generator.update_lookup_table_for_fields \
            .assert_called_once()
        svc._synonym_generator.save_lookup_table.assert_not_called()

    def test_no_synonym_work_when_no_new_fields(self):
        # No new fields → the whole stage short-circuits; neither the
        # incremental update nor a full rebuild runs.
        from src.modules.asset_generation.services.embedding_integration import (
            EmbeddingIntegrationService,
        )
        svc = EmbeddingIntegrationService.__new__(EmbeddingIntegrationService)
        svc.db = MagicMock()
        svc.db.execute_query.return_value = []  # no new fields
        svc.field_registry = MagicMock()
        svc._injected_description_generator = MagicMock()
        svc._synonym_generator = MagicMock()
        svc._heavy_loaded = True

        result = svc.run_incremental_update()

        assert result['skipped'] is True
        svc._synonym_generator.update_lookup_table_for_fields \
            .assert_not_called()
        svc._synonym_generator.save_lookup_table.assert_not_called()


class TestEmbeddingIntegrationService:
    """Test the incremental embedding orchestrator."""

    def test_no_new_fields_skips_everything(self):
        from src.modules.asset_generation.services.embedding_integration import (
            EmbeddingIntegrationService,
        )
        svc = EmbeddingIntegrationService.__new__(EmbeddingIntegrationService)
        svc.db = MagicMock()
        svc.db.execute_query.return_value = []
        svc.field_registry = MagicMock()
        svc.field_registry.get_all_fields.return_value = []
        svc._injected_description_generator = MagicMock()
        svc._synonym_generator = None

        result = svc.run_incremental_update()
        assert result['new_fields'] == 0
        assert result['skipped'] is True

    def test_new_fields_detected_and_processed(self):
        from src.modules.asset_generation.services.embedding_integration import (
            EmbeddingIntegrationService,
        )
        svc = EmbeddingIntegrationService.__new__(EmbeddingIntegrationService)
        svc.db = MagicMock()
        svc.db.execute_query.return_value = [
            {'field_name': 'new_field_1', 'data_source': 'crowdstrike', 'sample_values': ['val1']},
        ]
        svc.field_registry = MagicMock()
        svc.field_registry.has_description.return_value = False
        svc.description_generator = MagicMock()
        svc.description_generator.generate_batch_single_call.return_value = {
            'new_field_1': 'new_field_1 - A test field.'
        }
        svc.description_generator.is_available.return_value = True
        svc._injected_description_generator = svc.description_generator
        svc.field_registry.register_extended_field.return_value = MagicMock()
        svc._chromadb_store = None
        svc._synonym_generator = MagicMock()
        svc._synonym_generator.save_lookup_table.return_value = None
        svc._embedding_service = None
        svc._heavy_loaded = True

        result = svc.run_incremental_update()
        assert result['new_fields'] == 1
        assert result['descriptions_generated'] == 1
        svc.field_registry.register_extended_field.assert_called_once()

    def test_run_incremental_update_prunes_orphans(self):
        """run_incremental_update must call prune_orphaned_fields and surface
        the count, otherwise removed enrichment fields linger in the registry."""
        from src.modules.asset_generation.services.embedding_integration import (
            EmbeddingIntegrationService,
        )
        svc = EmbeddingIntegrationService.__new__(EmbeddingIntegrationService)
        svc.db = MagicMock()

        # First call (prune) returns two orphans; subsequent calls (detect new)
        # return [], so the function takes the no-new-fields branch.
        svc.db.execute_query.side_effect = [
            [{'field_name': 'gone_field_1'}, {'field_name': 'gone_field_2'}],
            [],
        ]
        svc.field_registry = MagicMock()
        svc._injected_description_generator = MagicMock()
        svc._chromadb_store = None
        svc._synonym_generator = MagicMock()
        svc._embedding_service = None
        svc._heavy_loaded = True

        result = svc.run_incremental_update()

        assert result['pruned_fields'] == 2
        deleted_names = [
            c.args[0] for c in svc.field_registry.delete_field.call_args_list
        ]
        assert sorted(deleted_names) == ['gone_field_1', 'gone_field_2']

    def test_llm_unavailable_uses_fallback_descriptions(self):
        from src.modules.asset_generation.services.embedding_integration import (
            EmbeddingIntegrationService,
        )
        svc = EmbeddingIntegrationService.__new__(EmbeddingIntegrationService)
        svc.db = MagicMock()
        svc.db.execute_query.return_value = [
            {'field_name': 'new_field', 'data_source': 'intune', 'sample_values': []},
        ]
        svc.field_registry = MagicMock()
        svc.field_registry.has_description.return_value = False
        svc.description_generator = MagicMock()
        svc.description_generator.is_available.return_value = False
        svc._injected_description_generator = svc.description_generator
        svc.field_registry.register_extended_field.return_value = MagicMock()
        svc._chromadb_store = None
        svc._synonym_generator = MagicMock()
        svc._synonym_generator.save_lookup_table.return_value = None
        svc._embedding_service = None
        svc._heavy_loaded = True

        result = svc.run_incremental_update()
        assert result['new_fields'] == 1
        assert result['descriptions_fallback'] == 1
        call_kwargs = svc.field_registry.register_extended_field.call_args
        desc = call_kwargs.kwargs.get('description', '') or call_kwargs[1].get('description', '')
        assert 'intune' in desc.lower()


class TestRunFullRefresh:
    """run_full_refresh — the 'Regenerate All' action: extended fields only,
    Core-safe, and it rescues hardcoded (fallback) descriptions."""

    def _svc(self):
        from src.modules.asset_generation.services.embedding_integration import (
            EmbeddingIntegrationService,
        )
        svc = EmbeddingIntegrationService.__new__(EmbeddingIntegrationService)
        svc.db = MagicMock()
        svc.field_registry = MagicMock()
        svc._synonym_generator = MagicMock()
        svc._chromadb_store = None
        svc._embedding_service = None
        svc._heavy_loaded = True
        svc._synonym_lock = threading.Lock()
        return svc

    def test_regenerates_all_non_manual_descriptions(self, monkeypatch):
        from types import SimpleNamespace
        svc = self._svc()
        gen = MagicMock()
        gen.is_available.return_value = True
        gen.generate_batch_single_call.return_value = {}
        monkeypatch.setattr(svc, '_refresh_llm_client', lambda: gen)

        svc.field_registry.get_extended_fields.return_value = [
            SimpleNamespace(field_name='beszel_cores', data_source='Beszel',
                            description_source='hardcoded'),
            SimpleNamespace(field_name='intune_isEncrypted',
                            data_source='Intune', description_source='llm'),
            SimpleNamespace(field_name='ldap_attr', data_source='LDAP',
                            description_source=None),   # unset/NULL source
            SimpleNamespace(field_name='intune_note', data_source='Intune',
                            description_source='manual'),
        ]

        result = svc.run_full_refresh()

        # Every non-manual description (hardcoded, llm, AND unset/NULL) is
        # sent to the LLM; only the manual one is preserved.
        sent = gen.generate_batch_single_call.call_args.args[0]
        assert sorted(f['field_name'] for f in sent) == [
            'beszel_cores', 'intune_isEncrypted', 'ldap_attr']
        # Synonyms rebuilt for ALL extended fields, replace + seed_core
        # (Core-safe, complete-even-from-empty), after reloading cached fields.
        svc._synonym_generator.reload_fields.assert_called_once()
        call = svc._synonym_generator.update_lookup_table_for_fields.call_args
        assert sorted(call.args[0]) == [
            'beszel_cores', 'intune_isEncrypted', 'intune_note', 'ldap_attr']
        assert call.kwargs.get('replace') is True
        assert call.kwargs.get('use_llm') is True
        assert call.kwargs.get('seed_core') is True
        assert result['synonyms_regenerated'] is True

    def test_synonyms_not_reported_regenerated_when_field_load_fails(
            self, monkeypatch):
        # If the extended-field load raises, no synonym work runs — the result
        # must NOT claim synonyms_regenerated (avoids a false-success toast).
        svc = self._svc()
        gen = MagicMock()
        gen.is_available.return_value = True
        monkeypatch.setattr(svc, '_refresh_llm_client', lambda: gen)
        svc.field_registry.get_extended_fields.side_effect = RuntimeError('boom')

        result = svc.run_full_refresh()

        assert result['synonyms_regenerated'] is False
        assert any('boom' in e for e in result['errors'])
        svc._synonym_generator.update_lookup_table_for_fields \
            .assert_not_called()

    def test_regenerate_field_synonyms_replaces_single_field(self, monkeypatch):
        svc = self._svc()
        monkeypatch.setattr(svc, '_refresh_llm_client', lambda: MagicMock())

        svc.regenerate_field_synonyms('beszel_cores')

        svc._synonym_generator.reload_fields.assert_called_once()
        call = svc._synonym_generator.update_lookup_table_for_fields.call_args
        assert call.args[0] == ['beszel_cores']
        assert call.kwargs.get('replace') is True
        assert call.kwargs.get('use_llm') is True
        # Per-field regen must NOT seed Core (that's the full-refresh's job).
        assert call.kwargs.get('seed_core') is False


from src.modules.asset_generation.services.embedding_integration import (
    EmbeddingIntegrationService,
)


class _FakeGen:
    def __init__(self, available):
        self._a = available
        self.calls = []
        self.llm_client = object()
    def is_available(self): return self._a
    def generate_batch_single_call(self, fields):
        self.calls.append(fields); return {}


class _FakeRegistry:
    def has_description(self, name): return False
    def register_extended_field(self, **kw): pass


class _FakeSyn:
    def __init__(self): self.calls = []
    def update_lookup_table_for_fields(self, names, use_llm=True):
        self.calls.append(use_llm)


class _FakeDB:
    conn_manager = None


def _make_service(gen):
    svc = EmbeddingIntegrationService(
        _FakeDB(), field_registry=_FakeRegistry(), description_generator=gen)
    svc._heavy_loaded = True          # skip real ChromaDB / embedding model
    svc._synonym_generator = _FakeSyn()
    svc._chromadb_store = None
    svc._embedding_service = None
    return svc


def _stub_new_fields(monkeypatch, svc):
    monkeypatch.setattr(svc, "prune_orphaned_fields", lambda: [])
    monkeypatch.setattr(svc, "_detect_new_fields",
                        lambda: [{"field_name": "src_foo", "data_source": "src",
                                  "sample_values": []}])


def test_incremental_use_llm_false_skips_generation(monkeypatch):
    gen = _FakeGen(available=True)          # provider IS available...
    svc = _make_service(gen)
    _stub_new_fields(monkeypatch, svc)
    result = svc.run_incremental_update(use_llm=False)   # ...but toggle off
    assert gen.calls == []                                # never called the LLM
    assert result["descriptions_generated"] == 0
    assert result["descriptions_fallback"] == 1
    assert svc._synonym_generator.calls == [False]        # synonyms gated too


def test_incremental_use_llm_true_calls_generator(monkeypatch):
    gen = _FakeGen(available=True)
    svc = _make_service(gen)
    _stub_new_fields(monkeypatch, svc)
    svc.run_incremental_update(use_llm=True)
    assert len(gen.calls) == 1
    assert svc._synonym_generator.calls == [True]


def test_incremental_update_survives_refresh_failure(monkeypatch):
    """A provider-resolution failure must NOT abort the 'always non-fatal'
    stage — run_incremental_update guards _refresh_llm_client and degrades
    instead of propagating (mirrors the get_status hardening)."""
    gen = _FakeGen(available=True)
    svc = _make_service(gen)
    _stub_new_fields(monkeypatch, svc)

    def _boom():
        raise RuntimeError("provider resolution failed")
    monkeypatch.setattr(svc, "_refresh_llm_client", _boom)

    result = svc.run_incremental_update(use_llm=True)  # must not raise
    assert isinstance(result, dict)
    assert result["new_fields"] == 1


def test_get_status_degrades_when_refresh_fails():
    """get_status() must not raise when resolving the pipeline LLM client
    fails (e.g. chat-provider config unreachable) — the admin status page
    should still render gracefully instead of the route 500ing via @handle_errors."""
    svc = EmbeddingIntegrationService.__new__(EmbeddingIntegrationService)
    svc.db = MagicMock()
    svc.field_registry = MagicMock()
    svc.field_registry.get_all_fields.return_value = []
    svc.description_generator = None  # never successfully refreshed
    svc._chromadb_store = None
    svc._embedding_service = None
    svc._synonym_generator = MagicMock()
    svc._synonym_generator.config.synonym_output_path = "/nonexistent/lookup.json"
    svc._heavy_loaded = True

    def _boom():
        raise RuntimeError("provider config unreachable")
    svc._refresh_llm_client = _boom

    status = svc.get_status()  # must not raise
    # Verify status degrades gracefully: llm_server section reports unavailable
    assert status['llm_server']['available'] is False


def test_get_synonyms_by_api_field_inverts(tmp_path, monkeypatch):
    from src.services.embedding import config as cfg
    from src.modules.asset_generation.services.embedding_integration import (
        EmbeddingIntegrationService,
    )
    lookup = tmp_path / 'synonym_lookup.json'
    lookup.write_text(json.dumps({'synonyms': {
        'computer name': 'hostname',
        'host name': 'hostname',
        'bitlocker': 'intune_full__effectiveEncryption_s',
    }}))
    monkeypatch.setattr(cfg.default_config, 'synonym_output_path', str(lookup))

    svc = EmbeddingIntegrationService(
        db_adapter=MagicMock(), field_registry=MagicMock(),
        description_generator=MagicMock())
    result = svc.get_synonyms_by_api_field()

    # term order preserved; grouped by API-format field name
    assert result['hostname'] == ['computer name', 'host name']
    assert result['intune_full__effectiveEncryption_s'] == ['bitlocker']


def test_get_synonyms_by_api_field_missing_file(tmp_path, monkeypatch):
    from src.services.embedding import config as cfg
    from src.modules.asset_generation.services.embedding_integration import (
        EmbeddingIntegrationService,
    )
    monkeypatch.setattr(
        cfg.default_config, 'synonym_output_path', str(tmp_path / 'nope.json'))
    svc = EmbeddingIntegrationService(
        db_adapter=MagicMock(), field_registry=MagicMock(),
        description_generator=MagicMock())
    assert svc.get_synonyms_by_api_field() == {}


def test_get_synonyms_by_api_field_malformed_returns_empty(tmp_path, monkeypatch):
    from src.services.embedding import config as cfg
    from src.modules.asset_generation.services.embedding_integration import (
        EmbeddingIntegrationService,
    )
    svc = EmbeddingIntegrationService(
        db_adapter=MagicMock(), field_registry=MagicMock(),
        description_generator=MagicMock())
    lookup = tmp_path / 'synonym_lookup.json'
    monkeypatch.setattr(cfg.default_config, 'synonym_output_path', str(lookup))
    # Valid JSON but wrong shapes, plus non-JSON: all must degrade to {} (never raise).
    for payload in ('null', 'not json', '{"synonyms": ["a", "b"]}', '[]'):
        lookup.write_text(payload)
        assert svc.get_synonyms_by_api_field() == {}, f"payload {payload!r} should yield {{}}"
