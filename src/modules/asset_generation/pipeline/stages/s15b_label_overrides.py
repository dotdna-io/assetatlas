# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 15b — re-apply asset_label manual overrides onto the scored DataFrame.

Runs after s15 OS EOL enrichment and before s16 persistence so the
override column is on `ctx.scored_df` (the DataFrame s16 actually
bulk-upserts to `assets`) when the row is written.
"""

import logging
import time

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class AssetLabelOverridesStage(PipelineStage):
    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Stage 15b: Asset Label Overrides"

    @property
    def key(self) -> str:
        return "asset_label_overrides"

    @property
    def stage_label(self) -> str:
        return "Asset Label Overrides"

    def _execute(self, ctx: PipelineContext) -> None:
        if not (
            getattr(self.service, "enable_asset_label_overrides", False)
            and getattr(self.service, "asset_label_override_repo", None)
        ):
            logger.info("  [15b] Asset label overrides skipped (disabled)")
            ctx.stats["stages"]["asset_label_overrides"] = {"skipped": True}
            return

        sub_start = time.time()
        overrides = self.service.asset_label_override_repo.get_all_overrides()
        ctx.scored_df = self.service.asset_label_override_repo.apply_overrides(
            ctx.scored_df, overrides
        )
        ctx.stats["stages"]["asset_label_overrides"] = {
            "duration": time.time() - sub_start,
            "overrides_applied": int(
                ctx.scored_df["asset_label_override"].notna().sum()
            ) if "asset_label_override" in ctx.scored_df.columns else 0,
        }
