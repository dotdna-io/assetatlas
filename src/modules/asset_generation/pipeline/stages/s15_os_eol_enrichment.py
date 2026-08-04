# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 8.5 — OS end-of-life enrichment (post-aggregation)."""

import logging

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class OsEolEnrichmentStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Stage 8.5/12: OS EOL Enrichment"

    @property
    def key(self) -> str:
        return "os_eol_enrichment"

    @property
    def stage_label(self) -> str:
        return "OS EOL Enrichment"

    def _execute(self, ctx: PipelineContext) -> None:
        if 'os_normalized' not in ctx.scored_df.columns:
            logger.warning(
                "  No os_normalized column found — skipping EOL enrichment",
            )
            ctx.stats['stages'][self.key] = {
                'skipped': True,
                'reason': 'no_os_normalized',
            }
            return

        ctx.scored_df = self.service.enricher.enrich_os_eol(ctx.scored_df)

        eol_enriched = (
            int(ctx.scored_df['os_eol_status'].notna().sum())
            if 'os_eol_status' in ctx.scored_df.columns
            else 0
        )
        with_eol_data = (
            int(ctx.scored_df['os_eol_date'].notna().sum())
            if 'os_eol_date' in ctx.scored_df.columns
            else 0
        )

        ctx.stats['stages'][self.key] = {
            'assets_enriched': eol_enriched,
            'with_eol_data': with_eol_data,
        }

        logger.info(
            "  Enriched %d assets with EOL data (%d have EOL dates)",
            eol_enriched, with_eol_data,
        )
