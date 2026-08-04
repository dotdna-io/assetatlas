# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Category Selector - Selects the best category/subcategory for each asset from multiple predictions.

This module handles the selection of the most appropriate category when multiple data sources
provide different category predictions. Selection is based on:
1. Prediction confidence score
2. Consensus (majority vote across sources)
3. Rule type quality (OS patterns > combined > manufacturer > model > fallback)
4. Specificity (avoid generic "miscellaneous" when possible)
"""

import logging
from typing import Optional, Dict, Any, Tuple
import pandas as pd
import numpy as np

from src.modules.asset_generation.domain.agg_utils import extract_scalar

logger = logging.getLogger(__name__)


class CategorySelector:
    """
    Selects the best category and subcategory for each asset from multiple predictions.
    """

    # Rule type quality ranking (higher is better)
    RULE_TYPE_QUALITY = {
        'os_pattern': 10,           # Highest quality - OS is reliable indicator
        'combined_patterns': 8,     # Good quality - multiple signals
        'mac_vendor_pattern': 6,    # Medium quality - vendor suggests device type
        'knowledge_base': 5,       # KB lookup - manufacturer+model reference data (#342)
        'manufacturer_pattern': 4,  # Lower quality - manufacturer alone is weak
        'ml_catboost': 4,          # ML prediction - CatBoost model (#342)
        'vendor_category': 3,       # Low quality - vendor-provided, often inaccurate (#59)
        'model_pattern': 3,         # Low quality - model alone is weak
        'hostname_pattern': 2,     # Low quality - hostname heuristic (#288)
        'fallback': 1              # Lowest quality - no match found
    }

    # Category specificity (higher is better)
    CATEGORY_SPECIFICITY = {
        'server': 10,
        'workstation': 10,
        'mobile': 10,
        'network': 10,
        'iot': 9,
        'ot': 9,
        'storage': 8,
        'miscellaneous': 1,  # Generic fallback
        'unknown': 1         # No information
    }

    # Valid top-level categories. Anything else is remapped to miscellaneous.
    VALID_CATEGORIES = {'server', 'workstation', 'mobile', 'network', 'iot', 'ot', 'storage', 'miscellaneous', 'unknown'}

    def __init__(self):
        """Initialize category selector."""
        logger.info("CategorySelector initialized")

    def select_best_category_for_assets(
        self,
        df: pd.DataFrame,
        asset_id_field: str = 'asset_id',
        aggregated_predictions: Optional[pd.DataFrame] = None
    ) -> pd.DataFrame:
        """
        Select the best category and subcategory for each asset based on multiple factors.

        This method processes the dataframe BEFORE aggregation to ensure each asset
        gets a single best category/subcategory rather than arrays.

        Supports dual-prediction approach:
        1. Pre-aggregation predictions (from individual data sources) - consensus + diversity
        2. Post-aggregation prediction (from complete asset view) - holistic + context-rich

        Args:
            df: DataFrame with asset records (one row per data source per asset)
            asset_id_field: Name of the asset ID column
            aggregated_predictions: Optional DataFrame with post-aggregation predictions
                                   (one row per asset with category_aggregated, subcategory_aggregated)

        Returns:
            DataFrame with one row per asset containing category_best and related columns
        """
        start_time = pd.Timestamp.now()
        logger.info(f"Selecting best category for {df[asset_id_field].nunique()} unique assets...")

        if aggregated_predictions is not None:
            logger.info(f"Using dual-prediction approach (pre + post aggregation)")
        else:
            logger.info(f"Using single-prediction approach (pre-aggregation only)")

        # Work on only the columns needed for selection (avoid copying wide frame)
        selection_cols = [
            asset_id_field, 'predicted_category', 'predicted_subcategory',
            'category_confidence', 'category_rule_type', 'data_source'
        ]
        selection_cols = [c for c in selection_cols if c in df.columns]
        work_df = df[selection_cols].copy()

        # Ensure we have the required columns
        required_cols = ['predicted_category', 'predicted_subcategory', 'category_confidence', 'category_rule_type']
        missing_cols = [col for col in required_cols if col not in work_df.columns]
        if missing_cols:
            logger.warning(f"Missing columns for category selection: {missing_cols}")
            # Return empty per-asset result
            return pd.DataFrame(columns=[asset_id_field, 'category_best', 'subcategory_best',
                                         'category_confidence_best', 'category_rule_type_best',
                                         'category_best_source', 'category_all', 'subcategory_all'])

        # Calculate rule type quality score — .map() uses Cython fast path
        # Extract base rule type before '+' suffix (e.g. "os_pattern+vendor_category" → "os_pattern")
        # so composite rule types from _refine_with_vendor_category map correctly (#329)
        base_rule_type = work_df['category_rule_type'].str.split('+').str[0]
        work_df['rule_quality'] = base_rule_type.map(self.RULE_TYPE_QUALITY).fillna(1)

        # Boost vendor_category quality when it provides a specific subcategory (#161 QA-16)
        # vendor_category is highly accurate for subcategory differentiation (laptop vs desktop)
        # but was rated low (3) due to inconsistency at the category level (#59).
        # Boost to 6 when it resolves a specific subcategory.
        # Use .str.contains to match both 'vendor_category' and composite types like
        # 'os_pattern+vendor_category' (#329)
        _vc_specific = (
            work_df['category_rule_type'].str.contains('vendor_category', na=False) &
            work_df['predicted_subcategory'].notna() &
            ~work_df['predicted_subcategory'].str.lower().isin(['unknown', ''])
        )
        work_df.loc[_vc_specific, 'rule_quality'] = 6

        # Calculate category specificity score
        work_df['category_specificity'] = work_df['predicted_category'].map(self.CATEGORY_SPECIFICITY).fillna(5)
        work_df.loc[work_df['predicted_category'].isna(), 'category_specificity'] = 0

        # Calculate consensus score using vectorized operations (#45)
        # Exclude fallback predictions from consensus — sources with no data should not
        # dilute the consensus of real predictions (#59 Item 16)
        _non_fallback = work_df[work_df['category_rule_type'] != 'fallback']
        # Step 1: Count categories per (asset, category) — NaN excluded by groupby
        _cat_counts = (
            _non_fallback.groupby([asset_id_field, 'predicted_category'], dropna=True)
            .size()
            .reset_index(name='_cat_count')
        )
        # Step 2: Total non-fallback rows per asset
        _totals = _non_fallback.groupby(asset_id_field).size().reset_index(name='_total')
        # Step 3: Majority category per asset (highest count)
        _majority_idx = _cat_counts.groupby(asset_id_field)['_cat_count'].idxmax()
        _majority = _cat_counts.loc[_majority_idx, [asset_id_field, 'predicted_category', '_cat_count']].copy()
        _majority = _majority.rename(columns={
            'predicted_category': '_majority_cat',
            '_cat_count': '_majority_count',
        })
        # Step 4: Merge back and compute consensus ratio
        work_df = work_df.merge(_majority[[asset_id_field, '_majority_cat', '_majority_count']], on=asset_id_field, how='left')
        work_df = work_df.merge(_totals, on=asset_id_field, how='left')
        _consensus_ratio = work_df['_majority_count'] / work_df['_total']
        # Step 5: Majority category gets full ratio; minority gets half
        work_df['consensus_score'] = np.where(
            work_df['predicted_category'] == work_df['_majority_cat'],
            _consensus_ratio,
            _consensus_ratio * 0.5,
        )
        # Single source always gets 1.0
        work_df.loc[work_df['_total'] == 1, 'consensus_score'] = 1.0
        # Fallback-only assets: _total is NaN (no non-fallback rows) → consensus = 0.0
        # All-NaN groups: _majority_cat is NaN, so no row matches — they get ratio*0.5.
        # If *every* category in the group is NaN, _majority_count is NaN → consensus = NaN.
        # Original code returns 0.0 for all-NaN groups.
        work_df['consensus_score'] = work_df['consensus_score'].fillna(0.0)
        # Clean up temporary columns
        work_df.drop(columns=['_majority_cat', '_majority_count', '_total'], inplace=True)

        # Calculate composite score for category selection
        # Weights: confidence (40%), consensus (30%), rule_quality (20%), specificity (10%)
        # Consensus is important - if multiple sources agree, trust that
        work_df['category_selection_score'] = (
            work_df['category_confidence'] * 0.4 +
            work_df['consensus_score'] * 0.3 +
            work_df['rule_quality'] / 10 * 0.2 +  # Normalize to 0-1 range
            work_df['category_specificity'] / 10 * 0.1  # Normalize to 0-1 range
        )

        # Penalize "unknown" subcategory — prefer specific predictions (#59 Item 1)
        # OS-only rules correctly set subcategory="unknown" (can't distinguish laptop/desktop),
        # but combined rules that resolve the subcategory should win the selection.
        unknown_subcat_mask = work_df['predicted_subcategory'].str.lower() == 'unknown'
        work_df.loc[unknown_subcat_mask, 'category_selection_score'] *= 0.5

        # Handle missing categories
        work_df.loc[
            work_df['predicted_category'].isna() |
            (work_df['predicted_category'] == '') |
            (work_df['predicted_category'] == 'unknown'),
            'category_selection_score'
        ] = -1

        # =====================================================================
        # DUAL-PREDICTION APPROACH: Incorporate post-aggregation predictions
        # =====================================================================
        if aggregated_predictions is not None:
            logger.info("Incorporating post-aggregation predictions into selection...")

            # Merge aggregated predictions with work_df
            if 'category_aggregated' in aggregated_predictions.columns:
                # Create a temporary dataframe for aggregated predictions as "synthetic source"
                agg_pred_df = aggregated_predictions[[
                    asset_id_field,
                    'category_aggregated',
                    'subcategory_aggregated',
                    'category_aggregated_confidence',
                    'category_aggregated_rule_type'
                ]].copy()

                # Rename to match work_df columns
                agg_pred_df = agg_pred_df.rename(columns={
                    'category_aggregated': 'predicted_category',
                    'subcategory_aggregated': 'predicted_subcategory',
                    'category_aggregated_confidence': 'category_confidence',
                    'category_aggregated_rule_type': 'category_rule_type'
                })

                # Add metadata
                agg_pred_df['data_source'] = 'aggregated_view'
                agg_pred_df['rule_quality'] = agg_pred_df['category_rule_type'].map(self.RULE_TYPE_QUALITY).fillna(1)
                # Boost vendor_category for specific subcategory (#161 QA-16)
                _vc_specific_agg = (
                    (agg_pred_df['category_rule_type'] == 'vendor_category') &
                    agg_pred_df['predicted_subcategory'].notna() &
                    ~agg_pred_df['predicted_subcategory'].str.lower().isin(['unknown', ''])
                )
                agg_pred_df.loc[_vc_specific_agg, 'rule_quality'] = 6
                agg_pred_df['category_specificity'] = agg_pred_df['predicted_category'].map(self.CATEGORY_SPECIFICITY).fillna(5)
                agg_pred_df.loc[agg_pred_df['predicted_category'].isna(), 'category_specificity'] = 0

                # Vectorized consensus for aggregated prediction (#45)
                # Exclude fallback predictions from consensus (#59 Item 16)
                _non_fallback_pre = work_df[work_df['category_rule_type'] != 'fallback']
                # Pre-compute match counts per (asset, category) from non-fallback pre-agg data
                _pre_agg_counts = (
                    _non_fallback_pre.groupby([asset_id_field, 'predicted_category'], dropna=True)
                    .size()
                    .reset_index(name='_match_count')
                )
                # Total non-fallback predictions per asset (denominator for agreement)
                _pre_agg_totals = (
                    _non_fallback_pre.groupby(asset_id_field)['predicted_category']
                    .count()  # count() ignores NaN — equivalent to x.dropna().shape[0]
                    .reset_index(name='_pred_total')
                )
                # Merge counts and totals into agg_pred_df
                agg_pred_df = agg_pred_df.merge(
                    _pre_agg_counts,
                    on=[asset_id_field, 'predicted_category'],
                    how='left',
                )
                agg_pred_df = agg_pred_df.merge(_pre_agg_totals, on=asset_id_field, how='left')
                # Calculate agreement: matches / total non-NaN preds
                agg_pred_df['_match_count'] = agg_pred_df['_match_count'].fillna(0)
                agg_pred_df['_pred_total'] = agg_pred_df['_pred_total'].fillna(0)
                _agreement = np.where(
                    agg_pred_df['_pred_total'] > 0,
                    agg_pred_df['_match_count'] / agg_pred_df['_pred_total'],
                    0.0,
                )
                # Range: 0.5 (no agreement) to 1.0 (full agreement)
                agg_pred_df['consensus_score'] = 0.5 + (_agreement * 0.5)
                # Clean up temporary columns
                agg_pred_df.drop(columns=['_match_count', '_pred_total'], inplace=True)

                # Calculate selection score for aggregated prediction
                # HIGHER WEIGHT for aggregated prediction since it has complete context
                # Weights: confidence (50%), consensus (30%), rule_quality (15%), specificity (5%)
                agg_pred_df['category_selection_score'] = (
                    agg_pred_df['category_confidence'] * 0.5 +
                    agg_pred_df['consensus_score'] * 0.3 +
                    agg_pred_df['rule_quality'] / 10 * 0.15 +
                    agg_pred_df['category_specificity'] / 10 * 0.05
                )

                # Penalize "unknown" subcategory (#59 Item 1)
                unknown_subcat_mask = agg_pred_df['predicted_subcategory'].str.lower() == 'unknown'
                agg_pred_df.loc[unknown_subcat_mask, 'category_selection_score'] *= 0.5

                # Handle missing categories
                agg_pred_df.loc[
                    agg_pred_df['predicted_category'].isna() |
                    (agg_pred_df['predicted_category'] == '') |
                    (agg_pred_df['predicted_category'] == 'unknown'),
                    'category_selection_score'
                ] = -1

                # Add aggregated predictions to work_df for unified selection
                # These will compete with pre-aggregation predictions
                work_df = pd.concat([work_df, agg_pred_df], ignore_index=True)

                logger.info(f"Added {len(agg_pred_df)} post-aggregation predictions to selection pool")

        # Select best category for each asset (now includes aggregated predictions if provided)
        best_category_idx = work_df.groupby(asset_id_field)['category_selection_score'].idxmax()

        # Create best category dataframe
        best_category_columns = [
            asset_id_field,
            'predicted_category',
            'predicted_subcategory',
            'category_confidence',
            'category_rule_type',
            'rule_quality',
            'category_specificity',
            'consensus_score',
            'category_selection_score',
            'data_source'  # Track which source provided best category
        ]
        best_category_columns = [c for c in best_category_columns if c in work_df.columns]

        best_category_df = work_df.loc[best_category_idx, best_category_columns].copy()

        # Rename columns to indicate they are the best selection
        rename_dict = {
            'predicted_category': 'category_best',
            'predicted_subcategory': 'subcategory_best',
            'category_confidence': 'category_confidence_best',
            'category_rule_type': 'category_rule_type_best',
            'data_source': 'category_best_source'
        }
        best_category_df = best_category_df.rename(columns=rename_dict)

        # Default empty/null subcategories to 'unknown' (#161 QA-15)
        best_category_df['subcategory_best'] = best_category_df['subcategory_best'].fillna('unknown')
        best_category_df.loc[
            best_category_df['subcategory_best'].str.strip() == '', 'subcategory_best'
        ] = 'unknown'

        # Remap invalid top-level categories to miscellaneous, preserving
        # the original value as subcategory when it was 'unknown'.
        invalid_cat_mask = ~best_category_df['category_best'].isin(self.VALID_CATEGORIES)
        if invalid_cat_mask.any():
            unknown_sub = invalid_cat_mask & (best_category_df['subcategory_best'] == 'unknown')
            best_category_df.loc[unknown_sub, 'subcategory_best'] = best_category_df.loc[unknown_sub, 'category_best']
            best_category_df.loc[invalid_cat_mask, 'category_best'] = 'miscellaneous'
            logger.warning(
                f"Remapped {invalid_cat_mask.sum()} assets with invalid top-level category to miscellaneous"
            )

        # Collect all predicted categories/subcategories into arrays (for audit trail)
        # Use vectorized approach: collect unique non-null values per asset
        cat_unique = (
            work_df[work_df['predicted_category'].notna()]
            .groupby(asset_id_field)['predicted_category']
            .apply(lambda x: list(x.unique()))
            .reset_index(name='category_all')
        )
        subcat_unique = (
            work_df[work_df['predicted_subcategory'].notna()]
            .groupby(asset_id_field)['predicted_subcategory']
            .apply(lambda x: list(x.unique()))
            .reset_index(name='subcategory_all')
        )

        # Merge with best category dataframe
        best_category_df = best_category_df.merge(cat_unique, on=asset_id_field, how='left')
        best_category_df = best_category_df.merge(subcat_unique, on=asset_id_field, how='left')

        # Fill NaN for assets with no predictions
        best_category_df['category_all'] = best_category_df['category_all'].apply(
            lambda x: x if isinstance(x, list) else []
        )
        best_category_df['subcategory_all'] = best_category_df['subcategory_all'].apply(
            lambda x: x if isinstance(x, list) else []
        )

        # Log statistics
        logger.info(f"Category Selection Statistics:")
        logger.info(f"  - Assets with category: {(best_category_df['category_best'].notna()).sum()}")
        logger.info(f"  - Average confidence: {best_category_df['category_confidence_best'].mean():.3f}")
        logger.info(f"  - Average consensus: {best_category_df['consensus_score'].mean():.3f}")
        logger.info(f"  - Average rule quality: {best_category_df['rule_quality'].mean():.1f}/10")

        # Category distribution
        category_dist = best_category_df['category_best'].value_counts()
        logger.info(f"  - Top categories: {category_dist.head(5).to_dict()}")

        # Return per-asset result with only the needed columns
        keep_columns = [
            asset_id_field,
            'category_best',
            'subcategory_best',
            'category_confidence_best',
            'category_rule_type_best',
            'category_best_source',
            'category_all',
            'subcategory_all'
        ]
        keep_columns = [c for c in keep_columns if c in best_category_df.columns]

        elapsed = (pd.Timestamp.now() - start_time).total_seconds()
        logger.info(f"Category selection completed in {elapsed:.2f}s")

        return best_category_df[keep_columns].reset_index(drop=True)

    def get_category_selection_stats(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Get statistics about category selection results.

        Args:
            df: DataFrame with category_best column

        Returns:
            Dictionary with selection statistics
        """
        stats = {
            'total_assets': len(df),
            'assets_with_category': (df['category_best'].notna()).sum() if 'category_best' in df.columns else 0,
            'unique_categories': df['category_best'].nunique() if 'category_best' in df.columns else 0,
            'category_distribution': df['category_best'].value_counts().to_dict() if 'category_best' in df.columns else {},
            'subcategory_distribution': df['subcategory_best'].value_counts().head(10).to_dict() if 'subcategory_best' in df.columns else {}
        }

        if 'category_best_source' in df.columns:
            stats['category_by_source'] = df['category_best_source'].value_counts().to_dict()

        if 'category_rule_type_best' in df.columns:
            stats['category_by_rule_type'] = df['category_rule_type_best'].value_counts().to_dict()

        return stats

    def analyze_category_conflicts(
        self,
        df: pd.DataFrame,
        asset_id_field: str = 'asset_id'
    ) -> pd.DataFrame:
        """
        Analyze assets with conflicting category predictions.

        Args:
            df: DataFrame with category_all column
            asset_id_field: Name of the asset ID column

        Returns:
            DataFrame with assets that have category conflicts
        """
        if 'category_all' not in df.columns:
            logger.warning("category_all column not found")
            return pd.DataFrame()

        # Filter to assets with multiple different categories
        conflicts = df[df['category_all'].apply(lambda x: len(x) > 1 if isinstance(x, list) else False)]

        if len(conflicts) > 0:
            logger.info(f"Found {len(conflicts)} assets with category conflicts")

            # Add conflict details
            conflicts = conflicts.copy()
            conflicts['conflict_count'] = conflicts['category_all'].apply(len)
            conflicts['categories_conflicting'] = conflicts['category_all'].apply(
                lambda x: ', '.join(x) if isinstance(x, list) else str(x)
            )
        else:
            logger.info("No category conflicts found - all assets have consistent predictions")

        return conflicts

    def predict_categories_for_aggregated_assets(
        self,
        aggregated_df: pd.DataFrame,
        category_predictor,
        asset_id_field: str = 'asset_id'
    ) -> pd.DataFrame:
        """
        Run category prediction on aggregated assets (post-aggregation prediction).

        This provides a "big picture" prediction using the complete asset view
        with all data sources combined.

        Args:
            aggregated_df: DataFrame with aggregated assets (one row per asset)
                          Should have: os_normalized_best, mac_vendor_enriched arrays,
                          manufacturer arrays, model arrays, etc.
            category_predictor: CategoryPrediction instance to use for prediction
            asset_id_field: Name of the asset ID column

        Returns:
            DataFrame with category_aggregated, subcategory_aggregated,
            category_aggregated_confidence, category_aggregated_rule_type columns
        """
        logger.info(f"Running post-aggregation category prediction for {len(aggregated_df)} assets...")

        # Create a copy for prediction
        pred_df = aggregated_df.copy()

        # Prepare data for prediction
        # Use best/primary values from aggregated asset
        prediction_input = pd.DataFrame({
            'os_normalized': pred_df.get('os_normalized_best', pred_df.get('os_normalized', None)),
            'manufacturer': pred_df.get('manufacturer', None),
            'model': pred_df.get('model', None),
            'mac_vendor_enriched': pred_df.get('mac_vendor_enriched', None),
            'hostname': pred_df.get('hostname', None),
            'vendor_category': pred_df.get('vendor_category', None),
        })

        # Handle array columns — pick best (first non-empty) value (#161 QA-12)
        for col in ['os_normalized', 'manufacturer', 'model', 'mac_vendor_enriched', 'hostname', 'vendor_category']:
            if col in prediction_input.columns:
                prediction_input[col] = prediction_input[col].apply(extract_scalar)

        # Run category prediction
        predicted = category_predictor.predict_categories(
            prediction_input,
            os_column='os_normalized',
            manufacturer_column='manufacturer',
            model_column='model',
            mac_vendor_column='mac_vendor_enriched',
            hostname_column='hostname'
        )

        # Extract predicted columns and rename for aggregated context
        result = pd.DataFrame({
            asset_id_field: pred_df[asset_id_field],
            'category_aggregated': predicted['predicted_category'],
            'subcategory_aggregated': predicted['predicted_subcategory'],
            'category_aggregated_confidence': predicted['category_confidence'],
            'category_aggregated_rule_type': predicted['category_rule_type']
        })

        logger.info(f"Post-aggregation prediction complete:")
        logger.info(f"  - Predicted {len(result)} assets")
        logger.info(f"  - Categories: {result['category_aggregated'].value_counts().to_dict()}")
        logger.info(f"  - Average confidence: {result['category_aggregated_confidence'].mean():.3f}")

        return result
