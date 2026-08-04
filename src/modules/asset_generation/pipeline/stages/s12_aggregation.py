# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 7 — aggregate enriched rows into unique assets."""

import logging

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class AggregationStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Stage 7/12: Aggregating Assets"

    @property
    def key(self) -> str:
        return "aggregation"

    @property
    def stage_label(self) -> str:
        return "Aggregation"

    def _execute(self, ctx: PipelineContext) -> None:
        ctx.aggregated_df = self.service._aggregate_assets(ctx.enriched_df)

        ctx.stats['stages'][self.key] = {
            'unique_assets': len(ctx.aggregated_df),
        }

        logger.info(
            "  Aggregated to %d unique assets", len(ctx.aggregated_df),
        )
