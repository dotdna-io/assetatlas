# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 6.7 — select best OS for each asset."""

import logging

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class OSSelectionStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Stage 6.7/12: Selecting Best OS for Assets"

    @property
    def key(self) -> str:
        return "os_selection"

    @property
    def stage_label(self) -> str:
        return "OS Selection"

    def _execute(self, ctx: PipelineContext) -> None:
        ctx.enriched_df = self.service.os_selector.select_best_os_for_assets(
            ctx.enriched_df, asset_id_field='id',
        )

        assets_with_os = 0
        if 'os_normalized_best' in ctx.enriched_df.columns:
            assets_with_os = int(
                ctx.enriched_df['os_normalized_best'].notna().sum()
            )

        ctx.stats['stages'][self.key] = {
            'assets_with_os': assets_with_os,
        }

        logger.info(
            "  Selected best OS for %d assets (%d with OS data)",
            len(ctx.enriched_df['id'].unique()),
            assets_with_os,
        )
