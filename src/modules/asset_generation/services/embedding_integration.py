# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Embedding Integration Service - orchestrates incremental embedding updates.

Detects new extended_attributes fields after pipeline runs, generates
descriptions, updates ChromaDB embeddings, and merges synonym mappings.
All operations are non-fatal - failures are logged but never block the pipeline.
"""

import logging
import threading
import time
from typing import Any

from src.database.postgres_adapter import PostgreSQLAdapter

logger = logging.getLogger(__name__)

_NEW_FIELDS_SQL = """
    WITH nested_keys AS (
        SELECT DISTINCT
            src.key AS data_source,
            lower(replace(src.key, ' ', '_')) || '_' || field.key AS field_name
        FROM assets,
            jsonb_each(COALESCE(extended_attributes, '{}'::jsonb)) AS src,
            jsonb_each(src.value) AS field
        WHERE extended_attributes IS NOT NULL
          AND jsonb_typeof(src.value) = 'object'
          AND src.key != 'AssetAtlas'
    )
    SELECT nk.field_name, nk.data_source
    FROM nested_keys nk
    LEFT JOIN field_registry fr ON nk.field_name = fr.field_name
    WHERE fr.id IS NULL
    ORDER BY nk.field_name
"""

_SAMPLE_VALUES_SQL = """
    SELECT DISTINCT (src.value->>%(raw_field)s) AS val
    FROM assets,
        jsonb_each(COALESCE(extended_attributes, '{}'::jsonb)) AS src
    WHERE jsonb_typeof(src.value) = 'object'
      AND src.value ? %(raw_field)s
      AND (src.value->>%(raw_field)s) IS NOT NULL
      AND (src.value->>%(raw_field)s) != ''
    LIMIT 5
"""

_ORPHANED_FIELDS_SQL = """
    WITH live_keys AS (
        SELECT DISTINCT
            lower(replace(src.key, ' ', '_')) || '_' || field.key AS field_name
        FROM assets,
            jsonb_each(COALESCE(extended_attributes, '{}'::jsonb)) AS src,
            jsonb_each(src.value) AS field
        WHERE extended_attributes IS NOT NULL
          AND jsonb_typeof(src.value) = 'object'
    )
    SELECT fr.field_name
    FROM field_registry fr
    LEFT JOIN live_keys lk ON fr.field_name = lk.field_name
    WHERE fr.field_type = 'extended'
    AND lk.field_name IS NULL
