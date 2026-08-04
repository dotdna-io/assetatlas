#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Category Prediction Component for Asset Generation Pipeline.

This component wraps the pattern-based category predictor and integrates it
into the asset generation pipeline. It predicts category and subcategory
using rule-based patterns after OS normalization and MAC vendor enrichment.

Author: AssetAtlas Team
Date: 2025-10-29
"""

import logging
import pandas as pd
from typing import Optional, Dict, Any

from src.modules.asset_generation.domain.category_prediction_rules import PatternBasedCategoryPredictor

logger = logging.getLogger(__name__)


class CategoryPrediction:
    """
    Category prediction component for the asset generation pipeline.

    Predicts category and subcategory using rule-based patterns matching on:
    - os_normalized (after OS normalization)
    - manufacturer
    - model
    - mac_vendor_enriched (after MAC vendor enrichment)

    Fallback: If no rule matches -> category="miscellaneous", subcategory="unknown"
    """

    def __init__(self):
        """Initialize the category predictor."""
        self.predictor = PatternBasedCategoryPredictor()
        self.stats = {
            'total_predictions': 0,
            'successful_predictions': 0,
            'fallback_predictions': 0,
            'by_rule_type': {},
            'by_category': {}
        }
        logger.info("CategoryPrediction component initialized")

    def predict_categories(
        self,
        df: pd.DataFrame,
        os_column: str = 'os_normalized',
        manufacturer_column: str = 'manufacturer',
        model_column: str = 'model',
        mac_vendor_column: str = 'mac_vendor_enriched',
        vendor_category_column: str = 'vendor_category',
        hostname_column: str = 'hostname'
    ) -> pd.DataFrame:
        """
        Predict categories and subcategories for all records in the DataFrame.

        Args:
            df: DataFrame with enriched asset data
            os_column: Column name for normalized OS
            manufacturer_column: Column name for manufacturer
            model_column: Column name for model
            mac_vendor_column: Column name for enriched MAC vendor
            vendor_category_column: Column name for vendor-provided category

        Returns:
            DataFrame with prediction columns added:
            - predicted_category
            - predicted_subcategory
            - category_confidence
            - category_rule_type
        """
        logger.info(f"Predicting categories for {len(df)} records...")

        # Clear prediction cache from previous runs
        self.predictor.predict.cache_clear()

        # Reset stats for this run
        self.stats = {
            'total_predictions': 0,
            'successful_predictions': 0,
            'fallback_predictions': 0,
            'by_rule_type': {},
            'by_category': {}
        }

        # Extract input columns, replacing NaN with None for consistent dedup
        input_cols = [os_column, manufacturer_column, model_column, mac_vendor_column]
        # vendor_category is optional — only include if present in DataFrame
        has_vendor_category = vendor_category_column in df.columns
        if has_vendor_category:
            input_cols.append(vendor_category_column)
        inputs = df[input_cols].copy()
        for col in input_cols:
            inputs[col] = inputs[col].where(inputs[col].notna(), None)

        # Build a dedup key per row from the prediction inputs. EVERY value that
        # predict() reads from the bucket (i.e. every column in `input_cols`) MUST be
        # part of this key — otherwise rows that share the keyed fields but differ in
        # an un-keyed input collapse into one bucket and all take the first row's value
        # for it. vendor_category (Priority 6) is the optional 5th input: omit it from
        # the key and firewalls with no other signal take a neighbour's vendor_category
        # and land in miscellaneous instead of network/firewall.
        # fillna prevents NaN propagation in string concatenation (NaN + str → NaN).
        sentinel = '__NULL__'
        dedup_keys = (
            inputs[os_column].fillna(sentinel).astype(str) + '|' +
            inputs[manufacturer_column].fillna(sentinel).astype(str) + '|' +
            inputs[model_column].fillna(sentinel).astype(str) + '|' +
            inputs[mac_vendor_column].fillna(sentinel).astype(str)
        )
        if has_vendor_category:
            dedup_keys = dedup_keys + '|' + inputs[vendor_category_column].fillna(sentinel).astype(str)

        # Build a mapping from dedup key -> input tuple (input_cols, in order)
        # in a single pass (first occurrence wins)
        key_to_inputs = {}
        keys_array = dedup_keys.values
        for i in range(len(inputs)):
            key = keys_array[i]
            if key not in key_to_inputs:
                row = inputs.iloc[i]
                key_to_inputs[key] = tuple(
                    None if pd.isna(row[col]) else row[col]
                    for col in input_cols
                )

        logger.info(f"  Unique input combos: {len(key_to_inputs)} (from {len(df)} rows)")

        # Predict once per unique combo
        key_to_prediction = {}
        for key, input_vals in key_to_inputs.items():
            os_val, mfr_val, model_val, mac_val = input_vals[:4]
            vc_val = input_vals[4] if has_vendor_category else None
            prediction = self.predictor.predict(
                os=os_val,
                manufacturer=mfr_val,
                model=model_val,
                mac_vendor=mac_val,
                vendor_category=vc_val
            )
            if prediction:
                key_to_prediction[key] = prediction
            else:
                # Fallback
                key_to_prediction[key] = {
                    'category': 'miscellaneous',
                    'subcategory': 'unknown',
                    'confidence': 0.5,
                    'rule_type': 'fallback',
                }

        # Second pass: attempt hostname-based resolution on fallback rows only.
        # Hostname is NOT in the dedup key (per-device, would kill dedup optimization).
        # Instead, iterate fallback rows individually with their hostname.
        # Note: predictor.predict() has lru_cache — first-pass (hostname=None) and
        # second-pass (hostname=value) are separate cache entries. This is intentional.
        hostname_overrides = {}  # idx -> prediction (local var, no instance state)
        has_hostname = hostname_column in df.columns
        if has_hostname:
            fallback_keys = {k for k, p in key_to_prediction.items() if p['rule_type'] == 'fallback'}
            if fallback_keys:
                fallback_mask = dedup_keys.isin(fallback_keys)
                hostnames = df.loc[fallback_mask, hostname_column]
                hostnames = hostnames.where(hostnames.notna(), None)

                for idx, hn_val in hostnames.items():
                    if not hn_val or not str(hn_val).strip():
                        continue
                    key = dedup_keys.at[idx]
                    input_vals = key_to_inputs[key]
                    os_val, mfr_val, model_val, mac_val = input_vals[:4]
                    vc_val = input_vals[4] if has_vendor_category else None
                    prediction = self.predictor.predict(
                        os=os_val,
                        manufacturer=mfr_val,
                        model=model_val,
                        mac_vendor=mac_val,
                        hostname=hn_val,
                        vendor_category=vc_val,
                    )
                    if prediction:
                        hostname_overrides[idx] = prediction

                logger.info(f"  Hostname second-pass: {len(hostname_overrides)} rows resolved from fallback")

        # Map predictions back to all rows via the dedup key (bulk assignment)
        df['predicted_category'] = dedup_keys.map(lambda k: key_to_prediction[k]['category']).values
        df['predicted_subcategory'] = dedup_keys.map(lambda k: key_to_prediction[k]['subcategory']).values
        df['category_confidence'] = dedup_keys.map(lambda k: key_to_prediction[k]['confidence']).values
        df['category_rule_type'] = dedup_keys.map(lambda k: key_to_prediction[k]['rule_type']).values

        # Apply hostname second-pass overrides (per-row, not per-key)
        if hostname_overrides:
            for idx, pred in hostname_overrides.items():
                df.at[idx, 'predicted_category'] = pred['category']
                df.at[idx, 'predicted_subcategory'] = pred['subcategory']
                df.at[idx, 'category_confidence'] = pred['confidence']
                df.at[idx, 'category_rule_type'] = pred['rule_type']

        # Compute stats from final DataFrame columns (after hostname overrides)
        self.stats['total_predictions'] = len(df)
        self.stats['fallback_predictions'] = int((df['category_rule_type'] == 'fallback').sum())
        self.stats['successful_predictions'] = self.stats['total_predictions'] - self.stats['fallback_predictions']

        for rt, count in df['category_rule_type'].value_counts().items():
            self.stats['by_rule_type'][rt] = int(count)
        for cat, count in df['predicted_category'].value_counts().items():
            self.stats['by_category'][cat] = int(count)

        # Log statistics
        logger.info(f"Category prediction complete:")
        logger.info(f"  Total predictions: {self.stats['total_predictions']}")
        logger.info(f"  Successful (rule matched): {self.stats['successful_predictions']}")
        logger.info(f"  Fallback (no match): {self.stats['fallback_predictions']}")
        logger.info(f"  By category: {self.stats['by_category']}")
        logger.info(f"  By rule type: {self.stats['by_rule_type']}")

        return df

    def get_stats(self) -> Dict[str, Any]:
        """
        Get prediction statistics.

        Returns:
            Dictionary with prediction statistics
        """
        return self.stats.copy()

    def get_pattern_stats(self) -> Dict[str, int]:
        """
        Get statistics about loaded patterns.

        Returns:
            Dictionary with pattern counts
        """
        return self.predictor.get_stats()


# Example usage
if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Initialize predictor
    predictor = CategoryPrediction()

    # Show pattern statistics
    pattern_stats = predictor.get_pattern_stats()
    print("\nPattern Statistics:")
    for pattern_type, count in pattern_stats.items():
        print(f"  {pattern_type}: {count}")

    # Test with sample data
    test_data = pd.DataFrame([
        {
            'os_normalized': 'Windows Server 2022',
            'manufacturer': 'Dell Technologies Inc.',
            'model': 'PowerEdge R750',
            'mac_vendor_enriched': 'Dell'
        },
        {
            'os_normalized': 'Windows 11',
            'manufacturer': 'HP Inc.',
            'model': 'EliteBook 850',
            'mac_vendor_enriched': 'HP'
        },
        {
            'os_normalized': 'iOS 17.2',
            'manufacturer': 'Apple Inc.',
            'model': 'iPhone 15 Pro',
            'mac_vendor_enriched': 'Apple'
        },
        {
            'os_normalized': 'Cisco IOS-XE 17.9.1',
            'manufacturer': 'Cisco Systems',
            'model': 'Catalyst 9300',
            'mac_vendor_enriched': 'Cisco'
        },
        {
            'os_normalized': None,
            'manufacturer': 'Unknown Manufacturer',
            'model': 'Unknown Model',
            'mac_vendor_enriched': None
        }
    ])

    print("\n" + "="*80)
    print("Testing Category Prediction")
    print("="*80)

    # Predict
    result_df = predictor.predict_categories(test_data)

    # Show results
    print("\nPrediction Results:")
    print(result_df[['os_normalized', 'manufacturer', 'predicted_category', 'predicted_subcategory', 'category_confidence', 'category_rule_type']].to_string())

    # Show stats
    print("\n" + "="*80)
    print("Prediction Statistics:")
    print("="*80)
    stats = predictor.get_stats()
    for key, value in stats.items():
        print(f"{key}: {value}")
