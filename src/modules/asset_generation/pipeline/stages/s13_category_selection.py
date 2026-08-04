# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 7.5-7.7 — post-agg prediction, category selection, overrides."""

import logging
import time

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext
from src.modules.asset_generation.repositories.category_override_repository import (
    CategoryOverrideRepository,
)

logger = logging.getLogger(__name__)


class CategorySelectionStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Stage 7.5-7.7/12: Category Selection"

    @property
    def key(self) -> str:
        return "category_selection_combined"

    @property
    def stage_label(self) -> str:
        return "Category Selection"

    def _execute(self, ctx: PipelineContext) -> None:
        # Stage 7.5: Post-Aggregation Category Prediction
        if self.service.enable_rule_based_prediction and self.service.category_predictor:
            logger.info("  [7.5] Post-aggregation category prediction")
            sub_start = time.time()
            ctx.aggregated_predictions = (
                self.service.category_selector.predict_categories_for_aggregated_assets(
                    ctx.aggregated_df,
                    self.service.category_predictor,
                    asset_id_field='id',
                )
            )
            ctx.stats['stages']['post_agg_prediction'] = {
                'duration': time.time() - sub_start,
                'predictions_generated': (
                    len(ctx.aggregated_predictions)
                    if ctx.aggregated_predictions is not None
                    else 0
                ),
            }
        else:
            logger.info("  [7.5] Post-aggregation prediction skipped (disabled)")
            ctx.stats['stages']['post_agg_prediction'] = {'skipped': True}

        # Stage 7.6: Category Selection
        if self.service.enable_rule_based_prediction and self.service.category_predictor:
            logger.info("  [7.6] Category selection")
            sub_start = time.time()
            selected_categories = (
                self.service.category_selector.select_best_category_for_assets(
                    ctx.enriched_df,
                    asset_id_field='id',
                    aggregated_predictions=ctx.aggregated_predictions,
                )
            )
            ctx.aggregated_df = ctx.aggregated_df.merge(
                selected_categories, on='id', how='left', suffixes=('_old', ''),
            )
            cols_to_drop = [
                c for c in ctx.aggregated_df.columns if c.endswith('_old')
            ]
            if cols_to_drop:
                ctx.aggregated_df = ctx.aggregated_df.drop(columns=cols_to_drop)
            ctx.stats['stages']['category_selection'] = {
                'duration': time.time() - sub_start,
            }
        else:
            logger.info("  [7.6] Category selection skipped (disabled)")
            ctx.stats['stages']['category_selection'] = {'skipped': True}

        # --- Sub-stage 7.6b: Tiered fallback (KB + CatBoost) ---
        if self.service.knowledge_base or (self.service.enable_ml_prediction and self.service.catboost_predictor):
            logger.info("Stage 7.6b: Applying tiered fallback...")
            pre_fallback = (ctx.aggregated_df['category_best'].isin(['miscellaneous', 'unknown'])).sum()
            ctx.aggregated_df = self.service._apply_tiered_fallback(ctx.aggregated_df)
            post_fallback = (ctx.aggregated_df['category_best'].isin(['miscellaneous', 'unknown'])).sum()
            resolved = pre_fallback - post_fallback
            logger.info(
                "Stage 7.6b complete: %d/%d fallback assets resolved (KB + CatBoost)",
                resolved, pre_fallback
            )

        # Sub-stage 7.6c: snapshot the prediction, unconditionally.
        #
        # This is the pipeline's answer for the asset, and it is what clearing a
        # manual override restores it to — so it has to be taken here, after the
        # fallback has had its say and before 7.7 can overwrite the category.
        # Doing it inside apply_overrides (as it used to be) tied it to
        # enable_category_overrides, which defaults to False: most runs supplied
        # no prediction at all, and now that the upsert persists these columns
        # such a run would push NULL at a stored snapshot. Overrides being
        # enabled has never had anything to do with whether a prediction exists.
        CategoryOverrideRepository.snapshot_predictions(ctx.aggregated_df)

        # Stage 7.7: Category Overrides
        if self.service.enable_category_overrides and self.service.category_override_repo:
            logger.info("  [7.7] Category overrides")
            sub_start = time.time()
            ctx.aggregated_df = self.service._apply_category_overrides(
                ctx.aggregated_df,
            )
            ctx.stats['stages']['category_overrides'] = {
                'duration': time.time() - sub_start,
            }
        else:
            logger.info("  [7.7] Category overrides skipped (disabled)")
            ctx.stats['stages']['category_overrides'] = {'skipped': True}
