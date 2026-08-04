# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Incremental Embedding Update Integration Tests

Tests the pipeline integration scenarios for embedding updates.
These tests use mocked services (no real DB/LLM needed).
"""

import pytest
from unittest.mock import MagicMock, patch


class TestIncrementalUpdateScenarios:
    """Test embedding update under various pipeline conditions."""

    def _make_service(self, new_fields=None, llm_available=True, chromadb_available=True):
        """Create a configured EmbeddingIntegrationService with mocks."""
        from src.modules.asset_generation.services.embedding_integration import (
            EmbeddingIntegrationService,
        )
        svc = EmbeddingIntegrationService.__new__(EmbeddingIntegrationService)
        svc.db = MagicMock()
        svc.db.execute_query.return_value = new_fields or []
        svc.field_registry = MagicMock()
        svc.field_registry.has_description.return_value = False
        svc.field_registry.get_field_by_name.return_value = MagicMock(
            field_name='test', description='test desc',
            to_embedding_document=lambda: 'test - test desc'
        )
        svc.field_registry.get_all_fields.return_value = []
        svc.field_registry.register_extended_field.return_value = MagicMock()

        svc.description_generator = MagicMock()
        svc.description_generator.is_available.return_value = llm_available
        svc.description_generator.generate_batch_single_call.return_value = (
            {f['field_name']: f'desc for {f["field_name"]}' for f in (new_fields or [])}
            if llm_available else {}
        )
        svc._injected_description_generator = svc.description_generator

        svc._synonym_generator = MagicMock()
        svc._heavy_loaded = True

        if chromadb_available:
            svc._chromadb_store = MagicMock()
            svc._embedding_service = MagicMock()
            import numpy as np
            svc._embedding_service.embed_text.return_value = np.zeros(768)
        else:
            svc._chromadb_store = None
            svc._embedding_service = None

        return svc

    def test_no_new_fields_early_exit(self):
        svc = self._make_service(new_fields=[])
        result = svc.run_incremental_update()
        assert result['skipped'] is True
        assert result['new_fields'] == 0
        svc.description_generator.generate_batch_single_call.assert_not_called()
        # No synonym update when there are no new fields
        svc._synonym_generator.update_lookup_table_for_fields.assert_not_called()

    def test_new_field_with_llm_available(self):
        svc = self._make_service(
            new_fields=[{'field_name': 'new_field', 'data_source': 'test', 'sample_values': []}],
            llm_available=True,
        )
        result = svc.run_incremental_update()
        assert result['new_fields'] == 1
        assert result['descriptions_generated'] == 1
        svc.field_registry.register_extended_field.assert_called_once()
        # Incremental synonym update for just the new fields
        svc._synonym_generator.update_lookup_table_for_fields.assert_called_once_with(
            ['new_field'], use_llm=True,
        )

    def test_new_fields_with_llm_down_uses_fallback(self):
        svc = self._make_service(
            new_fields=[{'field_name': 'new_field', 'data_source': 'intune', 'sample_values': []}],
            llm_available=False,
        )
        result = svc.run_incremental_update()
        assert result['new_fields'] == 1
        assert result['descriptions_fallback'] == 1
        call_kwargs = svc.field_registry.register_extended_field.call_args
        desc = call_kwargs.kwargs.get('description', '') or call_kwargs[1].get('description', '')
        assert 'intune' in desc.lower()

    def test_chromadb_unavailable_continues(self):
        svc = self._make_service(
            new_fields=[{'field_name': 'f1', 'data_source': 'test', 'sample_values': []}],
            chromadb_available=False,
        )
        result = svc.run_incremental_update()
        assert result['new_fields'] == 1
        assert result['embeddings_added'] == 0

    def test_field_already_has_description_skips_llm(self):
        svc = self._make_service(
            new_fields=[{'field_name': 'f1', 'data_source': 'test', 'sample_values': []}],
        )
        svc.field_registry.has_description.return_value = True
        result = svc.run_incremental_update()
        # When all fields already have descriptions, batch call is either
        # not called or called with an empty list
        if svc.description_generator.generate_batch_single_call.called:
            call_args = svc.description_generator.generate_batch_single_call.call_args
            assert len(call_args[0][0]) == 0

    def test_multiple_new_fields(self):
        fields = [
            {'field_name': f'field_{i}', 'data_source': 'test', 'sample_values': []}
            for i in range(5)
        ]
        svc = self._make_service(new_fields=fields)
        result = svc.run_incremental_update()
        assert result['new_fields'] == 5

    def test_exception_in_synonym_gen_is_non_fatal(self):
        svc = self._make_service(
            new_fields=[{'field_name': 'f1', 'data_source': 'test', 'sample_values': []}],
        )
        svc._synonym_generator.update_lookup_table_for_fields.side_effect = RuntimeError('boom')
        result = svc.run_incremental_update()
        assert result['new_fields'] == 1
        assert len(result['errors']) > 0
        assert 'synonyms' in result['errors'][0]