"""


class EmbeddingIntegrationService:
    """Orchestrates incremental embedding system updates."""

    def __init__(self, db_adapter: PostgreSQLAdapter, *,
                 field_registry=None, description_generator=None,
                 synonym_generator=None, chat_providers_service=None):
        self.db = db_adapter
        self._chat_providers_service = chat_providers_service
        self._injected_description_generator = description_generator

        from src.services.embedding.field_registry import FieldRegistry
        from src.services.embedding.description_generator import DescriptionGenerator

        self.field_registry = field_registry or FieldRegistry()
        # Non-None floor (NullPipelineLLM-backed, reports unavailable) so a
        # resolution failure or a cold instance can't None-deref; the real
        # client is resolved fresh per run via _refresh_llm_client().
        self.description_generator = description_generator or DescriptionGenerator()
        self._synonym_generator = synonym_generator
        self._chromadb_store = None
        self._embedding_service = None
        self._heavy_loaded = False
        # Serializes synonym regeneration within the process. The synonym
        # generator is a shared singleton and the lookup file is a single
        # read-modify-write, so a per-field Regen and the background
        # "Regenerate All" (or two per-field Regens) must not run their synonym
        # steps concurrently.
        self._synonym_lock = threading.Lock()

    def _ensure_heavy_components(self):
        """Lazy-load embedding model, ChromaDB, and synonym generator on first use."""
        if self._heavy_loaded:
            return
        self._heavy_loaded = True

        from src.services.embedding.synonym_generator import SynonymGenerator
        if not self._synonym_generator:
            llm_client = self.description_generator.llm_client if self.description_generator else None
            self._synonym_generator = SynonymGenerator(llm_client=llm_client)

        try:
            from src.services.embedding.chromadb_store import ChromaDBStore
            from src.services.embedding.embedding_service import EmbeddingService
            self._chromadb_store = ChromaDBStore()
            self._embedding_service = EmbeddingService()
        except Exception as e:
            logger.warning(f"ChromaDB/Embedding not available: {e}")

    def _refresh_llm_client(self):
        """Resolve a fresh pipeline description generator from current config.

        Returns the resolved generator so each caller uses a LOCAL reference for
        the whole operation. The service is a shared app-level singleton, so
        reading ``self.description_generator`` across an ``is_available()`` /
        ``generate()`` pair would be a TOCTOU race with a concurrent refresh
        (e.g. the admin background-refresh thread reassigning it mid-call). Also
        updates ``self.description_generator`` and re-points a loaded synonym
        generator so the status/synonym paths see the current provider. Heavy
        components (embedding model, ChromaDB) stay cached.
        """
        if self._injected_description_generator is not None:
            generator = self._injected_description_generator
        else:
            from src.services.embedding.pipeline_llm import (
                build_pipeline_description_generator,
            )
            generator = build_pipeline_description_generator(
                self.db, self._chat_providers_service,
            )
        self.description_generator = generator
        if self._synonym_generator is not None:
            from src.services.embedding.synonym_llm import LLMSynonymGenerator
            self._synonym_generator.llm_synonym_gen = LLMSynonymGenerator(
                generator.llm_client,
            )
        return generator

    @property
    def synonym_generator(self):
        self._ensure_heavy_components()
        return self._synonym_generator

    @property
    def chromadb_store(self):
        self._ensure_heavy_components()
        return self._chromadb_store

    @property
    def embedding_service(self):
        self._ensure_heavy_components()
        return self._embedding_service

    def prune_orphaned_fields(self) -> list[str]:
        """Remove extended fields from field_registry that no longer exist in any asset.

        Returns list of pruned field names.
        """
        try:
            rows = self.db.execute_query(_ORPHANED_FIELDS_SQL, {})
        except Exception as e:
            logger.warning(f"Failed to detect orphaned fields: {e}")
            return []

        if not rows:
            return []

        pruned = []
        for row in rows:
            name = row['field_name']
            try:
                self.field_registry.delete_field(name)
                pruned.append(name)
            except Exception as e:
                logger.warning(f"Failed to delete orphaned field '{name}': {e}")

        if pruned:
            logger.info(f"Pruned {len(pruned)} orphaned fields: {pruned}")

            # Remove from ChromaDB if available
            if self.chromadb_store:
                for name in pruned:
                    try:
                        self.chromadb_store.delete_field(name)
                    except Exception as e:
                        logger.warning(f"Failed to remove orphaned embedding '{name}': {e}")

        return pruned

    def run_incremental_update(self, use_llm: bool = True) -> dict[str, Any]:
        """Detect new fields, prune orphans, and incrementally update embeddings.

        ``use_llm`` gates automatic LLM description/synonym generation. The
        pipeline (stage 22) passes the opt-in setting; manual/admin callers use
        the default True (toggle-independent). Fallback + embeddings run either
        way, so semantic search never depends on the LLM.
        """
        try:
            generator = self._refresh_llm_client()
        except Exception as e:
            logger.error(
                f"Failed to resolve LLM client (using fallback descriptions): {e}"
            )
            generator = self.description_generator
        start = time.time()
        result = {
            'new_fields': 0,
            'pruned_fields': 0,
            'descriptions_generated': 0,
            'descriptions_fallback': 0,
            'embeddings_added': 0,
            'synonyms_rebuilt': False,
            'skipped': False,
            'errors': [],
        }

        # Prune first so stale entries don't survive removed enrichments. The
        # pipeline runs this stage last (s22) — by which point s16 has already
        # TRUNCATE+INSERTed the full asset set and every later stage has
        # written its data, so the orphaned-fields query reflects the final
        # state. Callers outside the pipeline (admin API, scheduled job) hit
        # the same SQL against a steady-state assets table, which is also safe.
        try:
            pruned = self.prune_orphaned_fields()
            result['pruned_fields'] = len(pruned)
        except Exception as e:
            logger.error(f"Failed to prune orphaned fields: {e}")
            result['errors'].append(f"prune: {e}")

        try:
            new_fields = self._detect_new_fields()
        except Exception as e:
            logger.error(f"Failed to detect new fields: {e}")
            result['errors'].append(str(e))
            result['duration_seconds'] = round(time.time() - start, 2)
            return result

        if not new_fields:
            logger.info("No new extended fields detected — skipping LLM and synonym update")
            result['skipped'] = True
            result['duration_seconds'] = round(time.time() - start, 2)
            return result

        result['new_fields'] = len(new_fields)
        field_names = [f['field_name'] for f in new_fields]
        logger.info(f"Detected {len(new_fields)} new extended fields: {field_names}")

        descriptions = {}
        fields_needing_desc = [
            f for f in new_fields
            if not self.field_registry.has_description(f['field_name'])
        ]

        if use_llm and fields_needing_desc and generator.is_available():
            try:
                descriptions = generator.generate_batch_single_call(
                    fields_needing_desc
                )
                result['descriptions_generated'] = len(descriptions)
            except Exception as e:
                logger.error(f"Batch description generation failed: {e}")
                result['errors'].append(f"description_generation: {e}")

        for field_info in new_fields:
            name = field_info['field_name']
            source = field_info.get('data_source', 'unknown')

            if name in descriptions:
                desc = descriptions[name]
                desc_source = 'llm'
            else:
                desc = f"Extended attribute '{name}' from {source}"
                desc_source = 'hardcoded'
                result['descriptions_fallback'] += 1

            display_name = name.replace('_', ' ').replace('__', ' - ').title()

            try:
                self.field_registry.register_extended_field(
                    field_name=name,
                    display_name=display_name,
                    description=desc,
                    data_source=source,
                    description_source=desc_source,
                )
            except Exception as e:
                logger.error(f"Failed to register field '{name}': {e}")
                result['errors'].append(f"register_{name}: {e}")

        if self.chromadb_store and self.embedding_service:
            try:
                self._update_chromadb(field_names)
                result['embeddings_added'] = len(field_names)
            except Exception as e:
                logger.error(f"ChromaDB update failed: {e}")
                result['errors'].append(f"chromadb: {e}")

        # Incremental synonym update — only generate synonyms for the new
        # fields and merge into the existing lookup table.  Avoids re-running
        # all 3 layers (rules + LLM + embeddings) for the entire field population.
        try:
            self.synonym_generator.update_lookup_table_for_fields(
                field_names, use_llm=use_llm,
            )
            result['synonyms_rebuilt'] = True
        except Exception as e:
            logger.error(f"Incremental synonym update failed: {e}")
            result['errors'].append(f"synonyms: {e}")

        result['duration_seconds'] = round(time.time() - start, 2)
        logger.info(
            f"Embedding update complete: {result['new_fields']} new, "
            f"{result['pruned_fields']} pruned, "
            f"{result['descriptions_generated']} LLM descriptions, "
            f"{result['descriptions_fallback']} fallback, "
            f"synonyms_rebuilt={result['synonyms_rebuilt']}, "
            f"{result['duration_seconds']}s"
        )
        return result

    def run_full_refresh(self) -> dict[str, Any]:
        """Regenerate descriptions + synonyms for EXTENDED fields only.

        Core (static) fields are never touched: descriptions come from
        ``get_extended_fields()`` (``field_type='extended'``) and the synonym
        rebuild is scoped to those same fields via ``replace=True`` — so
        existing Core synonym entries are preserved verbatim. ``manual``
        descriptions are preserved too; only ``llm`` and ``hardcoded``
        (fallback) descriptions are regenerated, so a field that fell back to
        the stub description while the LLM was down gets a real one.
        """
        start = time.time()
        try:
            generator = self._refresh_llm_client()
        except Exception as e:
            logger.error(f"Failed to resolve LLM client for full refresh: {e}")
            generator = self.description_generator
        result = {'mode': 'full', 'errors': []}

        try:
            fields = self.field_registry.get_extended_fields()
        except Exception as e:
            logger.error(f"Full refresh: failed to load extended fields: {e}")
            result['errors'].append(str(e))
            fields = []

        # Descriptions — regenerate every non-manual extended description (llm,
        # hardcoded fallback, or an unset/NULL source); manual descriptions stay
        # as the admin set them.
        try:
            regen_fields = [
                {'field_name': f.field_name, 'data_source': f.data_source}
                for f in fields
                if f.description_source != 'manual'
            ]
            if regen_fields and generator.is_available():
                descriptions = generator.generate_batch_single_call(
                    regen_fields
                )
                for name, desc in descriptions.items():
                    self.field_registry.update_description(name, desc, 'llm')
                result['descriptions_regenerated'] = len(descriptions)
            else:
                result['descriptions_regenerated'] = 0
        except Exception as e:
            logger.error(f"Full description regen failed: {e}")
            result['errors'].append(str(e))
            result['descriptions_regenerated'] = 0

        # Synonyms — extended-only replace, but seed Core so "Regenerate All"
        # produces a COMPLETE table even when the on-disk lookup was missing or
        # empty (Core synonyms are deterministic, not re-derived from the LLM).
        # synonyms_regenerated stays False unless the rebuild actually runs, so
        # a fields-load failure isn't reported as success.
        result['synonyms_regenerated'] = False
        try:
            ext_names = [f.field_name for f in fields]
            if ext_names:
                self._regenerate_synonyms_for(ext_names, seed_core=True)
                result['synonyms_regenerated'] = True
        except Exception as e:
            logger.error(f"Full synonym regen failed: {e}")
            result['errors'].append(str(e))

        result['duration_seconds'] = round(time.time() - start, 2)
        return result

    def regenerate_field_synonyms(self, field_name: str) -> None:
        """Regenerate one field's synonyms in place, replacing its old terms.

        Manual admin action (per-field Regen) — resolves the provider fresh so
        the Layer-2 LLM step runs when available. ``replace=True`` clears this
        field's stale terms without touching any other field's entries.
        """
        try:
            self._refresh_llm_client()
        except Exception as e:
            logger.warning(
                f"Failed to resolve LLM client for field synonym regen: {e}")
        self._regenerate_synonyms_for([field_name])

    def _regenerate_synonyms_for(
            self, field_names: list[str], *, seed_core: bool = False) -> None:
        """Rebuild synonyms for ``field_names`` (replace), serialized.

        Holds ``_synonym_lock`` so per-field Regen and the background
        "Regenerate All" (and two per-field Regens) can't run their synonym
        steps concurrently — otherwise ``reload_fields()`` would clear the
        shared generator's caches mid-run and the two lookup-file writes would
        clobber each other. ``reload_fields()`` first so a just-updated
        description feeds generation. (Cross-process races with the pipeline's
        own s22 write are pre-existing and out of scope.)
        """
        with self._synonym_lock:
            self.synonym_generator.reload_fields()
            self.synonym_generator.update_lookup_table_for_fields(
                field_names, use_llm=True, replace=True, seed_core=seed_core)

    def get_synonyms_by_api_field(self) -> dict[str, list[str]]:
        """Invert the synonym lookup file into ``{api_field: [terms...]}``.

        Reads ``static/synonym_lookup.json`` via ``default_config`` directly —
        deliberately NOT through ``self.synonym_generator`` (a lazy heavy
        property that loads the embedding model + ChromaDB). This keeps the
        field-registry list endpoint cheap. Any read/parse/shape failure
        degrades to an empty map so the list never breaks on a missing or
        malformed file.

        Term order from the file is preserved (core/canonical terms are seeded
        first), which makes for readable per-field previews.
        """
        import json
        from pathlib import Path
        from src.services.embedding.config import default_config

        inverted: dict[str, list[str]] = {}
        try:
            path = Path(default_config.synonym_output_path)
            if not path.exists():
                return inverted
            with open(path, encoding='utf-8') as fh:
                data = json.load(fh)
            synonyms = data.get('synonyms') if isinstance(data, dict) else None
            if isinstance(synonyms, dict):
                for term, api_field in synonyms.items():
                    inverted.setdefault(api_field, []).append(term)
        except Exception as e:
            logger.warning(
                f"Could not read synonym lookup for per-field view: {e}")
            return {}
        return inverted

    def get_status(self) -> dict[str, Any]:
        """Get current embedding system status."""
        try:
            generator = self._refresh_llm_client()
        except Exception as e:
            logger.warning(f"Failed to refresh LLM client for status: {e}")
            generator = self.description_generator
        status = {}

        try:
            all_fields = self.field_registry.get_all_fields()
            static = [f for f in all_fields if f.field_type == 'static']
            extended = [f for f in all_fields if f.field_type == 'extended']
            with_desc = [f for f in all_fields if f.description and f.description.strip()]
            status['field_registry'] = {
                'total': len(all_fields),
                'static': len(static),
                'extended': len(extended),
                'with_descriptions': len(with_desc),
                'without_descriptions': len(all_fields) - len(with_desc),
            }
        except Exception as e:
            status['field_registry'] = {'error': str(e)}

        try:
            if self.chromadb_store:
                stats = self.chromadb_store.get_collection_stats()
                status['chromadb'] = {
                    'fields_count': stats.get('fields_count', 0),
                    'values_count': stats.get('values_count', 0),
                }
            else:
                status['chromadb'] = {'status': 'unavailable'}
        except Exception as e:
            status['chromadb'] = {'error': str(e)}

        try:
            import json
            from pathlib import Path
            import os
            lookup_path = Path(self.synonym_generator.config.synonym_output_path)
            if lookup_path.exists():
                with open(lookup_path) as f:
                    data = json.load(f)
                meta = data.get('_metadata', {})
                mtime = os.path.getmtime(lookup_path)
                age_hours = (time.time() - mtime) / 3600
                status['synonyms'] = {
                    'total_terms': meta.get('matched_terms', 0),
                    'last_generated': meta.get('generated_at', meta.get('last_incremental_update')),
                    'file_age_hours': round(age_hours, 1),
                }
            else:
                status['synonyms'] = {'status': 'file_missing'}
        except Exception as e:
            status['synonyms'] = {'error': str(e)}

        try:
            from src.services.embedding.pipeline_llm import describe_client
            client = generator.llm_client
            provider, model = describe_client(client)
            status['llm_server'] = {
                'available': generator.is_available(),
                'provider': provider,
                'model': model,
                'url': getattr(getattr(client, 'config', None), 'base_url', '') or '',
            }
        except Exception as e:
            status['llm_server'] = {'available': False, 'error': str(e)}

        return status

    def _detect_new_fields(self) -> list[dict]:
        """Detect extended_attributes keys not in field_registry."""
        rows = self.db.execute_query(_NEW_FIELDS_SQL, {})
        if not rows:
            return []

        result = []
        for row in rows:
            field_name = row['field_name']
            data_source = row.get('data_source', 'unknown')

            # Extract raw field name (without source prefix) for sample query
            source_prefix = data_source.lower().replace(' ', '_') + '_'
            raw_field = (field_name[len(source_prefix):]
                         if field_name.startswith(source_prefix)
                         else field_name)

            try:
                sample_rows = self.db.execute_query(
                    _SAMPLE_VALUES_SQL,
                    {'raw_field': raw_field}
                )
                samples = [r['val'] for r in sample_rows if r['val']]
            except Exception:
                samples = []

            result.append({
                'field_name': field_name,
                'data_source': data_source,
                'sample_values': samples,
            })

        return result

    def _update_chromadb(self, field_names: list[str]) -> None:
        """Generate embeddings for new fields and upsert into ChromaDB."""
        fields = []
        for name in field_names:
            field_def = self.field_registry.get_field_by_name(name)
            if field_def:
                fields.append(field_def)

        if not fields:
            return

        embeddings = []
        ids = []
        metadatas = []
        for field_def in fields:
            doc = field_def.to_embedding_document()
            embedding = self.embedding_service.embed_text(doc)
            embeddings.append(embedding.tolist())
            ids.append(field_def.field_name)
            metadatas.append({
                'field_name': field_def.field_name,
                'display_name': field_def.display_name,
                'description': field_def.description,
                'field_type': field_def.field_type,
                'data_source': field_def.data_source or '',
            })

        self.chromadb_store.add_field_embeddings(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        logger.info(f"Upserted {len(ids)} field embeddings in ChromaDB")
