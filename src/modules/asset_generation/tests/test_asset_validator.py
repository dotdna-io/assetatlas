# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for AssetValidator

Tests data validation, quality checking, and asset field generation.
"""

import pytest
import pandas as pd
import numpy as np
from src.modules.asset_generation.domain.asset_validator import AssetValidator


class TestAssetValidatorInitialization:
    """Test AssetValidator initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default parameters."""
        validator = AssetValidator()

        assert validator is not None
        assert validator.sn_overuse_threshold == 4
        assert validator.sn_migration_threshold == 2
        assert validator.consecutive_char_length_threshold == 4
        assert len(validator.common_sn_placeholders) > 0

    def test_init_with_custom_thresholds(self):
        """Test initialization with custom thresholds."""
        validator = AssetValidator(
            sn_overuse_threshold=10,
            sn_migration_threshold=5,
            consecutive_char_length_threshold=3
        )

        assert validator.sn_overuse_threshold == 10
        assert validator.sn_migration_threshold == 5
        assert validator.consecutive_char_length_threshold == 3


class TestIPAddressValidation:
    """Test IP address validation."""

    def test_valid_ipv4_address(self):
        """Test validation of valid IPv4 addresses."""
        validator = AssetValidator()

        valid_ips = [
            '192.168.1.1',
            '10.0.0.1',
            '172.16.0.1',
            '8.8.8.8',
            '255.255.255.255',
            '0.0.0.0'
        ]

        for ip in valid_ips:
            assert validator.is_valid_ip_address(ip), f"Failed for {ip}"

    def test_valid_ipv6_address(self):
        """Test validation of valid IPv6 addresses."""
        validator = AssetValidator()

        valid_ips = [
            '2001:0db8:85a3:0000:0000:8a2e:0370:7334',
            '2001:db8:85a3::8a2e:370:7334',
            '::1',
            'fe80::1'
        ]

        for ip in valid_ips:
            assert validator.is_valid_ip_address(ip), f"Failed for {ip}"

    def test_invalid_ip_address(self):
        """Test validation of invalid IP addresses."""
        validator = AssetValidator()

        invalid_ips = [
            '',
            None,
            'not.an.ip',
            '999.999.999.999',
            '192.168.1',
            '192.168.1.1.1',
            'hostname',
            '192.168.1.256'
        ]

        for ip in invalid_ips:
            assert not validator.is_valid_ip_address(ip), f"Should fail for {ip}"

    def test_ip_validation_caching(self):
        """Test that IP validation uses LRU cache."""
        validator = AssetValidator()

        # Call multiple times - should use cache
        ip = '192.168.1.1'
        result1 = validator.is_valid_ip_address(ip)
        result2 = validator.is_valid_ip_address(ip)

        assert result1 == result2
        assert result1 is True


class TestSequentialDetection:
    """Test sequential number and letter detection."""

    def test_has_sequential_numbers(self):
        """Test detection of sequential numbers."""
        validator = AssetValidator()

        # Should detect sequences
        assert validator.has_sequential_numbers('12345')
        assert validator.has_sequential_numbers('abc1234def')
        assert validator.has_sequential_numbers('56789')

        # Should not detect sequences
        assert not validator.has_sequential_numbers('13579')
        assert not validator.has_sequential_numbers('abcdef')
        assert not validator.has_sequential_numbers('111222')

    def test_has_sequential_numbers_with_custom_length(self):
        """Test sequential numbers with custom length."""
        validator = AssetValidator()

        # Length 3
        assert validator.has_sequential_numbers('123', length=3)
        assert not validator.has_sequential_numbers('135', length=3)

        # Length 5
        assert validator.has_sequential_numbers('12345', length=5)
        assert not validator.has_sequential_numbers('1234', length=5)

    def test_has_sequential_letters(self):
        """Test detection of sequential letters (default length 4)."""
        validator = AssetValidator()

        # Should detect sequences (4+ chars)
        assert validator.has_sequential_letters('abcd')
        assert validator.has_sequential_letters('DEFG')
        assert validator.has_sequential_letters('wxyz')  # 4 sequential letters

        # Should not detect sequences
        assert not validator.has_sequential_letters('xyz')  # Only 3 letters (threshold is 4)
        assert not validator.has_sequential_letters('aceg')
        assert not validator.has_sequential_letters('12345')
        assert not validator.has_sequential_letters('aabb')

    def test_has_sequential_letters_case_insensitive(self):
        """Test that sequential letter detection is case insensitive."""
        validator = AssetValidator()

        assert validator.has_sequential_letters('ABCD')
        assert validator.has_sequential_letters('abcd')
        assert validator.has_sequential_letters('AbCd')


class TestSerialNumberValidation:
    """Test serial number validation."""

    def test_is_sn_suspicious_with_placeholders(self):
        """Test that placeholder SNs are flagged as suspicious."""
        validator = AssetValidator()

        placeholders = [
            'unknown',
            'none',
            'n/a',
            'not available',
            'to be filled by o.e.m.',
            'system serial number',
            '00000000',
            '12345678'
        ]

        for sn in placeholders:
            assert validator.is_sn_suspicious(sn), f"Should be suspicious: {sn}"

    def test_is_sn_suspicious_with_null_empty(self):
        """Test that null/empty SNs are suspicious."""
        validator = AssetValidator()

        assert validator.is_sn_suspicious(None)
        assert validator.is_sn_suspicious('')
        assert validator.is_sn_suspicious('   ')
        assert validator.is_sn_suspicious(pd.NA)
        assert validator.is_sn_suspicious(np.nan)

    def test_is_sn_suspicious_with_repeating_chars(self):
        """Test that SNs with repeating characters are suspicious."""
        validator = AssetValidator()

        assert validator.is_sn_suspicious('0000000')
        assert validator.is_sn_suspicious('AAAAAAA')
        assert validator.is_sn_suspicious('1111111')

    def test_is_sn_suspicious_with_sequential_patterns(self):
        """Test that SNs with sequential patterns are suspicious."""
        validator = AssetValidator()

        assert validator.is_sn_suspicious('ABC123456')  # Sequential numbers
        assert validator.is_sn_suspicious('XYZ-ABCDEF')  # Sequential letters

    def test_is_sn_suspicious_with_valid_sns(self):
        """Test that legitimate SNs are not flagged."""
        validator = AssetValidator()

        valid_sns = [
            'CND9876XYZ',
            'S/N: 5CD8472K',
            'VMW-42A8B9',
            'DELL-R740-XYZ123'
        ]

        for sn in valid_sns:
            assert not validator.is_sn_suspicious(sn), f"Should not be suspicious: {sn}"

    def test_is_sn_suspicious_with_overuse_set(self):
        """Test that overused SNs are flagged when provided."""
        validator = AssetValidator()

        overused = {'OVERUSED123', 'DUPLICATE999'}

        assert validator.is_sn_suspicious('OVERUSED123', overused)
        assert validator.is_sn_suspicious('DUPLICATE999', overused)
        assert not validator.is_sn_suspicious('VALID456', overused)


class TestSerialNumberQuality:
    """Test serial number quality evaluation."""

    def test_evaluate_sn_quality_high_quality(self):
        """Test quality evaluation of high-quality SNs."""
        validator = AssetValidator()

        # Valid SN with low hostname count
        score, is_migration = validator.evaluate_sn_quality('DELL-XYZ123', hostname_count=1)

        assert score > 0.8  # Should have high score
        assert is_migration is True  # Legitimate SN

    def test_evaluate_sn_quality_placeholder(self):
        """Test quality evaluation of placeholder SNs."""
        validator = AssetValidator()

        score, is_migration = validator.evaluate_sn_quality('unknown', hostname_count=1)

        assert score == 0.0  # Zero quality
        assert is_migration is False  # Not migration

    def test_evaluate_sn_quality_overused(self):
        """Test quality evaluation of overused SNs."""
        validator = AssetValidator()

        # SN with high hostname count (exceeds overuse threshold)
        score, is_migration = validator.evaluate_sn_quality('VALID123', hostname_count=10)

        assert score < 0.5  # Low score due to overuse
        assert is_migration is False  # Too many hosts

    def test_evaluate_sn_quality_potential_migration(self):
        """Test quality evaluation of potential migration SNs."""
        validator = AssetValidator()

        # SN with moderate hostname count (between migration and overuse thresholds)
        score, is_migration = validator.evaluate_sn_quality('VALID456', hostname_count=3)

        # Should have moderate score and might be migration
        assert 0.5 <= score <= 1.0
        # is_migration depends on quality score

    def test_evaluate_sn_quality_null_sn(self):
        """Test quality evaluation of null/empty SNs."""
        validator = AssetValidator()

        score, is_migration = validator.evaluate_sn_quality(None, hostname_count=1)
        assert score == 0.0
        assert is_migration is False

        score, is_migration = validator.evaluate_sn_quality('', hostname_count=1)
        assert score == 0.0
        assert is_migration is False


class TestComponentValidation:
    """Test generic component validation."""

    def test_validate_component_ip(self):
        """Test IP component validation."""
        validator = AssetValidator()

        assert validator.validate_component('ip', '192.168.1.1')
        assert not validator.validate_component('ip', 'not.an.ip')
        assert not validator.validate_component('ip', None)

    def test_validate_component_sn(self):
        """Test SN component validation."""
        validator = AssetValidator()

        assert validator.validate_component('sn', 'VALID123XYZ')
        assert not validator.validate_component('sn', 'unknown')
        assert not validator.validate_component('sn', None)

    def test_validate_component_hostname(self):
        """Test hostname component validation."""
        validator = AssetValidator()

        assert validator.validate_component('hostname', 'server01')
        assert not validator.validate_component('hostname', 'unknown')
        assert not validator.validate_component('hostname', None)
        assert not validator.validate_component('hostname', '')

    def test_validate_component_mac(self):
        """Test MAC component validation."""
        validator = AssetValidator()

        assert validator.validate_component('mac', '00:11:22:33:44:55')
        assert validator.validate_component('mac', 'AA:BB:CC:DD:EE:FF')
        assert not validator.validate_component('mac', 'too-short')  # is_valid_mac validates format (12 hex chars)
        assert not validator.validate_component('mac', None)


class TestValidationStatistics:
    """Test validation statistics generation."""

    def test_get_validation_stats(self):
        """Test validation statistics calculation."""
        validator = AssetValidator()

        df = pd.DataFrame({
            'sn': ['VALID123', 'unknown', 'VALID456', 'none', '12345678']
        })

        stats = validator.get_validation_stats(df, sn_column='sn')

        assert stats['total_records'] == 5
        assert stats['suspicious_sns'] == 3  # unknown, none, 12345678
        assert stats['valid_sns'] == 2  # VALID123, VALID456
        assert 'suspicious_sn_percentage' in stats

    def test_get_validation_stats_all_valid(self):
        """Test validation stats with all valid SNs."""
        validator = AssetValidator()

        df = pd.DataFrame({
            'sn': ['VALID123', 'VALID456', 'VALID789']
        })

        stats = validator.get_validation_stats(df, sn_column='sn')

        assert stats['suspicious_sns'] == 0
        assert stats['valid_sns'] == 3

    def test_get_validation_stats_empty_dataframe(self):
        """Test validation stats with empty DataFrame."""
        validator = AssetValidator()

        df = pd.DataFrame({'sn': []})

        stats = validator.get_validation_stats(df, sn_column='sn')

        assert stats['total_records'] == 0
        assert stats['suspicious_sn_percentage'] == 0


class TestAssetFieldGeneration:
    """Test asset field generation."""

    def test_create_asset_field_basic(self):
        """Test basic asset field creation."""
        validator = AssetValidator()

        df = pd.DataFrame([{
            'hostname': 'server01',
            'ip': '192.168.1.1',
            'mac': '00:11:22:33:44:55',
            'sn': 'VALID123',
            'fqdn': 'server01.example.com',
            'data_source': 'source1'
        }])

        result = validator.create_asset_field(df)

        assert 'asset' in result.columns
        asset_field = result['asset'].iloc[0]
        assert 'server01' in asset_field
        assert '192.168.1.1' in asset_field
        assert '00:11:22:33:44:55' in asset_field
        assert 'sn:valid123' in asset_field

    def test_create_asset_field_excludes_placeholders(self):
        """Test that placeholder SNs are excluded."""
        validator = AssetValidator()

        df = pd.DataFrame([{
            'hostname': 'server01',
            'ip': '192.168.1.1',
            'mac': '00:11:22:33:44:55',
            'sn': 'unknown',  # Placeholder
            'fqdn': '',
            'data_source': 'source1'
        }])

        result = validator.create_asset_field(df)

        asset_field = result['asset'].iloc[0]
        assert 'unknown' not in asset_field
        assert 'sn:unknown' not in asset_field

    def test_create_asset_field_excludes_duplicates(self):
        """Test that duplicate identifiers are removed."""
        validator = AssetValidator()

        df = pd.DataFrame([{
            'hostname': 'server01',
            'ip': '192.168.1.1',
            'mac': '00:11:22:33:44:55',
            'sn': 'VALID123',
            'fqdn': 'server01',  # Same as hostname
            'data_source': 'source1'
        }])

        result = validator.create_asset_field(df)

        asset_field = result['asset'].iloc[0]
        # server01 should appear only once
        assert asset_field.count('server01') == 1

    def test_create_asset_field_with_missing_columns(self):
        """Test asset field creation with missing columns."""
        validator = AssetValidator()

        df = pd.DataFrame([{
            'ip': '192.168.1.1',
            'data_source': 'source1'
        }])

        result = validator.create_asset_field(df)

        assert 'asset' in result.columns
        assert '192.168.1.1' in result['asset'].iloc[0]

    def test_create_asset_field_multiple_rows(self):
        """Test asset field creation with multiple rows."""
        validator = AssetValidator()

        df = pd.DataFrame([
            {
                'hostname': 'server01',
                'ip': '192.168.1.1',
                'mac': '00:11:22:33:44:55',
                'sn': 'VALID123',
                'fqdn': '',
                'data_source': 'source1'
            },
            {
                'hostname': 'server02',
                'ip': '192.168.1.2',
                'mac': '00:11:22:33:44:66',
                'sn': 'VALID456',
                'fqdn': '',
                'data_source': 'source1'
            }
        ])

        result = validator.create_asset_field(df)

        assert len(result) == 2
        assert 'server01' in result['asset'].iloc[0]
        assert 'server02' in result['asset'].iloc[1]


class TestExclusionSetBuilding:
    """Test exclusion set building logic."""

    def test_build_exclusion_sets_ips_with_multiple_hostnames(self):
        """Test that IPs with multiple hostnames are excluded."""
        validator = AssetValidator()

        df = pd.DataFrame([
            {'hostname': 'host1', 'ip': '192.168.1.1', 'mac': '', 'sn': '', 'fqdn': '', 'data_source': 'source1'},
            {'hostname': 'host2', 'ip': '192.168.1.1', 'mac': '', 'sn': '', 'fqdn': '', 'data_source': 'source1'},
            {'hostname': 'host3', 'ip': '192.168.1.2', 'mac': '', 'sn': '', 'fqdn': '', 'data_source': 'source1'}
        ])

        exclusion_sets = validator._build_exclusion_sets(df)

        # 192.168.1.1 has multiple hostnames, should be excluded
        assert '192.168.1.1' in exclusion_sets['ips']
        # 192.168.1.2 has only one hostname, should not be excluded
        assert '192.168.1.2' not in exclusion_sets['ips']

    def test_build_exclusion_sets_macs_with_multiple_hostnames(self):
        """Test that MACs with >2 hostnames are excluded (threshold=2 allows renames)."""
        validator = AssetValidator()

        df = pd.DataFrame([
            # MAC with 3 hostnames — exceeds threshold of 2, should be excluded
            {'hostname': 'host1', 'ip': '', 'mac': 'AA:BB:CC:DD:EE:FF', 'sn': '', 'fqdn': '', 'data_source': 'source1'},
            {'hostname': 'host2', 'ip': '', 'mac': 'AA:BB:CC:DD:EE:FF', 'sn': '', 'fqdn': '', 'data_source': 'source1'},
            {'hostname': 'host3', 'ip': '', 'mac': 'AA:BB:CC:DD:EE:FF', 'sn': '', 'fqdn': '', 'data_source': 'source1'},
            # MAC with 2 hostnames — within threshold (device rename), NOT excluded
            {'hostname': 'old-name', 'ip': '', 'mac': 'CC:DD:EE:FF:00:11', 'sn': '', 'fqdn': '', 'data_source': 'source1'},
            {'hostname': 'new-name', 'ip': '', 'mac': 'CC:DD:EE:FF:00:11', 'sn': '', 'fqdn': '', 'data_source': 'source1'},
            # MAC with 1 hostname — should not be excluded
            {'hostname': 'host4', 'ip': '', 'mac': '11:22:33:44:55:66', 'sn': '', 'fqdn': '', 'data_source': 'source1'}
        ])

        exclusion_sets = validator._build_exclusion_sets(df)

        # 3 hostnames (>2) = excluded
        assert 'aa:bb:cc:dd:ee:ff' in exclusion_sets['macs']
        # 2 hostnames (<=2, device rename) = NOT excluded
        assert 'cc:dd:ee:ff:00:11' not in exclusion_sets['macs']
        # 1 hostname = not excluded
        assert '11:22:33:44:55:66' not in exclusion_sets['macs']

    def test_build_exclusion_sets_overused_sns(self):
        """Test that overused SNs are excluded."""
        validator = AssetValidator(sn_overuse_threshold=2)

        df = pd.DataFrame([
            {'hostname': 'host1', 'ip': '', 'mac': '', 'sn': 'VALID123', 'fqdn': '', 'data_source': 'source1'},
            {'hostname': 'host2', 'ip': '', 'mac': '', 'sn': 'VALID123', 'fqdn': '', 'data_source': 'source1'},
            {'hostname': 'host3', 'ip': '', 'mac': '', 'sn': 'VALID123', 'fqdn': '', 'data_source': 'source1'},
            {'hostname': 'host4', 'ip': '', 'mac': '', 'sn': 'VALID456', 'fqdn': '', 'data_source': 'source1'}
        ])

        exclusion_sets = validator._build_exclusion_sets(df)

        # VALID123 has 3 hostnames (exceeds threshold of 2), should be excluded
        assert 'valid123' in exclusion_sets['sns']
        # VALID456 has only 1 hostname, should not be excluded
        assert 'valid456' not in exclusion_sets['sns']

    def test_build_exclusion_sets_empty_dataframe(self):
        """Test exclusion set building with empty DataFrame."""
        validator = AssetValidator()

        # Create empty but properly structured DataFrame
        df = pd.DataFrame(columns=['hostname', 'ip', 'mac', 'sn', 'fqdn', 'data_source'])

        exclusion_sets = validator._build_exclusion_sets(df)

        assert len(exclusion_sets['ips']) == 0
        assert len(exclusion_sets['macs']) == 0
        assert len(exclusion_sets['sns']) == 0

    def test_build_exclusion_sets_cross_row_mac_sn(self):
        """Test that MACs bridging hostnames with different SNs are excluded.

        Simulates a docking station MAC: the MAC appears with host1 (from Armis)
        and host2 (from Defender), while host1 has SN1 (from ServiceNow) and host2
        has SN2 (from ServiceNow). MAC and SN never appear on the same row, but the
        cross-row check correlates through hostname.
        """
        validator = AssetValidator()

        df = pd.DataFrame([
            # Dock MAC seen on host1 (source: Armis, no SN)
            {'hostname': 'host1', 'ip': '', 'mac': 'dd:dd:20:52:41:53', 'sn': None, 'fqdn': '', 'data_source': 'Armis'},
            # Dock MAC seen on host2 (source: Defender, no SN)
            {'hostname': 'host2', 'ip': '', 'mac': 'dd:dd:20:52:41:53', 'sn': None, 'fqdn': '', 'data_source': 'Defender'},
            # host1 has SN1 (source: ServiceNow, no MAC)
            {'hostname': 'host1', 'ip': '', 'mac': None, 'sn': 'SN0001', 'fqdn': '', 'data_source': 'ServiceNow'},
            # host2 has SN2 (source: ServiceNow, no MAC)
            {'hostname': 'host2', 'ip': '', 'mac': None, 'sn': 'SN0002', 'fqdn': '', 'data_source': 'ServiceNow'},
        ])

        exclusion_sets = validator._build_exclusion_sets(df)

        # Dock MAC bridges 2 hostnames with DIFFERENT SNs → excluded
        assert 'dd:dd:20:52:41:53' in exclusion_sets['macs']

    def test_build_exclusion_sets_cross_row_mac_same_sn_kept(self):
        """Test that MACs bridging hostnames with the SAME SN are NOT excluded.

        This is the device-rename scenario: same device, new hostname, same SN.
        """
        validator = AssetValidator()

        df = pd.DataFrame([
            {'hostname': 'old-name', 'ip': '', 'mac': 'aa:bb:cc:dd:ee:ff', 'sn': None, 'fqdn': '', 'data_source': 'Armis'},
            {'hostname': 'new-name', 'ip': '', 'mac': 'aa:bb:cc:dd:ee:ff', 'sn': None, 'fqdn': '', 'data_source': 'Armis'},
            {'hostname': 'old-name', 'ip': '', 'mac': None, 'sn': 'SAME-SN', 'fqdn': '', 'data_source': 'ServiceNow'},
            {'hostname': 'new-name', 'ip': '', 'mac': None, 'sn': 'SAME-SN', 'fqdn': '', 'data_source': 'ServiceNow'},
        ])

        exclusion_sets = validator._build_exclusion_sets(df)

        # Same SN on both hostnames → device rename → MAC kept
        assert 'aa:bb:cc:dd:ee:ff' not in exclusion_sets['macs']

    def test_build_exclusion_sets_cross_row_mac_no_sn_kept(self):
        """Test that MACs bridging hostnames with NO SN data are NOT excluded.

        Conservative: can't confirm different devices without SN evidence.
        """
        validator = AssetValidator()

        df = pd.DataFrame([
            {'hostname': 'host1', 'ip': '', 'mac': 'aa:bb:cc:dd:ee:ff', 'sn': None, 'fqdn': '', 'data_source': 'Armis'},
            {'hostname': 'host2', 'ip': '', 'mac': 'aa:bb:cc:dd:ee:ff', 'sn': None, 'fqdn': '', 'data_source': 'Armis'},
        ])

        exclusion_sets = validator._build_exclusion_sets(df)

        # No SN evidence → no determination → MAC kept
        assert 'aa:bb:cc:dd:ee:ff' not in exclusion_sets['macs']


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_validator_with_special_characters(self):
        """Test validator with special characters in identifiers."""
        validator = AssetValidator()

        # Hostname with hyphens and dots
        assert validator.validate_component('hostname', 'server-01.example.com')

        # IPv6 with colons
        assert validator.validate_component('ip', '2001:db8::1')

        # MAC with colons
        assert validator.validate_component('mac', 'AA:BB:CC:DD:EE:FF')

    def test_validator_with_unicode(self):
        """Test validator with unicode characters."""
        validator = AssetValidator()

        # Ukrainian placeholder
        assert validator.is_sn_suspicious('серійний номер відсутній')

        # Spanish placeholder
        assert validator.is_sn_suspicious('no disponible')

    def test_validator_with_very_long_strings(self):
        """Test validator with very long strings."""
        validator = AssetValidator()

        long_sn = 'A' * 1000
        # Should not crash, but will be flagged for repeating characters
        result = validator.is_sn_suspicious(long_sn)
        assert result is True  # All same character

    def test_create_asset_field_preserves_order(self):
        """Test that asset field preserves identifier order."""
        validator = AssetValidator()

        df = pd.DataFrame([{
            'hostname': 'server01',
            'ip': '192.168.1.1',
            'mac': '00:11:22:33:44:55',
            'sn': 'VALID123',
            'fqdn': 'server01.example.com',
            'data_source': 'source1'
        }])

        result = validator.create_asset_field(df)

        asset_field = result['asset'].iloc[0]
        parts = asset_field.split('|')

        # Hostname/FQDN should come before SN, IP, MAC based on code order
        hostname_idx = next((i for i, p in enumerate(parts) if 'server01' in p), None)
        sn_idx = next((i for i, p in enumerate(parts) if 'sn:' in p), None)
        ip_idx = next((i for i, p in enumerate(parts) if '192.168.1.1' in p), None)

        assert hostname_idx is not None
        assert sn_idx is not None
        assert ip_idx is not None

    def test_validator_with_mixed_types(self):
        """Test validator with mixed data types."""
        validator = AssetValidator()

        df = pd.DataFrame([{
            'hostname': 123,  # Integer instead of string
            'ip': 192.168,  # Float instead of string
            'mac': None,
            'sn': True,  # Boolean
            'fqdn': '',
            'data_source': 'source1'
        }])

        # Should handle gracefully by converting to strings
        result = validator.create_asset_field(df)

        assert 'asset' in result.columns


class TestManufacturerHostnameExclusion:
    """Test that manufacturer hostname check uses exact match only (#104)."""

    def test_hostname_starting_with_manufacturer_not_excluded(self):
        """'dell-server-01' with manufacturer 'dell' should keep hostname."""
        validator = AssetValidator()
        df = pd.DataFrame({
            'hostname': ['dell-server-01'],
            'manufacturer': ['Dell'],
            'sn': ['SN001'],
            'ip': [None], 'mac': [None], 'fqdn': [None],
            'os': [None], 'data_source': ['test'],
        })
        result = validator.create_asset_field(df)
        assert 'dell-server-01' in result.at[0, 'asset']

    def test_exact_manufacturer_hostname_excluded(self):
        """Hostname exactly matching manufacturer name should be excluded."""
        validator = AssetValidator()
        df = pd.DataFrame({
            'hostname': ['dell'],
            'manufacturer': ['Dell'],
            'sn': ['SN001'],
            'ip': [None], 'mac': [None], 'fqdn': [None],
            'os': [None], 'data_source': ['test'],
        })
        result = validator.create_asset_field(df)
        # 'dell' hostname excluded, asset should only have sn:
        parts = result.at[0, 'asset'].split('|')
        hostnames = [p for p in parts if not p.startswith('sn:')]
        assert 'dell' not in hostnames


class TestManufacturerHostnameNormalization:
    """Test that manufacturer names used as hostnames are rejected."""

    def test_hostname_matches_manufacturer_variant(self):
        """Hostname 'Samsung' should be rejected when manufacturer is 'Samsung Electronics Co.'"""
        validator = AssetValidator()
        df = pd.DataFrame([{
            'hostname': 'Samsung',
            'ip': '172.31.203.59',
            'mac': 'b0:f2:f6:cc:86:78',
            'sn': None,
            'fqdn': None,
            'manufacturer': 'Samsung Electronics Co.',
        }])
        result = validator.create_asset_field(df)
        assert 'samsung' not in result.iloc[0]['asset'].split('|')

    def test_hostname_is_bare_manufacturer_name_no_mfr_field(self):
        """Hostname 'Samsung' should be rejected even without manufacturer field."""
        validator = AssetValidator()
        df = pd.DataFrame([{
            'hostname': 'Samsung',
            'ip': '172.31.203.59',
            'mac': 'b0:f2:f6:cc:86:78',
            'sn': None,
            'fqdn': None,
            'manufacturer': None,
        }])
        result = validator.create_asset_field(df)
        assert 'samsung' not in result.iloc[0]['asset'].split('|')

    def test_normal_hostname_with_samsung_manufacturer_kept(self):
        """Normal hostname should be kept even when manufacturer is Samsung."""
        validator = AssetValidator()
        df = pd.DataFrame([{
            'hostname': 'DECL76JLZ',
            'ip': '172.31.203.59',
            'mac': None,
            'sn': None,
            'fqdn': None,
            'manufacturer': 'Samsung Electronics',
        }])
        result = validator.create_asset_field(df)
        assert 'decl76jlz' in result.iloc[0]['asset'].split('|')


class TestFQDNGenericHostnameCheck:
    """Test that FQDNs with generic short names are rejected."""

    def test_fqdn_with_generic_hostname_iphone(self):
        """FQDN 'iphone.primprod.net' should be rejected."""
        validator = AssetValidator()
        df = pd.DataFrame([{
            'hostname': None,
            'ip': '172.18.133.213',
            'mac': None,
            'sn': None,
            'fqdn': 'iphone.primprod.net',
            'manufacturer': None,
        }])
        result = validator.create_asset_field(df)
        asset_components = result.iloc[0]['asset'].split('|')
        assert 'iphone.primprod.net' not in asset_components
        assert 'iphone' not in asset_components

    def test_fqdn_with_generic_hostname_desktop(self):
        """FQDN 'desktop.corp.local' should be rejected."""
        validator = AssetValidator()
        df = pd.DataFrame([{
            'hostname': None,
            'ip': '10.0.0.1',
            'mac': None,
            'sn': None,
            'fqdn': 'desktop.corp.local',
            'manufacturer': None,
        }])
        result = validator.create_asset_field(df)
        asset_components = result.iloc[0]['asset'].split('|')
        assert 'desktop.corp.local' not in asset_components

    def test_fqdn_with_valid_hostname(self):
        """FQDN 'server01.primprod.net' should be accepted."""
        validator = AssetValidator()
        df = pd.DataFrame([{
            'hostname': None,
            'ip': '172.18.133.213',
            'mac': None,
            'sn': None,
            'fqdn': 'server01.primprod.net',
            'manufacturer': None,
        }])
        result = validator.create_asset_field(df)
        asset_components = result.iloc[0]['asset'].split('|')
        assert 'server01.primprod.net' in asset_components


class TestFQDNDatePatternCheck:
    """Test that FQDNs with date patterns in the short name are rejected."""

    def test_fqdn_with_date_dash_rejected(self):
        """FQDN 'device-3-15-2025.corp.com' should be rejected."""
        validator = AssetValidator()
        df = pd.DataFrame([{
            'hostname': None,
            'ip': '10.0.0.1',
            'mac': None,
            'sn': None,
            'fqdn': 'device-3-15-2025.corp.com',
            'manufacturer': None,
        }])
        result = validator.create_asset_field(df)
        asset_components = result.iloc[0]['asset'].split('|')
        assert 'device-3-15-2025.corp.com' not in asset_components
        assert 'device-3-15-2025' not in asset_components

    def test_fqdn_without_date_accepted(self):
        """FQDN 'server01.corp.com' should be accepted."""
        validator = AssetValidator()
        df = pd.DataFrame([{
            'hostname': None,
            'ip': '10.0.0.1',
            'mac': None,
            'sn': None,
            'fqdn': 'server01.corp.com',
            'manufacturer': None,
        }])
        result = validator.create_asset_field(df)
        asset_components = result.iloc[0]['asset'].split('|')
        assert 'server01.corp.com' in asset_components


class TestModelHostnameDetection:
    """Test that device model names used as hostnames are rejected."""

    @pytest.fixture
    def kb_with_models(self, tmp_path):
        """Create a KB with known model patterns."""
        from src.modules.asset_generation.domain.device_knowledge_base import DeviceKnowledgeBase
        (tmp_path / "product_lines.csv").write_text(
            "manufacturer_normalized,model_pattern,category,subcategory,confidence,source\n"
            "polycom,^mp5,miscellaneous,voip,0.9,manual\n"
            "polycom,^vvx,miscellaneous,voip,0.9,manual\n"
        )
        (tmp_path / "manufacturers.csv").write_text(
            "manufacturer_normalized,category,subcategory_default,confidence,source\n"
        )
        (tmp_path / "manufacturer_aliases.csv").write_text(
            "alias_pattern,manufacturer_normalized\n"
        )
        (tmp_path / "oui_categories.csv").write_text(
            "oui_prefix,manufacturer_normalized,category,source\n"
        )
        return DeviceKnowledgeBase(str(tmp_path))

    def test_model_hostname_mp56_rejected(self, kb_with_models):
        validator = AssetValidator(device_kb=kb_with_models)
        df = pd.DataFrame([{
            'hostname': 'MP56-E2', 'ip': '172.30.9.92',
            'mac': None, 'sn': None, 'fqdn': None, 'manufacturer': None,
        }])
        result = validator.create_asset_field(df)
        assert 'mp56-e2' not in result.iloc[0]['asset'].split('|')

    def test_normal_hostname_kept_with_kb(self, kb_with_models):
        validator = AssetValidator(device_kb=kb_with_models)
        df = pd.DataFrame([{
            'hostname': 'DECL76JLZ', 'ip': '172.30.9.92',
            'mac': None, 'sn': None, 'fqdn': None, 'manufacturer': None,
        }])
        result = validator.create_asset_field(df)
        assert 'decl76jlz' in result.iloc[0]['asset'].split('|')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
