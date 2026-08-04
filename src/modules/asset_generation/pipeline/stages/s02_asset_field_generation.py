# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 2 — create the composite 'asset' field and validate rows."""

import logging

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


def _load_denylist(db):
    """Load enabled denylist components; fail-open to empty on any error."""
    from src.modules.merge_denylist.repository import MergeDenylistRepository

    return MergeDenylistRepository(db).load_enabled_components()


class AssetFieldGenerationStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Stage 2/12: Creating Asset Field"

    @property
    def key(self) -> str:
        return "asset_field_generation"

    @property
    def stage_label(self) -> str:
        return "Asset Field Generation"

    def _execute(self, ctx: PipelineContext) -> None:
        try:
            denylist = _load_denylist(self.service.db)
        except Exception as exc:  # never break asset generation on a denylist read
            logger.warning("Denylist load failed (%s); proceeding without it", exc)
            denylist = frozenset()
        self.service.validator.denylist_components = denylist

        ctx.asset_field_df = self.service.validator.create_asset_field(
            ctx.preprocessed_df
        )
        validation_stats = self.service._validate_data(ctx.asset_field_df)

        ctx.stats['stages'][self.key] = {
            'validation': validation_stats,
        }

        logger.info(
            "  Created asset fields for %d rows", len(ctx.asset_field_df),
        )
        if 'valid_assets' in validation_stats:
            logger.info(
                "    Valid assets: %d, Invalid: %d",
                validation_stats['valid_assets'],
                validation_stats['invalid_assets'],
            )
        else:
            logger.info("    Validation stats: %s", validation_stats)
