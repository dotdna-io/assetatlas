# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for manufacturer name normalization."""

import pytest

from src.modules.asset_generation.domain.manufacturer_normalizer import (
    normalize_manufacturer,
    normalize_manufacturer_list,
)


class TestNormalizeManufacturer:
    """Tests for normalize_manufacturer()."""

    @pytest.mark.parametrize("input_name,expected", [
        ("Dell Inc.", "Dell"),
        ("Dell Inc", "Dell"),
        ("Dell Technologies", "Dell"),
        ("Hewlett Packard", "HP"),
        ("HP Inc.", "HP"),
        ("hewlett-packard", "HP"),
        ("Hewlett Packard Enterprise", "HPE"),
        ("Cisco Systems", "Cisco"),
        ("Cisco Systems, Inc.", "Cisco"),
        ("VMware, Inc.", "VMware"),
        ("Microsoft Corporation", "Microsoft"),
        ("Apple Inc.", "Apple"),
        ("Samsung Electronics", "Samsung"),
        ("Intel Corporation", "Intel"),
        ("Juniper", "Juniper Networks"),
        ("Palo Alto", "Palo Alto Networks"),
        ("Allen-Bradley", "Rockwell Automation"),
        ("ASUSTeK Computer Inc.", "ASUSTeK"),
        ("ASUS", "ASUSTeK"),
        ("MSI", "Micro-Star"),
        ("Micro-Star Intl", "Micro-Star"),
    ])
    def test_known_aliases(self, input_name, expected):
        assert normalize_manufacturer(input_name) == expected

    @pytest.mark.parametrize("input_name,expected", [
        ("DELL", "Dell"),
        ("dell", "Dell"),
        ("hp", "HP"),
        ("HP", "HP"),
        ("cisco", "Cisco"),
        ("CISCO SYSTEMS", "Cisco"),
        ("microsoft", "Microsoft"),
        ("APPLE", "Apple"),
    ])
    def test_case_insensitivity(self, input_name, expected):
        assert normalize_manufacturer(input_name) == expected

    def test_unknown_manufacturer_title_cased(self):
        assert normalize_manufacturer("acme widgets") == "Acme Widgets"
        assert normalize_manufacturer("SOME VENDOR") == "Some Vendor"

    def test_unknown_manufacturer_preserves_single_word(self):
        assert normalize_manufacturer("Fortinet") == "Fortinet"

    @pytest.mark.parametrize("input_name", [None, "", "  ", "   "])
    def test_empty_or_none_returns_none(self, input_name):
        assert normalize_manufacturer(input_name) is None

    def test_whitespace_stripped(self):
        assert normalize_manufacturer("  Dell Inc.  ") == "Dell"
        assert normalize_manufacturer(" hp ") == "HP"


class TestNormalizeManufacturerList:
    """Tests for normalize_manufacturer_list()."""

    def test_deduplicates_after_normalization(self):
        result = normalize_manufacturer_list(["Dell", "Dell Inc.", "DELL"])
        assert result == ["Dell"]

    def test_deduplicates_hp_variants(self):
        result = normalize_manufacturer_list(["HP", "Hewlett Packard", "HP Inc."])
        assert result == ["HP"]

    def test_preserves_order(self):
        result = normalize_manufacturer_list(["Cisco Systems", "Dell Inc.", "Apple"])
        assert result == ["Cisco", "Dell", "Apple"]

    def test_mixed_known_and_unknown(self):
        result = normalize_manufacturer_list(["Dell Inc.", "Acme Corp"])
        assert result == ["Dell", "Acme Corp"]

    def test_empty_list(self):
        assert normalize_manufacturer_list([]) == []

    def test_none_input(self):
        assert normalize_manufacturer_list(None) == []

    def test_filters_empty_strings(self):
        result = normalize_manufacturer_list(["Dell", "", "  ", "HP"])
        assert result == ["Dell", "HP"]

    def test_single_item(self):
        result = normalize_manufacturer_list(["Dell Inc."])
        assert result == ["Dell"]
