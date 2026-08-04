# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 21 — Apply CSV enrichment datasets to persisted assets."""

import logging

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class CsvEnrichmentStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Stage 21/21: Applying CSV Enrichments"

    @property
    def key(self) -> str:
        return "csv_enrichment"

    @property
    def stage_label(self) -> str:
        return "CSV Enrichment"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return not hasattr(self.service, 'enrichment_service') or self.service.enrichment_service is None

    def _execute(self, ctx: PipelineContext) -> None:
        try:
            results = self.service.enrichment_service.apply_all_active()

            total_matched = sum(r["matched_count"] for r in results)
            ctx.stats['stages'][self.key] = {
                'datasets_applied': len(results),
                'total_assets_enriched': total_matched,
                'details': results,
            }

            if results:
                logger.info(
                    "  Applied %d enrichment datasets, %d total assets enriched",
                    len(results), total_matched,
                )
            else:
                logger.info("  No active enrichment datasets")

        except Exception as e:
            logger.error("CSV enrichment stage failed: %s", e, exc_info=True)
            ctx.stats['stages'][self.key] = {'error': str(e)}
