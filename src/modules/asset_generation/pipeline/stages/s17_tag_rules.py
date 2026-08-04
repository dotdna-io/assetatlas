# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 9.5 — execute tag rules after asset generation."""

import logging

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class TagRulesStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Executing Tag Rules"

    @property
    def key(self) -> str:
        return "tag_rules"

    @property
    def stage_label(self) -> str:
        return "Tag Rules"

    def _execute(self, ctx: PipelineContext) -> None:
        try:
            from src.modules.tag_rules.integration import (
                execute_tag_rules_after_asset_generation,
            )

            tag_rules_result = execute_tag_rules_after_asset_generation(
                db_adapter=self.service.db,
                generation_run_id=str(ctx.generation_run_id),
            )
            ctx.stats['stages'][self.key] = {**tag_rules_result}
        except Exception as e:
            logger.error("Tag rules execution failed: %s", e, exc_info=True)
            ctx.stats['stages'][self.key] = {
                'status': 'failed',
                'error': str(e),
            }
            logger.warning(
                "  Continuing pipeline despite tag rules failure",
            )
