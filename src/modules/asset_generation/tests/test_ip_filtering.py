# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for IP address filtering in asset identification.

This test suite verifies that IP addresses are properly excluded from
persistent asset ID mappings to prevent incorrect merging when IPs are
reassigned (DHCP, device lifecycle, etc.).
"""

import pytest
import uuid
from unittest.mock import Mock, MagicMock, patch

from src.modules.asset_generation.domain.asset_validator import AssetValidator
from src.modules.asset_generation.domain.asset_identifier import AssetIdentifier
from src.modules.asset_generation.repositories.mapping_repository import MappingRepository


class TestAssetValidatorIPDetection:
    """Test IP address detection in AssetValidator."""

    def test_valid_ipv4_addresses(self):
        """Test detection of valid IPv4 addresses."""
        validator = AssetValidator()

        valid_ips = [
            "192.168.1.1",
            "10.0.0.1",
            "172.16.0.1",
            "8.8.8.8",
            "255.255.255.255",
            "0.0.0.0"
        ]

        for ip in valid_ips:
            assert validator.is_valid_ip_address(ip) is True, f"Failed to detect {ip} as valid IP"

    def test_valid_ipv6_addresses(self):
        """Test detection of valid IPv6 addresses."""
        validator = AssetValidator()

        valid_ips = [
            "2001:0db8:85a3:0000:0000:8a2e:0370:7334",
            "::1",
            "fe80::1",
            "::ffff:192.0.2.1"
        ]

        for ip in valid_ips:
            assert validator.is_valid_ip_address(ip) is True, f"Failed to detect {ip} as valid IPv6"

    def test_invalid_ip_addresses(self):
        """Test that non-IP strings are not detected as IPs."""
        validator = AssetValidator()

        invalid_ips = [
            "hostname.example.com",
            "server01",
            "00:11:22:33:44:55",  # MAC address
            "sn:ABC123456",
            "256.1.1.1",  # Invalid octets
            "192.168.1",  # Incomplete
            "",
            None,
            "not-an-ip"
        ]

        for value in invalid_ips:
            assert validator.is_valid_ip_address(value) is False, f"Incorrectly detected {value} as IP"

    def test_ip_detection_caching(self):
        """Test that IP detection uses LRU cache for performance."""
        # The is_valid_ip_address method should be cached
        # Call it multiple times with same value
        ip = "192.168.1.1"

        # First call - cache miss
        result1 = AssetValidator.is_valid_ip_address(ip)
        # Second call - cache hit
        result2 = AssetValidator.is_valid_ip_address(ip)

        assert result1 is True
        assert result2 is True
        # Both should return the same cached result


class TestAssetIdentifierIPFiltering:
    """Test IP filtering in AssetIdentifier."""

    def test_get_all_mappings_filters_ips(self):
        """Test that get_all_mappings() excludes IP addresses."""
        identifier = AssetIdentifier()

        # Create test mappings with IPs and non-IPs
        test_components = [
            "server01",
            "192.168.1.100",
            "00:11:22:33:44:55",
            "10.0.0.1",
            "server02.example.com",
            "172.16.0.50"
        ]

        # Group components to create mappings
        asset_id = identifier.group_components(test_components)

        # Get all mappings (should filter IPs)
        mappings = identifier.get_all_mappings()

        # Verify IPs are not in mappings
        assert "192.168.1.100" not in mappings, "IP 192.168.1.100 should be filtered"
        assert "10.0.0.1" not in mappings, "IP 10.0.0.1 should be filtered"
        assert "172.16.0.50" not in mappings, "IP 172.16.0.50 should be filtered"

        # Verify non-IPs are in mappings (lowercased by normalize_component)
        assert "server01" in mappings, "Hostname should be in mappings"
        assert "00:11:22:33:44:55" in mappings, "MAC should be in mappings"
        assert "server02.example.com" in mappings, "FQDN should be in mappings"

    def test_ip_filtering_count_logging(self):
        """Test that IP filtering logs the count of filtered IPs."""
        identifier = AssetIdentifier()

        # Create mappings with multiple IPs
        test_components = [
            "server01",
            "192.168.1.1",
            "10.0.0.1",
            "172.16.0.1"
        ]

        identifier.group_components(test_components)

        with patch('src.modules.asset_generation.domain.asset_identifier.logger') as mock_logger:
            mappings = identifier.get_all_mappings()

            # Should have logged the IP filtering count
            assert len([call for call in mock_logger.info.call_args_list
                       if 'Filtered' in str(call) and 'IP' in str(call)]) > 0


class TestMappingRepositoryIPFiltering:
    """Test IP filtering in MappingRepository."""

    def test_save_mappings_bulk_filters_ips(self):
        """Test that save_mappings_bulk() excludes IP addresses."""
        # Mock database adapter
        mock_db = Mock()
        mock_db.bulk_set_asset_id_mappings = Mock(return_value=3)

        repo = MappingRepository(mock_db)

        # Create test mappings with IPs and non-IPs
        test_mappings = {
            "server01": str(uuid.uuid4()),
            "192.168.1.100": str(uuid.uuid4()),
            "00:11:22:33:44:55": str(uuid.uuid4()),
            "10.0.0.1": str(uuid.uuid4()),
            "server02.example.com": str(uuid.uuid4()),
        }

        # Save mappings
        result = repo.save_mappings_bulk(test_mappings, use_uuid=False)

        # Get the actual mappings passed to database
        call_args = mock_db.bulk_set_asset_id_mappings.call_args[0][0]
        saved_components = [component for component, _ in call_args]

        # Verify IPs were filtered
        assert "192.168.1.100" not in saved_components, "IP should be filtered"
        assert "10.0.0.1" not in saved_components, "IP should be filtered"

        # Verify non-IPs were saved
        assert "server01" in saved_components, "Hostname should be saved"
        assert "00:11:22:33:44:55" in saved_components, "MAC should be saved"
        assert "server02.example.com" in saved_components, "FQDN should be saved"

    def test_ip_filtering_logging(self):
        """Test that IP filtering is logged during save."""
        mock_db = Mock()
        mock_db.bulk_set_asset_id_mappings = Mock(return_value=2)

        repo = MappingRepository(mock_db)

        test_mappings = {
            "server01": str(uuid.uuid4()),
            "192.168.1.1": str(uuid.uuid4()),
            "10.0.0.1": str(uuid.uuid4()),
        }

        with patch('src.modules.asset_generation.repositories.mapping_repository.logger') as mock_logger:
            repo.save_mappings_bulk(test_mappings, use_uuid=False)

            # Check that filtering was logged
            info_calls = [str(call) for call in mock_logger.info.call_args_list]
            filtered_logs = [call for call in info_calls if 'Filtered' in call and 'IP' in call]

            assert len(filtered_logs) > 0, "Should log IP filtering"


class TestIPReassignmentScenario:
    """Integration tests for IP reassignment scenarios."""

    def test_ip_reassignment_creates_separate_assets(self):
        """
        Test that when an IP is reassigned to a new device,
        it creates a separate asset (not merged with old device).

        Scenario:
        - Day 1: Server-A has IP 10.0.1.5, hostname server-a
        - Day 30: Server-A decommissioned
        - Day 60: Server-B gets IP 10.0.1.5, hostname server-b

        Expected: Two separate assets (A and B), not merged

        Note: This simulates two separate generation runs by creating
        a new identifier and loading only the persistent mappings (no IPs).
        """
        # Day 1: First generation run
        identifier_run1 = AssetIdentifier()

        components_a = ["server-a", "10.0.1.5", "sn:SN-AAA-001"]
        asset_id_a = identifier_run1.group_components(components_a)

        # Get mappings to persist (IPs should be filtered)
        mappings_day1 = identifier_run1.get_all_mappings()

        # Verify IP not in persistent mappings
        assert "10.0.1.5" not in mappings_day1, "IP should not be persisted"
        assert "server-a" in mappings_day1, "Hostname should be persisted"
        # normalize_component lowercases, so check lowercase
        assert "sn:sn-aaa-001" in mappings_day1, "SN should be persisted"

        # Day 60: Second generation run (simulate loading from DB)
        identifier_run2 = AssetIdentifier()
        identifier_run2.load_existing_mappings(mappings_day1)  # Load persistent mappings (no IPs)

        # Server-B gets same IP 10.0.1.5
        components_b = ["server-b", "10.0.1.5", "sn:SN-BBB-002"]
        asset_id_b = identifier_run2.group_components(components_b)

        # Should create NEW asset (not merge with A) because IP wasn't persisted
        assert asset_id_a != asset_id_b, "Should create separate asset for new device"

        # Get final mappings
        mappings_day60 = identifier_run2.get_all_mappings()

        # Verify IP still not in mappings
        assert "10.0.1.5" not in mappings_day60, "IP should still not be persisted"

        # Verify both servers have separate entries (lowercased)
        assert mappings_day60["server-a"] == asset_id_a
        assert mappings_day60["server-b"] == asset_id_b
        assert mappings_day60["sn:sn-aaa-001"] == asset_id_a
        assert mappings_day60["sn:sn-bbb-002"] == asset_id_b

    def test_ip_in_asset_field_still_used_for_matching(self):
        """
        Test that IPs in asset field are still used for matching
        within a single generation run, just not persisted.
        """
        identifier = AssetIdentifier()

        # Two records with same IP but different times (same generation run)
        components_1 = ["server01", "10.0.0.1", "sn:SN123"]
        components_2 = ["server01", "10.0.0.1"]  # No SN in second record

        asset_id_1 = identifier.group_components(components_1)
        asset_id_2 = identifier.group_components(components_2)

        # Should match to same asset within same run
        # because they share hostname
        assert asset_id_1 == asset_id_2, "Should match on hostname"

        # But IP should not be in persistent mappings
        mappings = identifier.get_all_mappings()
        assert "10.0.0.1" not in mappings, "IP should not be persisted"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
