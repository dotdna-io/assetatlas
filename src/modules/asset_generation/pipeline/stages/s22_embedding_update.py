# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Stage 22/22: Incremental Embedding Update

Detects new extended_attributes fields and incrementally updates the
embedding system (descriptions, ChromaDB, synonyms).

Always non-fatal - exceptions are caught and logged. The pipeline
succeeds regardless of embedding update outcome.
"""

import logging

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class EmbeddingUpdateStage(PipelineStage):

    # Generates descriptions/embeddings from the committed asset set via an
    # external LLM — must run after the core commit and outside the scope so a
    # slow LLM can't hold the post-TRUNCATE ACCESS EXCLUSIVE lock (#813).
    post_commit = True

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Incremental Embedding Update"

    @property
    def key(self) -> str:
        return "embedding_update"

    @property
    def stage_label(self) -> str:
        return "Embedding Update"

    def should_skip(self, ctx: PipelineContext) -> bool:
        if not getattr(self.service, '_embedding_auto_update', True):
            return True
        return False

    def _execute(self, ctx: PipelineContext) -> None:
        try:
            # Try to reuse the app-level EmbeddingIntegrationService (has LLM client)
            integration = self._get_integration_service()
            result = integration.run_incremental_update(
                use_llm=getattr(self.service, '_llm_descriptions_enabled', False),
            )

            ctx.stats['stages'][self.key] = {
                'new_fields': result.get('new_fields', 0),
                'pruned_fields': result.get('pruned_fields', 0),
                'descriptions_generated': result.get('descriptions_generated', 0),
                'synonyms_rebuilt': result.get('synonyms_rebuilt', False),
                'skipped': result.get('skipped', False),
            }

            if result.get('errors'):
                logger.warning(
                    f"Embedding update completed with errors: {result['errors']}"
                )
        except Exception as e:
            logger.error(f"Embedding update stage failed (non-fatal): {e}")
            ctx.stats['stages'][self.key] = {'error': str(e), 'skipped': True}

    def _get_integration_service(self):
        """Get EmbeddingIntegrationService, preferring app-context version with LLM."""
        from src.modules.asset_generation.services.embedding_integration import (
            EmbeddingIntegrationService,
        )

        # Try Flask app context first — the app-scoped service resolves the LLM
        # client fresh per run from the configured chat provider.
        try:
            from flask import current_app
            svc = getattr(current_app, 'embedding_integration_service', None)
            if svc:
                return svc
        except (ImportError, RuntimeError):
            pass

        # Fallback: create fresh instance (no LLM, Layer 2 disabled)
        return EmbeddingIntegrationService(self.service.db)
