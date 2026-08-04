# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Multi-run persistence tests for asset identification.

Validates that asset identity is preserved correctly across multiple
pipeline runs, including after SN conflict splits.
"""

import pytest
import pandas as pd
from unittest.mock import Mock, patch
from src.modules.asset_generation.domain.asset_identifier import AssetIdentifier
from src.modules.asset_generation.domain.asset_validator import AssetValidator
from src.modules.asset_generation.services.asset_generation_service import AssetGenerationService


class TestSNConflictSplitPersistence:
    """Test that SN conflict splits persist correctly across runs."""

    def _make_service(self):
        """Create an AssetGenerationService with mocked DB dependencies."""
        mock_db = Mock()
        mock_db.execute_query.return_value = []
        mock_db.conn_manager.is_scope_active.return_value = False
        service = AssetGenerationService(db_adapter=mock_db)
        return service

    def test_split_remaps_exclusive_components_in_unionfind(self):
        """
        After SN conflict split, exclusive hostname/MAC components of split rows
        should point to the NEW asset ID in UnionFind, not the old one.

        BUG (pre-fix): Only sn: components are remapped. hostname/MAC still
        point to old asset, causing re-merge on next run.
        """
        service = self._make_service()
        identifier = service.identifier

        # Simulate two devices that got incorrectly merged via shared hostname
        # Device 1: hostname=shared-host, mac=00:1A:2B:3C:4D:5E, sn=5CG8123ABC
        # Device 2: hostname=shared-host, mac=3C:22:FB:AA:BB:CC, sn=CNU4201XYZ
        # Note: Use physical (non-virtual) MACs so get_all_mappings() includes them
        # Note: These two SNs must stay edit distance > 2 apart with no substring
        # relation. are_sns_similar() reads near-identical serials (SN001/SN002)
        # as typos of one device, which makes _detect_and_fix_sn_conflicts keep
        # the asset merged — the split these tests rely on then never happens.
        components_row1 = ['shared-host', '00:1a:2b:3c:4d:5e', 'sn:5cg8123abc']
        components_row2 = ['shared-host', '3c:22:fb:aa:bb:cc', 'sn:cnu4201xyz']

        # Group both — they merge via shared hostname
        asset_id_1 = identifier.group_components(components_row1)
        asset_id_2 = identifier.group_components(components_row2)
        assert asset_id_1 == asset_id_2, "Should merge via shared hostname"

        merged_asset_id = asset_id_1

        # Build a DataFrame mimicking the pipeline state after ID assignment
        df = pd.DataFrame({
            'id': [merged_asset_id, merged_asset_id],
            'sn': ['5CG8123ABC', 'CNU4201XYZ'],
            'hostname': ['shared-host', 'shared-host'],
            'mac': ['00:1A:2B:3C:4D:5E', '3C:22:FB:AA:BB:CC'],
            '_parsed_components': [components_row1, components_row2],
        })

        # Run the SN conflict split
        conflicts_fixed = service._detect_and_fix_sn_conflicts(df)
        assert conflicts_fixed == 1, "Should detect 1 SN conflict"

        # After split, two different asset IDs should exist
        new_ids = df['id'].unique()
        assert len(new_ids) == 2, f"Expected 2 asset IDs after split, got {len(new_ids)}"

        # Get the new asset IDs per SN
        row1_new_id = df.iloc[0]['id']
        row2_new_id = df.iloc[1]['id']
        assert row1_new_id != row2_new_id, "Split rows should have different IDs"

        # KEY ASSERTION: The exclusive MAC components should now point to the
        # new asset IDs in UnionFind (not the old merged one)
        mappings = identifier.get_all_mappings()

        mac1_asset = mappings.get('00:1a:2b:3c:4d:5e')
        mac2_asset = mappings.get('3c:22:fb:aa:bb:cc')

        # After fix: each MAC should point to its row's new asset ID
        assert mac1_asset == row1_new_id, (
            f"MAC 00:1a:2b:3c:4d:5e should map to {row1_new_id} but maps to {mac1_asset}"
        )
        assert mac2_asset == row2_new_id, (
            f"MAC 3c:22:fb:aa:bb:cc should map to {row2_new_id} but maps to {mac2_asset}"
        )

    def test_split_does_not_remap_shared_components(self):
        """
        Components that appear in rows with DIFFERENT SNs within the same
        conflicted asset should NOT be remapped — they're ambiguous.
        """
        service = self._make_service()
        identifier = service.identifier

        # Both rows share 'shared-host' — it should not be remapped to either new asset
        # Note: Use physical (non-virtual) MACs so get_all_mappings() includes them
        # Note: These two SNs must stay edit distance > 2 apart with no substring
        # relation. are_sns_similar() reads near-identical serials (SN001/SN002)
        # as typos of one device, which makes _detect_and_fix_sn_conflicts keep
        # the asset merged — the split these tests rely on then never happens.
        components_row1 = ['shared-host', '00:1a:2b:3c:4d:5e', 'sn:5cg8123abc']
        components_row2 = ['shared-host', '3c:22:fb:aa:bb:cc', 'sn:cnu4201xyz']

        identifier.group_components(components_row1)
        merged_asset_id = identifier.group_components(components_row2)

        df = pd.DataFrame({
            'id': [merged_asset_id, merged_asset_id],
            'sn': ['5CG8123ABC', 'CNU4201XYZ'],
            'hostname': ['shared-host', 'shared-host'],
            'mac': ['00:1A:2B:3C:4D:5E', '3C:22:FB:AA:BB:CC'],
            '_parsed_components': [components_row1, components_row2],
        })

        service._detect_and_fix_sn_conflicts(df)

        # shared-host appeared with both SNs — should NOT be remapped
        # It should either stay with the old asset or not be in mappings at all
        mappings = identifier.get_all_mappings()
        row1_id = df.iloc[0]['id']
        row2_id = df.iloc[1]['id']

        shared_host_asset = mappings.get('shared-host')
        # shared-host should NOT be remapped to either new split asset
        # (it's ambiguous — appeared with both SNs)
        # It may still point to the old merged_asset_id or be absent from mappings
        assert shared_host_asset not in [row1_id, row2_id], (
            f"Shared hostname should NOT be remapped to either split asset, "
            f"but maps to {shared_host_asset}"
        )


class TestIdentityPreservationAcrossRuns:
    """Test that asset identity is preserved when mappings are loaded across runs."""

    def test_basic_identity_preserved(self):
        """
        Run 1: Create asset from hostname+MAC+SN.
        Run 2: Load mappings, same components get same asset ID.
        """
        # --- Run 1 ---
        identifier_run1 = AssetIdentifier()
        components = ['hostname:server01', 'aa:bb:cc:dd:ee:ff', 'sn:dell123']
        asset_id_run1 = identifier_run1.group_components(components)
        mappings = identifier_run1.get_all_mappings()

        # --- Run 2 ---
        identifier_run2 = AssetIdentifier()
        identifier_run2.load_existing_mappings(mappings)
        asset_id_run2 = identifier_run2.group_components(components)

        assert asset_id_run1 == asset_id_run2, (
            f"Asset ID should be preserved across runs: {asset_id_run1} != {asset_id_run2}"
        )

    def test_transitive_chain_across_three_runs(self):
        """
        Run 1: Component A -> asset-1
        Run 2: Component B bridges to A -> both map to asset-1
        Run 3: Load all mappings -- A and B resolve to asset-1
        """
        # --- Run 1 ---
        id1 = AssetIdentifier()
        comp_a = ['hostname:server01', 'sn:sn001']
        asset_1 = id1.group_components(comp_a)
        mappings_1 = id1.get_all_mappings()

        # --- Run 2 ---
        id2 = AssetIdentifier()
        id2.load_existing_mappings(mappings_1)
        # New component B shares hostname with A -> bridges to asset_1
        comp_b = ['hostname:server01', 'aa:bb:cc:dd:ee:ff']
        asset_2 = id2.group_components(comp_b)
        assert asset_2 == asset_1, "B should merge with A via shared hostname"
        mappings_2 = id2.get_all_mappings()

        # --- Run 3 ---
        id3 = AssetIdentifier()
        id3.load_existing_mappings(mappings_2)
        # Both A's SN and B's MAC should still resolve to asset_1
        asset_a = id3.group_components(comp_a)
        asset_b = id3.group_components(comp_b)
        assert asset_a == asset_1
        assert asset_b == asset_1
        assert asset_a == asset_b

    def test_component_only_rows_preserve_identity(self):
        """
        Run 1: hostname+MAC (no SN) -> asset-A
        Run 2: Same hostname+MAC -> same asset-A
        """
        # --- Run 1 ---
        id1 = AssetIdentifier()
        components = ['hostname:workstation01', 'aa:bb:cc:dd:ee:ff']
        asset_run1 = id1.group_components(components)
        mappings = id1.get_all_mappings()

        # --- Run 2 ---
        id2 = AssetIdentifier()
        id2.load_existing_mappings(mappings)
        asset_run2 = id2.group_components(components)

        assert asset_run1 == asset_run2

    def test_new_device_after_split_does_not_remerge(self):
        """
        After a split, a new row with same hostname + one of the SNs
        gets the correct split asset (not re-merged with the other).
        """
        # Use AssetGenerationService for the split logic
        mock_db = Mock()
        mock_db.execute_query.return_value = []
        mock_db.conn_manager.is_scope_active.return_value = False
        service = AssetGenerationService(db_adapter=mock_db)
        identifier = service.identifier

        # Two devices merged via shared hostname, then split
        # Use physical MACs (not locally-administered)
        # Note: These two SNs must stay edit distance > 2 apart with no substring
        # relation. are_sns_similar() reads near-identical serials (SN001/SN002)
        # as typos of one device, which makes _detect_and_fix_sn_conflicts keep
        # the asset merged — the split these tests rely on then never happens.
        components_row1 = ['shared-host', '00:1a:2b:3c:4d:5e', 'sn:5cg8123abc']
        components_row2 = ['shared-host', '3c:22:fb:aa:bb:cc', 'sn:cnu4201xyz']

        identifier.group_components(components_row1)
        merged_id = identifier.group_components(components_row2)

        df = pd.DataFrame({
            'id': [merged_id, merged_id],
            'sn': ['5CG8123ABC', 'CNU4201XYZ'],
            'hostname': ['shared-host', 'shared-host'],
            'mac': ['00:1A:2B:3C:4D:5E', '3C:22:FB:AA:BB:CC'],
            '_parsed_components': [components_row1, components_row2],
        })

        service._detect_and_fix_sn_conflicts(df)
        asset_for_5cg8123abc = df.iloc[0]['id']
        asset_for_cnu4201xyz = df.iloc[1]['id']

        # Extract mappings and start "Run 2"
        mappings = identifier.get_all_mappings()

        id2 = AssetIdentifier()
        id2.load_existing_mappings(mappings)

        # New row with MAC from device 1 + SN from device 1 -> should get device 1's asset
        new_row = ['new-hostname', '00:1a:2b:3c:4d:5e', 'sn:5cg8123abc']
        new_asset = id2.group_components(new_row)
        assert new_asset == asset_for_5cg8123abc, (
            "New row with 5CG8123ABC's MAC should map to "
            f"{asset_for_5cg8123abc}, got {new_asset}"
        )

        # And NOT to device 2's asset
        assert new_asset != asset_for_cnu4201xyz
