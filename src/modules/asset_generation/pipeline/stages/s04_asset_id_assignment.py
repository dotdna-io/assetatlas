# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 4 — assign asset IDs via the identifier service."""

import logging

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class AssetIdAssignmentStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Stage 4/12: Assigning Asset IDs"

    @property
    def key(self) -> str:
        return "id_assignment"

    @property
    def stage_label(self) -> str:
        return "Asset ID Assignment"

    def _execute(self, ctx: PipelineContext) -> None:
        ctx.id_assigned_df, ctx.merge_pairs = self.service._assign_asset_ids(ctx.asset_field_df)
        assets_with_ids = (ctx.id_assigned_df['id'] != '<NA>').sum()

        ctx.stats['stages'][self.key] = {
            'assets_with_ids': assets_with_ids,
        }

        logger.info(
            "  Assigned IDs to %d/%d assets",
            assets_with_ids, len(ctx.id_assigned_df),
        )
        logger.info(
            "    Unique assets: %d", ctx.id_assigned_df['id'].nunique(),
        )
