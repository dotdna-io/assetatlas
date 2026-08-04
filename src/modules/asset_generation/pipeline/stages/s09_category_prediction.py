# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 6.5 — rule-based and ML category/subcategory prediction."""

import logging
import time

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class CategoryPredictionStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Stage 6.5/12: Category Prediction"

    @property
    def key(self) -> str:
        return "category_prediction"

    @property
    def stage_label(self) -> str:
        return "Category Prediction"

    def _execute(self, ctx: PipelineContext) -> None:
        # Stage 6.5a: Rule-based prediction
        if self.service.enable_rule_based_prediction and self.service.category_predictor:
            logger.info("  [6.5a] Rule-based prediction")
            sub_start = time.time()
            ctx.enriched_df = self.service._predict_categories_rule_based(
                ctx.enriched_df,
            )
            prediction_stats = self.service.category_predictor.get_stats()
            ctx.stats['stages']['rule_based_prediction'] = {
                'duration': time.time() - sub_start,
                'stats': prediction_stats,
            }
            logger.info(
                "    Rule-based prediction complete: "
                "%d/%d matched, %d fallback",
                prediction_stats['successful_predictions'],
                prediction_stats['total_predictions'],
                prediction_stats['fallback_predictions'],
            )
        else:
            logger.info("  [6.5a] Rule-based prediction skipped (disabled)")
            ctx.stats['stages']['rule_based_prediction'] = {'skipped': True}

        # Stage 6.5b: ML prediction (deprecated, disabled by default)
        if self.service.enable_ml_prediction and self.service.catboost_predictor:
            logger.info("  [6.5b] ML prediction")
            sub_start = time.time()
            ctx.enriched_df = self.service._predict_categories_ml(
                ctx.enriched_df,
            )
            ctx.stats['stages']['ml_prediction'] = {
                'duration': time.time() - sub_start,
            }
        else:
            logger.info("  [6.5b] ML prediction skipped (disabled)")
            ctx.stats['stages']['ml_prediction'] = {'skipped': True}
