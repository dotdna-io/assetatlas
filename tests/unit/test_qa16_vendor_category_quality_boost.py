# SPDX-License-Identifier: AGPL-3.0-or-later
"""QA-16: Test vendor_category rule quality boost for specific subcategories.

Bug: vendor_category has rule quality 3/10, same as model_pattern. But
vendor_category is highly accurate for subcategory differentiation. When it
provides a specific (non-unknown) subcategory, its quality should be boosted
to 6/10 so it can compete more fairly with higher-quality rules.
"""
import pytest
import pandas as pd
import sys
sys.path.insert(0, '.')

from src.modules.asset_generation.domain.category_selector import CategorySelector


@pytest.fixture
def selector():
    return CategorySelector()


class TestVendorCategoryQualityBoost:
    """vendor_category with specific subcategory should get boosted rule_quality."""

    def test_vendor_category_specific_subcat_beats_manufacturer_pattern(self, selector):
        """vendor_category (base 3, boosted to 6) with specific subcategory should
        beat manufacturer_pattern (4) with specific subcategory.

        Without the boost, manufacturer_pattern wins due to higher rule_quality.
        With the boost, vendor_category's quality of 6 > 4 tips the balance.
        """
        df = pd.DataFrame([
            {
                'asset_id': 'a1',
                'predicted_category': 'workstation',
                'predicted_subcategory': 'laptop',
                'category_confidence': 0.7,
                'category_rule_type': 'vendor_category',
                'data_source': 'source_a',
            },
            {
                'asset_id': 'a1',
                'predicted_category': 'workstation',
                'predicted_subcategory': 'desktop',
                'category_confidence': 0.7,
                'category_rule_type': 'manufacturer_pattern',
                'data_source': 'source_b',
            },
        ])

        result = selector.select_best_category_for_assets(df, asset_id_field='asset_id')
        row = result[result['asset_id'] == 'a1'].iloc[0]

        # With boost: vendor_category quality=6 > manufacturer_pattern quality=4
        # Both have specific subcategories so the 0.5x penalty doesn't apply to either
        # The ONLY differentiator is rule_quality (confidence, consensus, specificity are equal)
        assert row['category_rule_type_best'] == 'vendor_category', (
            f"Expected vendor_category to win with boosted quality, "
            f"got rule_type='{row['category_rule_type_best']}', subcat='{row['subcategory_best']}'"
        )

    def test_vendor_category_unknown_subcat_loses_to_manufacturer_pattern(self, selector):
        """vendor_category with unknown subcategory should NOT be boosted —
        manufacturer_pattern (4) should still beat unboosted vendor_category (3)."""
        df = pd.DataFrame([
            {
                'asset_id': 'a1',
                'predicted_category': 'workstation',
                'predicted_subcategory': 'unknown',
                'category_confidence': 0.7,
                'category_rule_type': 'vendor_category',
                'data_source': 'source_a',
            },
            {
                'asset_id': 'a1',
                'predicted_category': 'workstation',
                'predicted_subcategory': 'desktop',
                'category_confidence': 0.7,
                'category_rule_type': 'manufacturer_pattern',
                'data_source': 'source_b',
            },
        ])

        result = selector.select_best_category_for_assets(df, asset_id_field='asset_id')
        row = result[result['asset_id'] == 'a1'].iloc[0]

        # vendor_category stays at 3 (no boost), manufacturer_pattern is 4
        # Plus unknown subcat gets 0.5x penalty → manufacturer_pattern wins easily
        assert row['category_rule_type_best'] == 'manufacturer_pattern', (
            f"Expected manufacturer_pattern to win over unboosted vendor_category, "
            f"got rule_type='{row['category_rule_type_best']}'"
        )
