# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for case-insensitive component matching in asset identification.

This test suite verifies that component identifiers (hostnames, FQDNs, MACs, SNs)
are treated as case-insensitive to prevent duplicate assets from being created
for the same device with different casing.
"""

import pytest
from unittest.mock import Mock

from src.modules.asset_generation.domain.asset_identifier import AssetIdentifier
from src.modules.asset_generation.domain.asset_validator import AssetValidator
import pandas as pd


class TestComponentNormalization:
    """Test component normalization in AssetIdentifier."""

    def test_normalize_component_lowercase(self):
        """Test that components are normalized to lowercase."""
        assert AssetIdentifier.normalize_component("SERVER01") == "server01"
        assert AssetIdentifier.normalize_component("Server01") == "server01"
        assert AssetIdentifier.normalize_component("server01") == "server01"

    def test_normalize_component_with_special_chars(self):
        """Test normalization preserves special characters."""
        assert AssetIdentifier.normalize_component("SERVER-01") == "server-01"
        assert AssetIdentifier.normalize_component("SERVER.EXAMPLE.COM") == "server.example.com"
        assert AssetIdentifier.normalize_component("00:11:22:AA:BB:CC") == "00:11:22:aa:bb:cc"

    def test_normalize_component_edge_cases(self):
        """Test normalization handles edge cases."""
        assert AssetIdentifier.normalize_component("") == ""
        assert AssetIdentifier.normalize_component(None) is None
        assert AssetIdentifier.normalize_component("123") == "123"


class TestCaseInsensitiveGrouping:
    """Test case-insensitive component grouping."""

    def test_uppercase_and_lowercase_same_asset(self):
        """Test that uppercase and lowercase variants create the same asset."""
        identifier = AssetIdentifier()

        # Group components with uppercase hostname
        components_upper = ["SERVER01", "192.168.1.1", "sn:ABC123"]
        asset_id_1 = identifier.group_components(components_upper)

        # Group components with lowercase hostname
        components_lower = ["server01", "192.168.1.2", "sn:ABC123"]
        asset_id_2 = identifier.group_components(components_lower)

        # Should be the same asset because hostname matches (case-insensitive)
        # and SN matches
        assert asset_id_1 == asset_id_2, "Case variants should match to same asset"

    def test_mixed_case_variants_single_asset(self):
        """Test that multiple case variants are treated as one component."""
        identifier = AssetIdentifier()

        # Create multiple records with different casing
        components_1 = ["SERVER01"]
        components_2 = ["Server01"]
        components_3 = ["server01"]

        asset_id_1 = identifier.group_components(components_1)
        asset_id_2 = identifier.group_components(components_2)
        asset_id_3 = identifier.group_components(components_3)

        # All should map to the same asset
        assert asset_id_1 == asset_id_2 == asset_id_3

        # Verify only one component in mappings
        mappings = identifier.get_all_mappings()
        server01_mappings = [comp for comp in mappings.keys() if "server01" in comp.lower()]
        assert len(server01_mappings) == 1, "Should have only one mapping for case variants"
        assert server01_mappings[0] == "server01", "Mapping should be lowercase"

    def test_case_insensitive_fqdn(self):
        """Test FQDN case insensitivity."""
        identifier = AssetIdentifier()

        components_1 = ["SERVER01.EXAMPLE.COM"]
        components_2 = ["server01.example.com"]
        components_3 = ["Server01.Example.Com"]

        asset_id_1 = identifier.group_components(components_1)
        asset_id_2 = identifier.group_components(components_2)
        asset_id_3 = identifier.group_components(components_3)

        assert asset_id_1 == asset_id_2 == asset_id_3

    def test_case_insensitive_mac(self):
        """Test MAC address case insensitivity."""
        identifier = AssetIdentifier()

        components_1 = ["server01", "00:11:22:AA:BB:CC"]
        components_2 = ["server01", "00:11:22:aa:bb:cc"]
        components_3 = ["server01", "00:11:22:Aa:Bb:Cc"]

        asset_id_1 = identifier.group_components(components_1)
        asset_id_2 = identifier.group_components(components_2)
        asset_id_3 = identifier.group_components(components_3)

        # All should map to same asset
        assert asset_id_1 == asset_id_2 == asset_id_3

        # Verify MAC is lowercase in mappings
        mappings = identifier.get_all_mappings()
        assert "00:11:22:aa:bb:cc" in mappings
        assert "00:11:22:AA:BB:CC" not in mappings

    def test_case_insensitive_serial_number(self):
        """Test serial number case insensitivity."""
        identifier = AssetIdentifier()

        components_1 = ["server01", "sn:ABC123XYZ"]
        components_2 = ["server02", "sn:abc123xyz"]
        components_3 = ["server03", "sn:Abc123Xyz"]

        asset_id_1 = identifier.group_components(components_1)
        asset_id_2 = identifier.group_components(components_2)
        asset_id_3 = identifier.group_components(components_3)

        # All should map to same asset (same SN)
        assert asset_id_1 == asset_id_2 == asset_id_3

        # Verify SN is lowercase in mappings
        mappings = identifier.get_all_mappings()
        assert "sn:abc123xyz" in mappings
        assert "sn:ABC123XYZ" not in mappings


class TestCaseInsensitiveLoading:
    """Test case-insensitive loading of existing mappings."""

    def test_load_mixed_case_mappings(self):
        """Test loading mappings with mixed case normalizes them."""
        identifier = AssetIdentifier()

        # Create mappings with mixed case
        mixed_case_mappings = {
            "SERVER01": "asset-111",
            "server02": "asset-222",
            "SERVER03.EXAMPLE.COM": "asset-333",
            "00:11:22:AA:BB:CC": "asset-444"
        }

        identifier.load_existing_mappings(mixed_case_mappings)

        # All should be normalized to lowercase
        mappings = identifier.get_all_mappings()
        assert "server01" in mappings
        assert "SERVER01" not in mappings
        assert "server02" in mappings
        assert "server03.example.com" in mappings
        assert "SERVER03.EXAMPLE.COM" not in mappings
        assert "00:11:22:aa:bb:cc" in mappings
        assert "00:11:22:AA:BB:CC" not in mappings

    def test_case_insensitive_lookup(self):
        """Test that lookups are case-insensitive."""
        identifier = AssetIdentifier()

        # Add component in lowercase
        components = ["server01"]
        asset_id = identifier.group_components(components)

        # Lookup with different casing should return same asset ID
        assert identifier.get_asset_id_for_component("SERVER01") == asset_id
        assert identifier.get_asset_id_for_component("Server01") == asset_id
        assert identifier.get_asset_id_for_component("server01") == asset_id


class TestAssetValidatorNormalization:
    """Test case normalization in AssetValidator."""

    def test_asset_field_creation_normalizes_case(self):
        """Test that create_asset_field normalizes components to lowercase."""
        validator = AssetValidator()

        # Create DataFrame with mixed case
        df = pd.DataFrame({
            'hostname': ['SERVER01', 'server02', 'Server03'],
            'fqdn': ['SERVER01.EXAMPLE.COM', 'server02.example.com', None],
            'ip': ['192.168.1.1', '192.168.1.2', '192.168.1.3'],
            'mac': ['00:11:22:AA:BB:CC', '00:11:22:DD:EE:FF', None],
            'sn': ['ABC123', 'XYZ789', None],
            'data_source': ['source1', 'source2', 'source3']
        })

        # Create asset field
        df_with_asset = validator.create_asset_field(df)

        # Verify components are lowercase in asset field
        asset_field_1 = df_with_asset.iloc[0]['asset']
        assert 'server01' in asset_field_1.lower()
        assert 'SERVER01' not in asset_field_1
        assert 'server01.example.com' in asset_field_1.lower()
        assert '00:11:22:aa:bb:cc' in asset_field_1.lower()
        assert 'sn:abc123' in asset_field_1.lower()


class TestCaseInsensitiveRegression:
    """Regression tests for the specific user-reported case."""

    def test_ldzl05azp_case_variants(self):
        """
        Test the specific case reported by user:
        LDZL05AZP and ldzl05azp should be treated as the same component.
        """
        identifier = AssetIdentifier()

        # Create record with uppercase
        components_upper = ["LDZL05AZP", "10.0.0.1"]
        asset_id_upper = identifier.group_components(components_upper)

        # Create record with lowercase
        components_lower = ["ldzl05azp", "10.0.0.2"]
        asset_id_lower = identifier.group_components(components_lower)

        # Should be same asset (same hostname)
        assert asset_id_upper == asset_id_lower

        # Verify only one mapping for hostname exists
        mappings = identifier.get_all_mappings()
        ldzl_mappings = [comp for comp in mappings.keys() if 'ldzl05azp' in comp.lower()]
        assert len(ldzl_mappings) == 1, "Should have only one mapping for ldzl05azp"
        assert ldzl_mappings[0] == "ldzl05azp", "Mapping should be lowercase"

    def test_no_duplicate_assets_from_case_variants(self):
        """Test that case variants don't create duplicate assets."""
        identifier = AssetIdentifier()

        # Simulate data coming in with different casing over time
        records = [
            ["SERVER01", "192.168.1.1", "sn:ABC123"],
            ["Server01", "192.168.1.1", "sn:ABC123"],
            ["server01", "192.168.1.1", "sn:ABC123"],
            ["SERVER01", "192.168.1.2", "sn:ABC123"],  # Different IP, same server
        ]

        asset_ids = set()
        for components in records:
            asset_id = identifier.group_components(components)
            asset_ids.add(asset_id)

        # Should all map to single asset
        assert len(asset_ids) == 1, "Case variants should not create duplicate assets"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
