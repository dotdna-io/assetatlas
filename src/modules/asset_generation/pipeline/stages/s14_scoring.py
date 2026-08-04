# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 8 — lifecycle metadata, scoring, column renames, category cleanup."""

import logging

import pandas as pd

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class ScoringStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Scoring"

    @property
    def key(self) -> str:
        return "scoring"

    @property
    def stage_label(self) -> str:
        return "Scoring"

    def _settings_get(self, category: str, key: str):
        """Read a custom setting if running inside a Flask app context.

        CLI/test paths (no Flask context) silently skip — same behaviour as
        the previous broad except: but no longer disguises real errors that
        happen inside the settings_manager itself. Mirrors the
        has_app_context() guard pattern in s20/s22.
        """
        from flask import current_app, has_app_context
        if not has_app_context():
            return None
        try:
            return current_app.settings_manager.get(category, key)
        except Exception as e:
            logger.warning("Could not read setting %s/%s: %s", category, key, e)
            return None

    def _load_custom_scores(self):
        """Load custom reliability scores from settings, if available."""
        custom_scores = self._settings_get(
            'asset_generation', 'source_reliability_scores'
        )
        if custom_scores and isinstance(custom_scores, dict):
            self.service.scorer.source_reliability_scores = custom_scores
            logger.info("Loaded custom source reliability scores from settings")

    def _load_calibration_curve(self):
        """Load custom calibration curve from settings, if available."""
        curve = self._settings_get(
            'asset_generation', 'calibration_curve'
        )
        if curve and isinstance(curve, dict):
            # Settings may store keys as strings; convert to float
            self.service.scorer.calibration_curve = {
                float(k): float(v) for k, v in curve.items()
            }
            logger.info("Loaded custom calibration curve from settings")

    def _execute(self, ctx: PipelineContext) -> None:
        # 0. Load custom scores and calibration curve from settings
        self._load_custom_scores()
        self._load_calibration_curve()

        # 1. Populate lifecycle metadata BEFORE scoring
        ctx.aggregated_df = self.service._populate_lifecycle_metadata(
            ctx.aggregated_df, ctx.generation_run_id,
        )

        # 2. Calculate scores
        ctx.scored_df = self.service._calculate_scores(
            ctx.aggregated_df, ctx.expanded_df,
        )

        # 3. Mean confidence
        mean_confidence = ctx.scored_df['confidence'].apply(
            lambda x: float(x[0]) if isinstance(x, list) else float(x)
        ).mean()
        ctx.stats['stages'][self.key] = {
            'mean_confidence': mean_confidence,
        }

        # 4. Load old assets BEFORE saving (for chronicle comparison)
        ctx.old_assets_df = (
            self.service.asset_repo.get_all_assets()
            if ctx.track_changes
            else pd.DataFrame()
        )

        # 5. Load historical IDs
        ctx.historical_ids = (
            self.service.asset_repo.get_all_historical_asset_ids()
            if ctx.track_changes
            else []
        )

        # 6. Column renames: extended_fields → extended_attributes
        if 'extended_attributes' in ctx.scored_df.columns:
            logger.warning(
                "extended_attributes column already exists before rename!",
            )

        if 'extended_fields' in ctx.scored_df.columns:
            if 'extended_attributes' in ctx.scored_df.columns:
                logger.warning(
                    "Dropping existing extended_attributes column before rename",
                )
                ctx.scored_df = ctx.scored_df.drop(
                    columns=['extended_attributes'],
                )
            ctx.scored_df = ctx.scored_df.rename(
                columns={'extended_fields': 'extended_attributes'},
            )

        # 7. OS column renames (os_normalized_best → os_normalized, etc.)
        if 'os_normalized_best' in ctx.scored_df.columns:
            columns_to_drop = []
            if 'os_normalized' in ctx.scored_df.columns and 'os_normalized' != 'os_normalized_best':
                columns_to_drop.append('os_normalized')
            if 'os_enrichment' in ctx.scored_df.columns and 'os_enrichment' != 'os_enrichment_best':
                columns_to_drop.append('os_enrichment')

            if columns_to_drop:
                ctx.scored_df = ctx.scored_df.drop(columns=columns_to_drop)

            # Drop 'os_orig' before renaming to avoid duplicates
            if 'os_orig' in ctx.scored_df.columns:
                if 'os_orig_best' in ctx.scored_df.columns or 'os_orig_x' in ctx.scored_df.columns:
                    ctx.scored_df = ctx.scored_df.drop(columns=['os_orig'])

            rename_dict = {
                'os_normalized_best': 'os_normalized',
                'os_enrichment_best': 'os_enrichment',
                'os_x': 'os',
                'os_orig_x': 'os_orig',
                'os_orig_best': 'os_orig',
            }
            rename_dict = {
                k: v for k, v in rename_dict.items()
                if k in ctx.scored_df.columns
            }
            ctx.scored_df = ctx.scored_df.rename(columns=rename_dict)

            if 'os_best_source' in ctx.scored_df.columns:
                ctx.scored_df = ctx.scored_df.drop(
                    columns=['os_best_source'],
                )

        # 7b. Disambiguate bare 'ios' OS values using manufacturer context
        ctx.scored_df = self.service._disambiguate_ios_os(ctx.scored_df)

        # 8. Category column cleanup
        if 'category_best' in ctx.scored_df.columns:
            cols_to_drop = [
                'category_all', 'subcategory_all',
                'category_confidence_best', 'category_rule_type_best',
                'predicted_category', 'predicted_subcategory',
            ]
            cols_to_drop = [
                c for c in cols_to_drop if c in ctx.scored_df.columns
            ]
            if 'category' in ctx.scored_df.columns:
                cols_to_drop.append('category')
            if 'subcategory' in ctx.scored_df.columns:
                cols_to_drop.append('subcategory')
            if cols_to_drop:
                ctx.scored_df = ctx.scored_df.drop(columns=cols_to_drop)
            ctx.scored_df = ctx.scored_df.rename(columns={
                'category_best': 'category',
                'subcategory_best': 'subcategory',
            })

            # Belt-and-suspenders: remap any invalid top-level category that
            # slipped through the CategorySelector validation.
            from src.modules.asset_generation.domain.category_selector import CategorySelector
            valid = CategorySelector.VALID_CATEGORIES
            if 'category' in ctx.scored_df.columns:
                bad = ctx.scored_df['category'].notna() & ~ctx.scored_df['category'].isin(valid)
                if bad.any():
                    unknown_sub = bad & (ctx.scored_df['subcategory'] == 'unknown')
                    ctx.scored_df.loc[unknown_sub, 'subcategory'] = ctx.scored_df.loc[unknown_sub, 'category']
                    ctx.scored_df.loc[bad, 'category'] = 'miscellaneous'
                    logger.warning("Remapped %d assets with invalid category to miscellaneous", bad.sum())

            # The snapshot s13 took (7.6c) predates this validation, so it can
            # carry a category this remap has just rejected. It is persisted now
            # (#1154) and clearing an override writes it straight back into
            # assets.category — an invalid value the Explorer's category facet
            # does not list, surviving until the next run remaps it again. Apply
            # the same rule to the snapshot so the two can never disagree.
            if 'category_predicted' in ctx.scored_df.columns:
                bad_pred = (
                    ctx.scored_df['category_predicted'].notna()
                    & ~ctx.scored_df['category_predicted'].isin(valid)
                )
                if bad_pred.any():
                    if 'subcategory_predicted' in ctx.scored_df.columns:
                        unknown_pred = bad_pred & (ctx.scored_df['subcategory_predicted'] == 'unknown')
                        ctx.scored_df.loc[unknown_pred, 'subcategory_predicted'] = (
                            ctx.scored_df.loc[unknown_pred, 'category_predicted']
                        )
                    ctx.scored_df.loc[bad_pred, 'category_predicted'] = 'miscellaneous'
