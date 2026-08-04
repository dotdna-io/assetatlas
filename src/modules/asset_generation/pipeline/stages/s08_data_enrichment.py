# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 6 — enrich asset data (MAC vendors, IPAM, OS normalization)."""

import logging

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class DataEnrichmentStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Stage 6/12: Enriching Asset Data"

    @property
    def key(self) -> str:
        return "enrichment"

    @property
    def stage_label(self) -> str:
        return "Data Enrichment"

    def _execute(self, ctx: PipelineContext) -> None:
        ctx.enriched_df = self.service._enrich_data(
            ctx.expanded_df,
            mac_vendor_df=ctx.mac_vendor_df,
            ipam_df=ctx.ipam_df,
        )

        enrichment_stats = self.service.enricher.get_enrichment_stats()
        ctx.stats['stages'][self.key] = {
            'enrichment_stats': enrichment_stats,
        }

        logger.info("  Enriched %d rows", len(ctx.enriched_df))
        if enrichment_stats:
            logger.info(
                "    MAC vendors enriched: %d",
                enrichment_stats.get('mac_vendors_enriched', 0),
            )
            logger.info(
                "    OS normalized: %d",
                enrichment_stats.get('os_normalized_count', 0),
            )
