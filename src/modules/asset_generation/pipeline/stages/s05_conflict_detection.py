# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 4.5 — detect business-level data quality conflicts."""

import logging

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class ConflictDetectionStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Detecting Conflicts"

    @property
    def key(self) -> str:
        return "conflict_detection"

    @property
    def stage_label(self) -> str:
        return "Conflict Detection"

    def _execute(self, ctx: PipelineContext) -> None:
        providers_df = ctx.providers_df_prepared
        if providers_df is None:
            logger.warning(
                "  Provider data not available; skipping conflict detection"
            )
            ctx.stats['stages'][self.key] = {
                'conflicts': {'total_conflicts': 0},
            }
            return

        conflict_stats = self.service._detect_conflicts(
            ctx.generation_run_id, providers_df=providers_df,
        )

        ctx.stats['stages'][self.key] = {
            'conflicts': conflict_stats,
        }

        logger.info(
            "  Conflict detection complete: %d conflicts found",
            conflict_stats['total_conflicts'],
        )
        if conflict_stats.get('has_critical'):
            logger.warning(
                "  CRITICAL conflicts detected (OS family conflicts: %d)",
                conflict_stats.get('os_family_conflicts', {}).get('count', 0),
            )
