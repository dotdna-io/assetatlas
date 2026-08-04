# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 5 — expand rows by asset IDs."""

import logging

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class ExpandRowsStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Stage 5/12: Expanding Rows by IDs"

    @property
    def key(self) -> str:
        return "expansion"

    @property
    def stage_label(self) -> str:
        return "Expand Rows"

    def _execute(self, ctx: PipelineContext) -> None:
        ctx.expanded_df = self.service._expand_rows_by_ids(ctx.valid_records_df)

        ctx.stats['stages'][self.key] = {
            'expanded_records': len(ctx.expanded_df),
        }

        logger.info("  Expanded to %d rows", len(ctx.expanded_df))

        # Resource limit check: components per asset
        components_per_asset = ctx.expanded_df.groupby('id').size()
        max_components = components_per_asset.max()
        if max_components > self.service.MAX_COMPONENTS_PER_ASSET:
            over_limit = (
                components_per_asset > self.service.MAX_COMPONENTS_PER_ASSET
            ).sum()
            logger.warning(
                "  %d asset(s) exceed %d components (max: %d). "
                "This may indicate data quality issues.",
                over_limit, self.service.MAX_COMPONENTS_PER_ASSET,
                max_components,
            )
