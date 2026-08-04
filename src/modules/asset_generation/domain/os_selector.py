# SPDX-License-Identifier: AGPL-3.0-or-later
"""
OS Selector - Selects the best OS for each asset from multiple data sources.

This module handles the selection of the most appropriate OS value for an asset
when multiple data sources report different OS information. Selection is based on:
1. Confidence score from normalization
2. Specificity (more detailed OS wins)
3. Recency (most recent observation)
"""

import logging
import re
from typing import Optional, Dict, Any
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

_WINDOWS_BUILD_RE = re.compile(r'\d+\.\d+\.\d{5}')


class OSSelector:
    """
    Selects the best OS for each asset from multiple data source records.
    """

    def __init__(self):
        """Initialize OS selector."""
        logger.info("OSSelector initialized")

    def select_best_os_for_assets(
        self,
        df: pd.DataFrame,
        asset_id_field: str = 'asset_id'
    ) -> pd.DataFrame:
        """
        Select the best OS for each asset based on confidence, specificity, and recency.

        This method processes the dataframe BEFORE aggregation to ensure each asset
        gets a single best OS value rather than arrays of OS values.

        Args:
            df: DataFrame with asset records (one row per data source per asset)
            asset_id_field: Name of the asset ID column

        Returns:
            DataFrame with os_normalized_best and os_enrichment_best columns added
        """
        start_time = pd.Timestamp.now()
        logger.info(f"Selecting best OS for {df[asset_id_field].nunique()} unique assets...")

        # Create a copy to avoid modifying original
        df = df.copy()

        # Extract confidence from os_enrichment JSONB
        def extract_confidence(enrichment):
            """Extract confidence score from os_enrichment."""
            if pd.isna(enrichment) or not enrichment:
                return 0.0
            if isinstance(enrichment, dict):
                return float(enrichment.get('confidence', 0.0))
            return 0.0

        df['os_confidence'] = df['os_enrichment'].apply(extract_confidence)

        # Calculate specificity (prefer cleaner OS names over detailed build numbers)
        def calculate_specificity(os_str):
            """
            Calculate specificity score - prefer cleaner OS names.

            Strategy:
            - Base score: moderate detail level is best (2-3 words = optimal)
            - Penalty for overly detailed strings (build numbers, long version strings)
            - Bonus for having version but not excessive detail
            """
            if pd.isna(os_str) or not os_str:
                return 0

            os_lower = str(os_str).lower()
            words = os_lower.split()
            word_count = len(words)

            # Optimal word count is 2-3 (or 4 for server editions)
            if word_count <= 1:
                base_score = 1  # Too generic (e.g., "linux", "windows")
            elif word_count in [2, 3]:
                base_score = 10  # Optimal (e.g., "windows 11", "ubuntu linux")
            elif word_count == 4 and 'server' in os_lower:
                base_score = 10  # Server with edition (e.g., "windows server 2022 datacenter")
            elif word_count in [4, 5]:
                base_score = 5   # Moderate detail
            else:
                base_score = 2   # Too detailed (e.g., "windows 10 10.0.19045.6456")

            # Penalty for Windows build numbers (e.g., 10.0.19045.6456)
            if 'windows' in os_lower:
                # Check for pattern like "10.0.19045.6456" (Windows build numbers)
                if _WINDOWS_BUILD_RE.search(os_lower):
                    base_score *= 0.3  # Heavy penalty for detailed builds

            # Bonus for having a clean version number
            has_version = any(char.isdigit() for char in os_lower)
            if has_version and word_count <= 3:
                base_score *= 1.2

            # Penalty for "unknown", "generic", "embedded" etc.
            low_quality_keywords = ['unknown', 'generic', 'embedded', 'firmware', 'other']
            if any(keyword in os_lower for keyword in low_quality_keywords):
                base_score *= 0.5

            return base_score

        df['os_specificity'] = df['os_normalized'].apply(calculate_specificity)

        # Handle last_time for recency scoring
        df['last_time_parsed'] = pd.to_datetime(df['last_time'], errors='coerce')

        # Calculate recency score (newer is better)
        max_time = df['last_time_parsed'].max()
        min_time = df['last_time_parsed'].min()
        if pd.notna(max_time) and pd.notna(min_time) and max_time != min_time:
            time_range = (max_time - min_time).total_seconds()
            df['os_recency'] = ((df['last_time_parsed'] - min_time).dt.total_seconds() / time_range).fillna(0)
        else:
            df['os_recency'] = 0.5  # Default if no time data

        # Calculate composite score for OS selection
        # Weights: confidence (60%), specificity (25%), recency (15%)
        # Confidence is most important, then clean OS name quality, then recency
        df['os_selection_score'] = (
            df['os_confidence'] * 0.6 +
            df['os_specificity'] / 10 * 0.25 +  # Normalize specificity to 0-1 range
            df['os_recency'] * 0.15
        )

        # Handle missing OS by giving minimal score
        df.loc[df['os_normalized'].isna() | (df['os_normalized'] == ''), 'os_selection_score'] = -1

        # Select best OS for each asset
        best_os_idx = df.groupby(asset_id_field)['os_selection_score'].idxmax()

        # Create best OS dataframe with the selected best OS
        best_os_columns = [
            asset_id_field,
            'os_normalized',
            'os_enrichment',
            'os_confidence',
            'os_specificity',
            'os_recency',
            'os_selection_score',
            'data_source'  # Track which source provided best OS
        ]

        # Add os_orig if it exists (the original value for the best selected OS)
        if 'os_orig' in df.columns:
            best_os_columns.insert(2, 'os_orig')  # Insert after os_normalized

        best_os_df = df.loc[best_os_idx, best_os_columns].copy()

        # Rename columns to indicate they are the best selection
        rename_dict = {
            'os_normalized': 'os_normalized_best',
            'os_enrichment': 'os_enrichment_best',
            'data_source': 'os_best_source'
        }
        if 'os_orig' in best_os_df.columns:
            rename_dict['os_orig'] = 'os_orig_best'

        best_os_df = best_os_df.rename(columns=rename_dict)

        # Collect all raw OS values from all data sources into 'os' array
        # Schema: os = array of all raw OS values from input data (all data sources)
        agg_dict = {}

        # If os_orig exists, use it for the 'os' array (all raw values)
        if 'os_orig' in df.columns:
            agg_dict['os_orig'] = lambda x: list(x.dropna().unique()) if len(x.dropna()) > 0 else []

        all_os_df = df.groupby(asset_id_field).agg(agg_dict).reset_index()

        # Rename os_orig array to 'os' (array of all raw OS values)
        if 'os_orig' in all_os_df.columns:
            all_os_df = all_os_df.rename(columns={'os_orig': 'os'})

        # Merge with best OS dataframe
        best_os_df = best_os_df.merge(all_os_df, on=asset_id_field, how='left')

        # Log statistics
        logger.info(f"OS Selection Statistics:")
        logger.info(f"  - Assets with OS: {(best_os_df['os_normalized_best'].notna()).sum()}")
        logger.info(f"  - Average confidence: {best_os_df['os_confidence'].mean():.3f}")
        logger.info(f"  - Average specificity: {best_os_df['os_specificity'].mean():.1f}")

        # Drop intermediate columns from best_os_df before merging
        keep_columns = [
            asset_id_field,
            'os_normalized_best',
            'os_enrichment_best',
            'os_best_source',
            'os'
        ]

        # Only keep os_orig_best if it exists (single raw value for best OS)
        if 'os_orig_best' in best_os_df.columns:
            keep_columns.append('os_orig_best')

        best_os_df = best_os_df[keep_columns]

        # Merge back with original dataframe
        result_df = df.merge(best_os_df, on=asset_id_field, how='left')

        # Drop temporary scoring columns
        columns_to_drop = [
            'os_confidence',
            'os_specificity',
            'os_recency',
            'os_selection_score',
            'last_time_parsed'
        ]
        result_df = result_df.drop(columns=columns_to_drop, errors='ignore')

        elapsed = (pd.Timestamp.now() - start_time).total_seconds()
        logger.info(f"OS selection completed in {elapsed:.2f}s")

        return result_df

    def get_os_selection_stats(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Get statistics about OS selection results.

        Args:
            df: DataFrame with os_normalized_best column

        Returns:
            Dictionary with selection statistics
        """
        stats = {
            'total_assets': len(df),
            'assets_with_os': (df['os_normalized_best'].notna()).sum() if 'os_normalized_best' in df.columns else 0,
            'unique_os_values': df['os_normalized_best'].nunique() if 'os_normalized_best' in df.columns else 0,
            'top_os_values': df['os_normalized_best'].value_counts().head(5).to_dict() if 'os_normalized_best' in df.columns else {}
        }

        if 'os_best_source' in df.columns:
            stats['os_by_source'] = df['os_best_source'].value_counts().to_dict()

        return stats