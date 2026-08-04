# SPDX-License-Identifier: AGPL-3.0-or-later
"""QA-12: Test that post-aggregation prediction picks the best value from arrays.

Bug: extract_first_value takes array[0] blindly. If the first source has
empty manufacturer/model, useful data from later sources is missed, leaving
subcategory as 'unknown' even though resolution data exists in the array.
"""
import pytest
import numpy as np
import pandas as pd
import sys
sys.path.insert(0, '.')

from src.modules.asset_generation.domain.category_selector import CategorySelector
from src.modules.asset_generation.domain.category_prediction import CategoryPrediction


@pytest.fixture
def selector():
    return CategorySelector()


@pytest.fixture
def predictor():
    return CategoryPrediction()


class TestExtractBestValue:
    """Post-aggregation prediction should skip empty/null array elements."""

    def test_skips_empty_first_element(self, selector, predictor):
        """When manufacturer[0] is empty but manufacturer[1] has data,
        the prediction should use the non-empty value."""
        aggregated_df = pd.DataFrame([{
            'asset_id': 'a1',
            'os_normalized_best': 'windows 10',
            'manufacturer': ['', 'Dell Inc'],
            'model': ['', 'Latitude 5520'],
            'mac_vendor_enriched': [None, None],
        }])

        result = selector.predict_categories_for_aggregated_assets(
            aggregated_df, predictor, asset_id_field='asset_id'
        )
        row = result[result['asset_id'] == 'a1'].iloc[0]

        # With Dell + Latitude, combined pattern should resolve to laptop
        assert row['subcategory_aggregated'] != 'unknown', (
            f"Expected specific subcategory from Dell/Latitude, "
            f"got '{row['subcategory_aggregated']}'"
        )

    def test_skips_none_first_element(self, selector, predictor):
        """None in first position should be skipped."""
        aggregated_df = pd.DataFrame([{
            'asset_id': 'a1',
            'os_normalized_best': 'windows server 2019',
            'manufacturer': [None, 'Dell Inc'],
            'model': [None, 'PowerEdge R740'],
            'mac_vendor_enriched': [None, None],
        }])

        result = selector.predict_categories_for_aggregated_assets(
            aggregated_df, predictor, asset_id_field='asset_id'
        )
        row = result[result['asset_id'] == 'a1'].iloc[0]

        # Dell + PowerEdge should resolve to physical_server
        assert row['subcategory_aggregated'] == 'physical_server', (
            f"Expected 'physical_server', got '{row['subcategory_aggregated']}'"
        )

    def test_skips_placeholder_values(self, selector, predictor):
        """Values like 'unknown', 'n/a', 'none' should be skipped."""
        aggregated_df = pd.DataFrame([{
            'asset_id': 'a1',
            'os_normalized_best': 'windows 10',
            'manufacturer': ['unknown', 'n/a', 'Lenovo'],
            'model': ['none', '', 'ThinkPad T14'],
            'mac_vendor_enriched': [None],
        }])

        result = selector.predict_categories_for_aggregated_assets(
            aggregated_df, predictor, asset_id_field='asset_id'
        )
        row = result[result['asset_id'] == 'a1'].iloc[0]

        # Lenovo + ThinkPad should resolve to laptop
        assert row['subcategory_aggregated'] == 'laptop', (
            f"Expected 'laptop', got '{row['subcategory_aggregated']}'"
        )

    def test_all_empty_returns_none_gracefully(self, selector, predictor):
        """If every element is empty/null, prediction still works (with less data)."""
        aggregated_df = pd.DataFrame([{
            'asset_id': 'a1',
            'os_normalized_best': 'windows 10',
            'manufacturer': ['', None, 'unknown'],
            'model': [None, '', 'n/a'],
            'mac_vendor_enriched': [None],
        }])

        result = selector.predict_categories_for_aggregated_assets(
            aggregated_df, predictor, asset_id_field='asset_id'
        )
        # Should not crash, should still produce a prediction
        assert len(result) == 1

    def test_scalar_value_passes_through(self, selector, predictor):
        """Non-array values should work as before."""
        aggregated_df = pd.DataFrame([{
            'asset_id': 'a1',
            'os_normalized_best': 'windows server 2019',
            'manufacturer': 'Dell Inc',
            'model': 'PowerEdge R740',
            'mac_vendor_enriched': None,
        }])

        result = selector.predict_categories_for_aggregated_assets(
            aggregated_df, predictor, asset_id_field='asset_id'
        )
        row = result[result['asset_id'] == 'a1'].iloc[0]

        assert row['subcategory_aggregated'] == 'physical_server', (
            f"Expected 'physical_server', got '{row['subcategory_aggregated']}'"
        )

    def test_numpy_array_skips_empty(self, selector, predictor):
        """numpy arrays should also skip empty values."""
        aggregated_df = pd.DataFrame([{
            'asset_id': 'a1',
            'os_normalized_best': 'windows 10',
            'manufacturer': np.array(['', 'HP Inc']),
            'model': np.array(['', 'EliteBook 840 G7']),
            'mac_vendor_enriched': np.array([None]),
        }])

        result = selector.predict_categories_for_aggregated_assets(
            aggregated_df, predictor, asset_id_field='asset_id'
        )
        row = result[result['asset_id'] == 'a1'].iloc[0]

        assert row['subcategory_aggregated'] != 'unknown', (
            f"Expected specific subcategory from HP/EliteBook, "
            f"got '{row['subcategory_aggregated']}'"
        )
