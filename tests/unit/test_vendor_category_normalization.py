# SPDX-License-Identifier: AGPL-3.0-or-later
"""Case-insensitive vendor_category matching with expanded vocabulary.

Verify that VENDOR_CATEGORY_MAP lookups are case-insensitive and that
new Armis, ServiceNow, and common-variant entries resolve correctly.
"""
import pytest

from src.modules.asset_generation.domain.category_prediction_rules import PatternBasedCategoryPredictor


@pytest.fixture
def rules():
    return PatternBasedCategoryPredictor()


class TestVendorCategoryCaseInsensitive:
    """Lookup should be case-insensitive for all VENDOR_CATEGORY_MAP entries."""

    @pytest.mark.parametrize("vendor_category, expected_cat, expected_subcat", [
        # Original entry, various casings
        ("Laptop", "workstation", "laptop"),
        ("LAPTOP", "workstation", "laptop"),
        ("laptop", "workstation", "laptop"),
        # Armis vocabulary
        ("Personal Computers", "workstation", "unknown"),
        ("PLCs", "ot", "plc"),
        ("plcs", "ot", "plc"),
        # ServiceNow sys_class_name
        ("cmdb_ci_ip_switch", "network", "switch"),
        ("CMDB_CI_IP_SWITCH", "network", "switch"),
        # Short codes
        ("ap", "network", "access_point"),
        ("AP", "network", "access_point"),
        # Mixed case
        ("VoIPs", "miscellaneous", "voip"),
        ("VoIP", "miscellaneous", "voip"),
    ])
    def test_case_insensitive_lookup(self, rules, vendor_category, expected_cat, expected_subcat):
        """With no other signals, vendor_category alone (Priority 6) should match regardless of case."""
        result = rules.predict(
            os="",
            manufacturer="",
            model="",
            mac_vendor="",
            vendor_category=vendor_category,
        )
        assert result is not None, f"vendor_category='{vendor_category}' returned None"
        assert result["category"] == expected_cat, (
            f"vendor_category='{vendor_category}': expected category '{expected_cat}', got '{result['category']}'"
        )
        assert result["subcategory"] == expected_subcat, (
            f"vendor_category='{vendor_category}': expected subcategory '{expected_subcat}', got '{result['subcategory']}'"
        )
        assert result["rule_type"] == "vendor_category"


class TestExpandedVocabularyEntries:
    """Spot-check that new vocabulary entries are present and correct."""

    @pytest.mark.parametrize("vendor_category, expected_cat, expected_subcat", [
        # OT device types
        ("hmi panels", "ot", "hmi"),
        ("scada servers", "ot", "scada_server"),
        ("ac drives", "ot", "ac_drives"),
        ("servo drives", "ot", "servo_drives"),
        ("controllers", "ot", "controller"),
        ("historians", "ot", "historian"),
        # Juniper Mist
        ("switch", "network", "switch"),
        # ServiceNow
        ("cmdb_ci_computer", "workstation", "unknown"),
        ("cmdb_ci_printer", "miscellaneous", "printer"),
        ("cmdb_ci_ip_router", "network", "router"),
        ("cmdb_ci_ip_firewall", "network", "firewall"),
        # Common variants
        ("thin clients", "workstation", "thin_client"),
        ("ups", "miscellaneous", "ups"),
        ("hypervisor", "server", "hypervisor"),
        ("virtual machines", "server", "virtual_server"),
    ])
    def test_expanded_entry_prediction(self, rules, vendor_category, expected_cat, expected_subcat):
        result = rules.predict(
            os="",
            manufacturer="",
            model="",
            mac_vendor="",
            vendor_category=vendor_category,
        )
        assert result is not None, f"vendor_category='{vendor_category}' returned None"
        assert result["category"] == expected_cat
        assert result["subcategory"] == expected_subcat
        assert result["rule_type"] == "vendor_category"


class TestAllKeysAreLowercase:
    """Guard: every key in VENDOR_CATEGORY_MAP must be lowercase."""

    def test_map_keys_lowercase(self, rules):
        for key in rules.VENDOR_CATEGORY_MAP:
            assert key == key.lower(), f"VENDOR_CATEGORY_MAP key '{key}' is not lowercase"
