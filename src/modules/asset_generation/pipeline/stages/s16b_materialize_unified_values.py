# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 16b — materialize Unified Field values into assets.unified_values.

Runs as a CORE stage (post_commit stays False) in two positions:

  scope="all"                — immediately after PersistenceStage, so tag rules
                               (s17) and posture evaluation (s17b) see the same
                               stored values the live merge expressions produce;
  scope="enrichment_sourced" — immediately after CsvEnrichmentStage (s21),
                               refreshing only fields with at least one
                               ext:enrichment: source, so stored values match
                               the post-enrichment rows post-run queries see.

Core placement is deliberate: a post-commit stage would leave s17/s17b reading
NULLs (they run before the commit), and post-commit errors are swallowed —
here a failed refresh must abort the run, and inside the atomic scope the
rollback takes the whole run with it. The one exception: a database without
migration 149 is NOT a failure — should_skip() probes for the columns before
touching them and the stage records {'skipped': True, 'reason': 'migration
149 not applied'}, leaving every read on the live merge expression.

The two passes write stats under DISTINCT keys, and the phase-mapping gate
(tests/unit/modules/test_phase_mapping.py) discovers stage keys by reading
`.key` off a bare ``cls.__new__(cls)`` instance — no __init__ — per stage
CLASS. That is why key/name/label are class attributes (the constructor also
sets them per scope, which wins for any normally-built instance) and why the
enrichment pass has its own thin subclass: one class can only ever surface one
key to that discovery.
"""

import logging
import time

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext
from src.modules.fields.unified.materialize import (
    is_materializable,
    materialization_available,
    refresh_unified_values,
    warn_migration_149_pending,
)
from src.modules.fields.unified.repository import UnifiedFieldsRepository

logger = logging.getLogger(__name__)

SCOPES = ("all", "enrichment_sourced")

_ENRICHMENT_SOURCE_PREFIX = "ext:enrichment:"


class MaterializeUnifiedValuesStage(PipelineStage):

    # Class-level defaults for the "all" scope. They exist so the phase-mapping
    # discovery above can read .key/.name off a bare __new__ instance; __init__
    # overwrites them per scope, which shadows these for real instances.
    _KEY = "materialize_unified_values"
    _NAME = "Materializing Unified Field Values"
    _STAGE_LABEL = "Materialize Unified Values"

    def __init__(self, service, scope: str):
        if scope not in SCOPES:
            raise ValueError(f"scope must be one of {SCOPES}, got {scope!r}")
        self.service = service
        self.scope = scope
        if scope == "enrichment_sourced":
            self._KEY = "materialize_unified_values_enrichment"
            self._NAME = "Re-materializing Enrichment-sourced Unified Field Values"
            self._STAGE_LABEL = "Materialize Unified Values (enrichment)"
        self._definitions = None
        self._migration_pending = False

    @property
    def name(self) -> str:
        return self._NAME

    @property
    def key(self) -> str:
        return self._KEY

    @property
    def stage_label(self) -> str:
        return self._STAGE_LABEL

    def _scoped_definitions(self):
        """Materializable definitions for this stage's scope (queried once)."""
        if self._definitions is None:
            defs = UnifiedFieldsRepository(self.service.db).list_for_catalog()
            defs = [d for d in defs if is_materializable(d)]
            if self.scope == "enrichment_sourced":
                defs = [d for d in defs if any(
                    str(s).startswith(_ENRICHMENT_SOURCE_PREFIX)
                    for s in d["sources"]
                )]
            self._definitions = defs
        return self._definitions

    def should_skip(self, ctx: PipelineContext) -> bool:
        # Probe BEFORE list_for_catalog: on a database without migration 149
        # the catalog SELECT raises UndefinedColumn, and base.execute() calls
        # should_skip outside any try — a raise here would abort every run
        # until the migration lands. Skipping is the degrade: the resolver
        # keeps serving the live merge expression.
        self._migration_pending = not materialization_available(self.service.db)
        if self._migration_pending:
            warn_migration_149_pending()
            return True
        return not self._scoped_definitions()

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        if self.should_skip(ctx) and self._migration_pending:
            logger.info(f"\n[{self.name}] Skipped — migration 149 not applied")
            ctx.stats['stages'][self.key] = {
                'skipped': True, 'reason': 'migration 149 not applied'}
            return ctx
        return super().execute(ctx)

    def _execute(self, ctx: PipelineContext) -> None:
        # Deferred: pipeline_orchestrator imports this module at module level,
        # so importing it back here at module level would be circular.
        from src.modules.asset_generation.services.pipeline_orchestrator import (
            _build_pipeline_field_resolver,
        )

        start = time.time()
        result = refresh_unified_values(
            self.service.db,
            _build_pipeline_field_resolver(self.service.db),
            self._scoped_definitions(),
        )
        elapsed_ms = int((time.time() - start) * 1000)

        ctx.stats['stages'][self.key] = {
            'fields': result['fields'],
            'rows': result['rows'],
            'chunks': result['chunks'],
            'ms': elapsed_ms,
        }
        logger.info(
            "  Materialized %d unified field(s) over %d asset(s) "
            "in %d statement(s), %d ms",
            result['fields'], result['rows'], result['chunks'], elapsed_ms,
        )


class MaterializeUnifiedValuesEnrichmentStage(MaterializeUnifiedValuesStage):
    """The enrichment-scoped pass as its own class, so the phase-mapping
    discovery surfaces its distinct key (see module docstring). Behaviorally
    identical to MaterializeUnifiedValuesStage(service, "enrichment_sourced")."""

    _KEY = "materialize_unified_values_enrichment"
    _NAME = "Re-materializing Enrichment-sourced Unified Field Values"
    _STAGE_LABEL = "Materialize Unified Values (enrichment)"

    def __init__(self, service):
        super().__init__(service, scope="enrichment_sourced")
