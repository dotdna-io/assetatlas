# SPDX-License-Identifier: AGPL-3.0-or-later
"""QA-15: Standardize subcategory taxonomy.

1. virtual_machine → virtual_server (synonymous, standardize to majority value)
2. Empty subcategories should default to 'unknown'
"""
import pytest
import pandas as pd
import sys
sys.path.insert(0, '.')

from src.modules.asset_generation.domain.category_prediction_rules import PatternBasedCategoryPredictor
from src.modules.asset_generation.domain.category_selector import CategorySelector


@pytest.fixture
def rules():
    return PatternBasedCategoryPredictor()


@pytest.fixture
def selector():
    return CategorySelector()


class TestVirtualMachineStandardization:
    """VENDOR_CATEGORY_MAP should use 'virtual_server' not 'virtual_machine'."""

    def test_vmware_guest_maps_to_virtual_server(self, rules):
        # Use no OS so vendor_category is the primary signal for subcategory
        result = rules.predict(
            os='',
            manufacturer='',
            model='',
            mac_vendor='',
            vendor_category='vmware guest'
        )
        assert result['subcategory'] == 'virtual_server', (
            f"Expected 'virtual_server', got '{result['subcategory']}'"
        )

    def test_hyperv_guest_maps_to_virtual_server(self, rules):
        result = rules.predict(
            os='',
            manufacturer='',
            model='',
            mac_vendor='',
            vendor_category='hyper-v guest'
        )
        assert result['subcategory'] == 'virtual_server', (
            f"Expected 'virtual_server', got '{result['subcategory']}'"
        )

    def test_vendor_category_map_has_no_virtual_machine(self, rules):
        """No entry in VENDOR_CATEGORY_MAP should produce 'virtual_machine'."""
        for key, (cat, subcat, conf) in rules.VENDOR_CATEGORY_MAP.items():
            assert subcat != 'virtual_machine', (
                f"VENDOR_CATEGORY_MAP['{key}'] still uses 'virtual_machine'"
            )


class TestEmptySubcategoryDefault:
    """Empty or null subcategories should default to 'unknown' after selection."""

    def test_null_subcategory_becomes_unknown(self, selector):
        df = pd.DataFrame([
            {
                'asset_id': 'a1',
                'predicted_category': 'ot',
                'predicted_subcategory': None,
                'category_confidence': 0.7,
                'category_rule_type': 'os_pattern',
                'data_source': 'source_a',
            },
        ])

        result = selector.select_best_category_for_assets(df, asset_id_field='asset_id')
        row = result[result['asset_id'] == 'a1'].iloc[0]
        assert row['subcategory_best'] == 'unknown', (
            f"Expected 'unknown', got '{row['subcategory_best']}'"
        )

    def test_empty_string_subcategory_becomes_unknown(self, selector):
        df = pd.DataFrame([
            {
                'asset_id': 'a1',
                'predicted_category': 'iot',
                'predicted_subcategory': '',
                'category_confidence': 0.7,
                'category_rule_type': 'os_pattern',
                'data_source': 'source_a',
            },
        ])

        result = selector.select_best_category_for_assets(df, asset_id_field='asset_id')
        row = result[result['asset_id'] == 'a1'].iloc[0]
        assert row['subcategory_best'] == 'unknown', (
            f"Expected 'unknown', got '{row['subcategory_best']}'"
        )
