# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 6.8 — prepare provider data before aggregation."""

import logging

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class ProviderPreparationStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Stage 6.8/12: Preparing Provider Data"

    @property
    def key(self) -> str:
        return "provider_prepare"

    @property
    def stage_label(self) -> str:
        return "Provider Data Preparation"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return not (
            self.service.enable_provider_tracking and self.service.provider_repo
        )

    def _execute(self, ctx: PipelineContext) -> None:
        # Let failures propagate. This stage only runs when provider tracking is
        # enabled (see should_skip), so a failure here is a real error that must
        # abort the run rather than silently ship empty asset_providers. The
        # orchestrator logs the failing stage with exc_info and rolls the atomic
        # transaction back.
        ctx.providers_df_prepared = (
            self.service.provider_repo.preprocess_for_providers(
                ctx.enriched_df,
            )
        )

        ctx.stats['stages'][self.key] = {
            'providers_prepared': len(ctx.providers_df_prepared),
        }

        logger.info(
            "  Prepared %d provider records",
            len(ctx.providers_df_prepared),
        )
