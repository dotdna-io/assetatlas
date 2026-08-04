# SPDX-License-Identifier: AGPL-3.0-or-later
"""#274: Add missing vendor category mappings.

13 observed vendor_category values had no entry in VENDOR_CATEGORY_MAP,
causing ~4,400 provider records to miss the vendor_category signal.
"""
import pytest

from src.modules.asset_generation.domain.category_prediction_rules import PatternBasedCategoryPredictor


@pytest.fixture
def rules():
    return PatternBasedCategoryPredictor()


class TestNewVendorCategoryMappings:
    """Each new mapping should produce the correct category/subcategory via Priority 6."""

    @pytest.mark.parametrize("vendor_category, expected_cat, expected_subcat", [
        ('audio visual', 'miscellaneous', 'monitor'),
        ('Audio Visual', 'miscellaneous', 'monitor'),
        ('AUDIO VISUAL', 'miscellaneous', 'monitor'),
        ('network', 'network', 'unknown'),
        ('mobile', 'mobile', 'phone'),
        ('smart office', 'iot', 'smart_device'),
        ('smart watch', 'mobile', 'wearable'),
        ('iot', 'iot', 'unknown'),
        ('storage', 'server', 'storage'),
        ('ip phone', 'miscellaneous', 'voip'),
        ('security', 'iot', 'security_camera'),
        ('gaming console', 'miscellaneous', 'unknown'),
        ('remote access controller', 'server', 'management'),
        ('firewall', 'network', 'firewall'),
        ('embedded', 'iot', 'embedded_pc'),
    ])
    def test_mapping_produces_correct_prediction(self, rules, vendor_category, expected_cat, expected_subcat):
        """With no other signals, vendor_category alone (Priority 6) should predict correctly."""
        result = rules.predict(
            os='',
            manufacturer='',
            model='',
            mac_vendor='',
            vendor_category=vendor_category,
        )
        assert result is not None, f"vendor_category='{vendor_category}' returned None"
        assert result['category'] == expected_cat, (
            f"vendor_category='{vendor_category}': expected category '{expected_cat}', got '{result['category']}'"
        )
        assert result['subcategory'] == expected_subcat, (
            f"vendor_category='{vendor_category}': expected subcategory '{expected_subcat}', got '{result['subcategory']}'"
        )
        assert result['rule_type'] == 'vendor_category'


class TestAmbiguousValuesRemainUnmapped:
    """OS-based and generic vendor_category values should NOT be in the map."""

    @pytest.mark.parametrize("vendor_category", [
        'Windows', 'Workstation', 'Unknown', 'Linux',
    ])
    def test_ambiguous_value_not_in_map(self, rules, vendor_category):
        assert vendor_category.strip().lower() not in rules.VENDOR_CATEGORY_MAP, (
            f"'{vendor_category}' should NOT be in VENDOR_CATEGORY_MAP (too ambiguous)"
        )
