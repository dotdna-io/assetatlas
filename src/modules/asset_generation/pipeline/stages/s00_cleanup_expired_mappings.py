# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 0 — clean up expired component-to-asset-ID mappings."""

import logging

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class CleanupExpiredMappingsStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Stage 0/12: Cleaning Up Expired Component Mappings"

    @property
    def key(self) -> str:
        return "cleanup_expired"

    @property
    def stage_label(self) -> str:
        return "Cleanup Expired Mappings"

    def _execute(self, ctx: PipelineContext) -> None:
        expiration_days = self.service._get_setting(
            'asset_generation', 'mapping_expiration_days'
        )
        expired_count = self.service.mapping_repo.cleanup_expired_mappings(
            expiration_days=expiration_days
        )

        ctx.stats['stages'][self.key] = {
            'expired_count': expired_count,
        }

        logger.info(
            "  Deleted %d expired mappings (inactive for >%d days)",
            expired_count, expiration_days,
        )
