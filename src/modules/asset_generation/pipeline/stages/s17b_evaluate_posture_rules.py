# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 9.6 — evaluate posture rules after tag rules execution."""

import logging

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class PostureRulesEvaluationStage(PipelineStage):
    """Evaluate enabled posture sub-rules and write the posture_rules JSON.

    Depends on: tag rules already executed so assets.tags is final.
    """

    def __init__(self, evaluation_service):
        self.service = evaluation_service

    @property
    def name(self) -> str:
        return "Stage 9.6/12: Evaluating Posture Rules"

    @property
    def key(self) -> str:
        return "posture_rules"

    @property
    def stage_label(self) -> str:
        return "Posture Rules"

    def _execute(self, ctx: PipelineContext) -> None:
        try:
            result = self.service.evaluate_all()
            ctx.stats['stages'][self.key] = {
                'status': 'ok',
                'updated_count': result['updated_count'],
                'duration_ms': result['duration_ms'],
            }
        except Exception as e:
            logger.error("Posture rules evaluation failed: %s", e, exc_info=True)
            ctx.stats['stages'][self.key] = {'status': 'failed', 'error': str(e)}
            logger.warning("  Continuing pipeline despite posture rules failure")
