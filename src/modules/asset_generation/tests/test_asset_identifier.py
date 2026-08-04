# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for AssetIdentifier (UnionFind algorithm)

Tests the core asset identification and grouping logic.
"""

import pytest
import pandas as pd
from src.modules.asset_generation.domain.asset_identifier import AssetIdentifier


class TestAssetIdentifierInitialization:
    """Test AssetIdentifier initialization."""

    def test_init_creates_empty_union_find(self):
        """Test that initialization creates an empty UnionFind structure."""
        identifier = AssetIdentifier()

        assert identifier is not None
        stats = identifier.get_stats()
        assert stats['total_components'] == 0
        assert stats['total_assets'] == 0


class TestComponentGrouping:
    """Test component grouping logic."""

    def test_group_components_single_ip(self):
        """Test grouping with a single IP component."""
        identifier = AssetIdentifier()

        components = ['ip:192.168.1.1']

        asset_id = identifier.group_components(components)

        assert asset_id is not None
        assert identifier.get_stats()['total_components'] > 0

    def test_group_components_multiple_identifiers(self):
        """Test grouping with multiple identifiers."""
        identifier = AssetIdentifier()

        # First asset with IP and MAC
        components1 = ['ip:192.168.1.1', 'mac:00:11:22:33:44:55']

        asset_id1 = identifier.group_components(components1)

        # Second asset with same IP (should get same asset_id)
        components2 = ['ip:192.168.1.1', 'mac:00:11:22:33:44:66']

        asset_id2 = identifier.group_components(components2)

        # Should be grouped together due to shared IP
        assert asset_id1 == asset_id2

    def test_group_components_with_hostname(self):
        """Test grouping with hostname component."""
        identifier = AssetIdentifier()

        components = ['hostname:server01', 'ip:10.0.0.1']

        asset_id = identifier.group_components(components)

        assert asset_id is not None
        assert isinstance(asset_id, str)

    def test_group_components_with_serial_number(self):
        """Test grouping with serial number."""
        identifier = AssetIdentifier()

        components = ['sn:ABC123', 'ip:10.0.0.1']

        asset_id = identifier.group_components(components)

        # Should have an asset ID
        assert asset_id is not None
        assert isinstance(asset_id, str)

    def test_group_components_empty_list(self):
        """Test grouping with empty components."""
        identifier = AssetIdentifier()

        # Empty list should raise ValueError
        with pytest.raises(ValueError):
            identifier.group_components([])


class TestAssetMerging:
    """Test asset merging logic with UnionFind."""

    def test_merge_assets_via_shared_component(self):
        """Test that assets with shared components get merged."""
        identifier = AssetIdentifier()

        # Asset 1: IP A + MAC X
        components1 = ['ip:192.168.1.1', 'mac:AA:BB:CC:DD:EE:FF']
        asset_id1 = identifier.group_components(components1)

        # Asset 2: IP B + MAC X (shares MAC with Asset 1)
        components2 = ['ip:192.168.1.2', 'mac:AA:BB:CC:DD:EE:FF']
        asset_id2 = identifier.group_components(components2)

        # Asset 3: IP A + MAC Y (shares IP with Asset 1)
        components3 = ['ip:192.168.1.1', 'mac:11:22:33:44:55:66']
        asset_id3 = identifier.group_components(components3)

        # All three should be merged together
        assert asset_id1 == asset_id2 == asset_id3

    def test_separate_assets_no_shared_components(self):
        """Test that assets without shared components stay separate."""
        identifier = AssetIdentifier()

        # Asset 1
        components1 = ['ip:192.168.1.1']
        asset_id1 = identifier.group_components(components1)

        # Asset 2 (completely different)
        components2 = ['ip:192.168.1.2']
        asset_id2 = identifier.group_components(components2)

        # Should be different asset IDs
        assert asset_id1 != asset_id2


class TestLoadExistingMappings:
    """Test loading existing mappings."""

    def test_load_mappings_dict(self):
        """Test loading mappings from dictionary."""
        identifier = AssetIdentifier()

        mappings = {
            'ip:192.168.1.1': 'asset-001',
            'mac:AA:BB:CC:DD:EE:FF': 'asset-001',
            'ip:192.168.1.2': 'asset-002'
        }

        identifier.load_existing_mappings(mappings)

        # Verify mappings were loaded
        all_mappings = identifier.get_all_mappings()
        assert len(all_mappings) >= 3
        assert 'ip:192.168.1.1' in all_mappings

    def test_load_mappings_preserves_grouping(self):
        """Test that loaded mappings preserve asset groupings."""
        identifier = AssetIdentifier()

        # Load existing mappings where components belong to same asset
        mappings = {
            'ip:10.0.0.1': 'asset-123',
            'mac:AA:BB:CC:DD:EE:FF': 'asset-123'
        }

        identifier.load_existing_mappings(mappings)

        # New component with shared IP should merge
        components = ['ip:10.0.0.1', 'hostname:server01']

        asset_id = identifier.group_components(components)

        # Should get the existing asset ID
        assert asset_id == 'asset-123'


class TestMacMappingHygiene:
    """Test MAC component removal from oversized groups during mapping load."""

    def test_mac_removed_from_group_with_many_hostnames(self):
        """MAC in a group with >2 hostnames should be removed (docking station pattern)."""
        identifier = AssetIdentifier()

        # Simulate 3 hostnames + 1 MAC in the same group (docking station bridged them)
        # Use real UUID format for asset_id (matches _UUID_PATTERN in hostname counting)
        aid = '11a583e5-dc8c-562c-ad93-7c86f2da2ca1'
        mappings = {
            'host1': aid,
            'host2': aid,
            'host3': aid,
            'd0:65:78:75:75:06': aid,
            'sn:sn001': aid,
        }
        identifier.load_existing_mappings(mappings)

        all_mappings = identifier.get_all_mappings()
        # MAC should be removed (3 hostnames > threshold of 2)
        assert 'd0:65:78:75:75:06' not in all_mappings
        # Hostnames and SN should be preserved
        assert 'host1' in all_mappings
        assert 'host2' in all_mappings
        assert 'host3' in all_mappings
        assert 'sn:sn001' in all_mappings

    def test_mac_kept_in_group_with_few_hostnames(self):
        """MAC in a group with <=2 hostnames should be kept (device rename)."""
        identifier = AssetIdentifier()

        # 2 hostnames + MAC = legitimate device rename, keep MAC
        aid = '22b694f6-ee9c-5148-8fa1-7de010c9a63c'
        mappings = {
            'old-name': aid,
            'new-name': aid,
            'd0:65:78:75:75:06': aid,
            'sn:sn002': aid,
        }
        identifier.load_existing_mappings(mappings)

        all_mappings = identifier.get_all_mappings()
        # MAC should be kept (2 hostnames <= threshold)
        assert 'd0:65:78:75:75:06' in all_mappings

    def test_mac_removed_does_not_affect_other_groups(self):
        """Removing MAC from one group should not affect other groups."""
        identifier = AssetIdentifier()

        aid1 = '33c7a5b7-ff0d-6259-9fb2-8ef121d0b74d'
        aid2 = '44d8b6c8-0010-7360-a0c3-9f0232e1c85e'
        mappings = {
            # Group 1: 3 hostnames + MAC → MAC should be removed
            'host1': aid1,
            'host2': aid1,
            'host3': aid1,
            'd0:65:78:75:75:06': aid1,
            # Group 2: 1 hostname + MAC → MAC should be kept
            'host4': aid2,
            'f8:ce:72:16:31:ca': aid2,
        }
        identifier.load_existing_mappings(mappings)

        all_mappings = identifier.get_all_mappings()
        assert 'd0:65:78:75:75:06' not in all_mappings  # removed
        assert 'f8:ce:72:16:31:ca' in all_mappings  # kept


class TestGetAllMappings:
    """Test retrieving all mappings."""

    def test_get_all_mappings_returns_dict(self):
        """Test that get_all_mappings returns a dictionary."""
        identifier = AssetIdentifier()

        components = ['ip:192.168.1.1']
        identifier.group_components(components)

        mappings = identifier.get_all_mappings()

        assert isinstance(mappings, dict)
        assert len(mappings) > 0

    def test_get_all_mappings_includes_all_components(self):
        """Test that all non-IP components are included in mappings."""
        identifier = AssetIdentifier()

        components = ['ip:192.168.1.1', 'mac:AA:BB:CC:DD:EE:FF', 'hostname:server01']

        asset_id = identifier.group_components(components)
        mappings = identifier.get_all_mappings()

        # normalize_component lowercases all components, so check lowercase versions
        # IP addresses are filtered out by get_all_mappings
        assert 'mac:aa:bb:cc:dd:ee:ff' in mappings
        assert 'hostname:server01' in mappings

        # Non-IP components should point to same asset
        assert mappings['mac:aa:bb:cc:dd:ee:ff'] == asset_id
        assert mappings['hostname:server01'] == asset_id


class TestStatistics:
    """Test statistics reporting."""

    def test_get_stats_returns_dict(self):
        """Test that get_stats returns a dictionary."""
        identifier = AssetIdentifier()
        stats = identifier.get_stats()

        assert isinstance(stats, dict)
        assert 'total_components' in stats
        assert 'total_assets' in stats
        assert 'avg_components_per_asset' in stats

    def test_get_stats_counts_correctly(self):
        """Test that stats are calculated correctly."""
        identifier = AssetIdentifier()

        # Create some mappings
        identifier.group_components(['ip:192.168.1.1', 'mac:AA:BB:CC:DD:EE:FF'])
        identifier.group_components(['ip:192.168.1.2'])

        stats = identifier.get_stats()

        assert stats['total_components'] >= 3  # At least 3 components
        assert stats['total_assets'] >= 2  # At least 2 distinct assets


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_components(self):
        """Test handling of empty components list."""
        identifier = AssetIdentifier()

        # Empty list should raise ValueError
        with pytest.raises(ValueError):
            identifier.group_components([])

    def test_components_with_special_chars(self):
        """Test components with special characters."""
        identifier = AssetIdentifier()

        components = [
            'hostname:server-01.example.com',
            'ip:2001:0db8:85a3::8a2e:0370:7334'  # IPv6
        ]

        asset_id = identifier.group_components(components)

        # Should handle gracefully
        assert asset_id is not None

    def test_very_long_component_string(self):
        """Test handling of very long component strings."""
        identifier = AssetIdentifier()

        long_hostname = 'a' * 1000
        components = [f'hostname:{long_hostname}']

        asset_id = identifier.group_components(components)

        assert asset_id is not None

    def test_get_or_create_asset_id(self):
        """Test get_or_create_asset_id method."""
        identifier = AssetIdentifier()

        # Get or create for new component
        asset_id1 = identifier.get_or_create_asset_id('ip:192.168.1.1')
        assert asset_id1 is not None

        # Get same ID for same component
        asset_id2 = identifier.get_or_create_asset_id('ip:192.168.1.1')
        assert asset_id1 == asset_id2


class TestProblematicHostnameSNHandling:
    """
    Test that _assign_asset_ids keeps valid SNs when a hostname is problematic
    (appears with multiple SNs) and strips the unreliable hostname instead.

    Bug: Previously, when a hostname was flagged as problematic (shared across
    multiple SNs, like a kiosk profile), the code stripped the SN from components
    instead of stripping the hostname. This caused records with valid SNs to
    get empty asset fields and be excluded from the pipeline.
    """

    @pytest.fixture
    def service(self):
        """Create AssetGenerationService with mock db_adapter."""
        from unittest.mock import Mock
        mock_db = Mock()
        from src.modules.asset_generation.services.asset_generation_service import (
            AssetGenerationService,
        )
        svc = AssetGenerationService(
            db_adapter=mock_db,
            enable_rule_based_prediction=False,
        )
        return svc

    def test_keeps_sn_when_hostname_is_problematic(self, service):
        """A record with a valid SN but problematic hostname keeps its SN and gets an ID."""
        # Two records share hostname 'kiosk01' but have different SNs
        # → hostname is problematic. The SN should be kept, hostname stripped.
        df = pd.DataFrame([
            {'hostname': 'kiosk01', 'sn': 'SN-AAA', 'asset': 'kiosk01|sn:sn-aaa'},
            {'hostname': 'kiosk01', 'sn': 'SN-BBB', 'asset': 'kiosk01|sn:sn-bbb'},
        ])

        result, _ = service._assign_asset_ids(df)

        # Both rows should have valid asset IDs (not <NA>)
        assert result.at[0, 'id'] != '<NA>', "Row 0 should have an asset ID (SN preserved)"
        assert result.at[1, 'id'] != '<NA>', "Row 1 should have an asset ID (SN preserved)"

    def test_different_sns_same_hostname_get_different_ids(self, service):
        """Records with the same problematic hostname but different SNs get separate asset IDs."""
        df = pd.DataFrame([
            {'hostname': 'kiosk01', 'sn': 'SN-AAA', 'asset': 'kiosk01|sn:sn-aaa'},
            {'hostname': 'kiosk01', 'sn': 'SN-BBB', 'asset': 'kiosk01|sn:sn-bbb'},
        ])

        result, _ = service._assign_asset_ids(df)

        id_0 = result.at[0, 'id']
        id_1 = result.at[1, 'id']

        # Because the hostname is stripped and SNs differ, these are separate assets
        assert id_0 != '<NA>'
        assert id_1 != '<NA>'
        assert id_0 != id_1, "Different SNs with problematic hostname should yield different asset IDs"

    def test_problematic_hostname_stripped_from_components(self, service):
        """The problematic hostname is stripped so it does not cause incorrect merges."""
        # If hostname were kept, both rows would merge via the shared 'kiosk01' component
        df = pd.DataFrame([
            {'hostname': 'kiosk01', 'sn': 'SN-AAA', 'asset': 'kiosk01|sn:sn-aaa'},
            {'hostname': 'kiosk01', 'sn': 'SN-BBB', 'asset': 'kiosk01|sn:sn-bbb'},
        ])

        result, _ = service._assign_asset_ids(df)

        # After processing, the identifier should not have 'kiosk01' as a component
        # (it was stripped). Only the SNs should be mapped.
        mappings = service.identifier.get_all_mappings()
        assert 'kiosk01' not in mappings, "Problematic hostname should be stripped from mappings"
        assert 'sn:sn-aaa' in mappings, "SN-AAA should be in mappings"
        assert 'sn:sn-bbb' in mappings, "SN-BBB should be in mappings"

    def test_non_problematic_hostname_preserved(self, service):
        """Hostnames that are NOT problematic remain in components."""
        # Each hostname has exactly one SN → not problematic
        df = pd.DataFrame([
            {'hostname': 'server01', 'sn': 'SN-111', 'asset': 'server01|sn:sn-111'},
            {'hostname': 'server02', 'sn': 'SN-222', 'asset': 'server02|sn:sn-222'},
        ])

        result, _ = service._assign_asset_ids(df)

        mappings = service.identifier.get_all_mappings()
        assert 'server01' in mappings, "Non-problematic hostname should be preserved"
        assert 'server02' in mappings, "Non-problematic hostname should be preserved"


class TestTrackChangesHistoricalIds:
    """
    Test that _track_changes uses pre-loaded historical_ids when provided.

    Bug: Stage 9 archives current-run assets to assets_history BEFORE Stage 10
    loads historical IDs. Every new asset's ID is already in assets_history when
    Stage 10 checks, so all new assets are classified as asset_readded instead
    of asset_created.

    Fix: Load historical_ids BEFORE persist and pass them into _track_changes.
    """

    @pytest.fixture
    def service(self):
        """Create AssetGenerationService with mocked asset_repo and chronicle_service."""
        from unittest.mock import Mock
        mock_db = Mock()
        from src.modules.asset_generation.services.asset_generation_service import (
            AssetGenerationService,
        )
        svc = AssetGenerationService(
            db_adapter=mock_db,
            enable_rule_based_prediction=False,
        )
        # Replace asset_repo and chronicle_service with mocks
        svc.asset_repo = Mock()
        svc.chronicle_service = Mock()
        svc.chronicle_service.excluded_fields = set()
        svc.chronicle_service.compare_and_generate_events.return_value = []
        svc.chronicle_service.write_events.return_value = 0
        return svc

    def test_track_changes_uses_passed_historical_ids(self, service):
        """When historical_ids is passed, _track_changes uses it instead of querying DB."""
        import uuid

        old_df = pd.DataFrame(columns=['id'])
        new_df = pd.DataFrame([{'id': 'new-asset-001', 'hostname': 'server01'}])
        run_id = uuid.uuid4()

        # Pass empty historical_ids — simulating a first-ever run
        service._track_changes(old_df, new_df, run_id, historical_ids=[])

        # chronicle_service.compare_and_generate_events should receive the empty list
        call_args = service.chronicle_service.compare_and_generate_events.call_args
        existing_ids_arg = call_args[0][2]  # 3rd positional arg
        assert existing_ids_arg == [], "Should pass empty historical_ids to chronicle"

        # asset_repo.get_all_historical_asset_ids should NOT have been called
        service.asset_repo.get_all_historical_asset_ids.assert_not_called()

    def test_track_changes_falls_back_to_db_when_no_historical_ids(self, service):
        """When historical_ids is None, _track_changes loads from DB (backward compat)."""
        import uuid

        service.asset_repo.get_all_historical_asset_ids.return_value = ['old-asset-001']

        old_df = pd.DataFrame(columns=['id'])
        new_df = pd.DataFrame([{'id': 'new-asset-001', 'hostname': 'server01'}])
        run_id = uuid.uuid4()

        # Don't pass historical_ids (defaults to None)
        service._track_changes(old_df, new_df, run_id)

        # Should have queried DB
        service.asset_repo.get_all_historical_asset_ids.assert_called_once()

        # chronicle_service.compare_and_generate_events should receive the DB result
        call_args = service.chronicle_service.compare_and_generate_events.call_args
        existing_ids_arg = call_args[0][2]
        assert existing_ids_arg == ['old-asset-001']

    def test_new_asset_classified_as_created_with_empty_historical_ids(self, service):
        """A new asset is classified as created (not readded) when historical_ids is empty."""
        import uuid
        from src.modules.asset_generation.services.chronicle_service import ChronicleService
        from unittest.mock import Mock

        # Use a real ChronicleService to verify event classification
        mock_db = Mock()
        real_chronicle = ChronicleService(db_adapter=mock_db)
        service.chronicle_service = real_chronicle

        old_df = pd.DataFrame(columns=['id'])
        new_df = pd.DataFrame([{'id': 'brand-new-asset', 'hostname': 'server01'}])
        run_id = uuid.uuid4()

        # Pass empty historical_ids — this asset has never existed before
        # Use compare_and_generate_events directly to inspect events without DB write
        events = real_chronicle.compare_and_generate_events(
            {},  # no current assets
            {'brand-new-asset': {'hostname': 'server01'}},  # new asset
            [],  # empty historical_ids
            generation_run_id=str(run_id)
        )

        assert len(events) == 1
        assert events[0]['event_type'] == 'asset_created', (
            "New asset with empty historical_ids should be asset_created, not asset_readded"
        )

    def test_existing_asset_classified_as_readded_with_historical_ids(self, service):
        """An asset in historical_ids is classified as readded (not created)."""
        import uuid
        from src.modules.asset_generation.services.chronicle_service import ChronicleService
        from unittest.mock import Mock

        mock_db = Mock()
        real_chronicle = ChronicleService(db_adapter=mock_db)

        # Asset was seen historically — should be readded, not created
        events = real_chronicle.compare_and_generate_events(
            {},  # not in current run
            {'known-asset-001': {'hostname': 'server01'}},  # "new" in this run
            ['known-asset-001'],  # but known historically
            generation_run_id=str(uuid.uuid4())
        )

        assert len(events) == 1
        assert events[0]['event_type'] == 'asset_readded', (
            "Asset in historical_ids should be asset_readded, not asset_created"
        )


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
