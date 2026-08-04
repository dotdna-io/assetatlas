# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for mapping hygiene: group size safety valve in load_existing_mappings."""
import re
import pytest
from src.modules.asset_generation.domain.asset_identifier import AssetIdentifier, _UUID_PATTERN

MAX_HOSTNAMES_PER_GROUP = AssetIdentifier.MAX_HOSTNAMES_PER_GROUP


class TestGroupSizeSafetyValve:
    """Groups with too many distinct hostnames are nuked during mapping load."""

    def setup_method(self):
        self.identifier = AssetIdentifier()

    def _all_components(self):
        """Get all components across all groups (excluding self-referential UUIDs)."""
        components = set()
        for group_components in self.identifier.get_asset_groups().values():
            for c in group_components:
                if not _UUID_PATTERN.match(c):
                    components.add(c)
        return components

    def test_normal_group_preserved(self):
        """Groups with <= threshold hostnames are kept intact."""
        asset_id = 'aaaa0000-bbbb-cccc-dddd-eeeeeeeeeeee'
        mappings = {f'switch{i:02d}': asset_id for i in range(10)}
        mappings['sn:serial123'] = asset_id
        self.identifier.load_existing_mappings(mappings)
        components = self._all_components()
        assert 'switch00' in components
        assert 'switch09' in components
        assert 'sn:serial123' in components

    def test_oversized_group_nuked(self):
        """Groups with > threshold hostnames are completely removed."""
        asset_id = 'aaaa0000-bbbb-cccc-dddd-eeeeeeeeeeee'
        mappings = {f'switch{i:02d}': asset_id for i in range(20)}
        self.identifier.load_existing_mappings(mappings)
        components = self._all_components()
        assert 'switch00' not in components
        assert 'switch19' not in components

    def test_exactly_at_threshold_preserved(self):
        """Groups with exactly threshold hostnames are kept (boundary check)."""
        asset_id = 'aaaa0000-bbbb-cccc-dddd-eeeeeeeeeeee'
        mappings = {f'host{i:03d}x': asset_id for i in range(MAX_HOSTNAMES_PER_GROUP)}
        self.identifier.load_existing_mappings(mappings)
        components = self._all_components()
        assert 'host000x' in components

    def test_one_over_threshold_nuked(self):
        """Groups with threshold + 1 hostnames are nuked."""
        asset_id = 'aaaa0000-bbbb-cccc-dddd-eeeeeeeeeeee'
        mappings = {f'host{i:03d}x': asset_id for i in range(MAX_HOSTNAMES_PER_GROUP + 1)}
        self.identifier.load_existing_mappings(mappings)
        components = self._all_components()
        assert 'host000x' not in components

    def test_oversized_group_doesnt_affect_other_groups(self):
        """Nuking an oversized group doesn't touch other groups."""
        bad_id = 'aaaa0000-bbbb-cccc-dddd-eeeeeeeeeeee'
        good_id = 'ffff0000-1111-2222-3333-444444444444'
        mappings = {}
        # Oversized group: 20 hostnames
        for i in range(20):
            mappings[f'badswitch{i:02d}'] = bad_id
        # Normal group: 2 hostnames + 1 SN
        mappings['goodserver01'] = good_id
        mappings['goodserver02'] = good_id
        mappings['sn:goodserial'] = good_id
        self.identifier.load_existing_mappings(mappings)
        components = self._all_components()
        # Bad group gone
        assert 'badswitch00' not in components
        assert 'badswitch19' not in components
        # Good group preserved
        assert 'goodserver01' in components
        assert 'goodserver02' in components
        assert 'sn:goodserial' in components

    def test_sn_components_dont_count_toward_hostname_limit(self):
        """SN-prefixed components are not counted as hostnames for the size limit."""
        asset_id = 'aaaa0000-bbbb-cccc-dddd-eeeeeeeeeeee'
        mappings = {}
        # 10 hostnames (under limit)
        for i in range(10):
            mappings[f'server{i:02d}aa'] = asset_id
        # 10 SNs (should not push over limit)
        for i in range(10):
            mappings[f'sn:serial{i:02d}'] = asset_id
        self.identifier.load_existing_mappings(mappings)
        components = self._all_components()
        assert 'server00aa' in components
        assert 'sn:serial00' in components

    def test_mac_components_dont_count_toward_hostname_limit(self):
        """MAC-format components are not counted as hostnames for the size limit."""
        asset_id = 'aaaa0000-bbbb-cccc-dddd-eeeeeeeeeeee'
        mappings = {}
        # 14 hostnames (just under limit)
        for i in range(14):
            mappings[f'server{i:02d}aa'] = asset_id
        # 5 MACs (should not push over limit)
        for i in range(5):
            mappings[f'00:50:56:92:44:{i:02d}'] = asset_id
        self.identifier.load_existing_mappings(mappings)
        components = self._all_components()
        assert 'server00aa' in components
        assert '00:50:56:92:44:00' in components

    def test_multiple_oversized_groups_all_nuked(self):
        """Multiple oversized groups are all removed independently."""
        id_a = 'aaaa0000-bbbb-cccc-dddd-eeeeeeeeeeee'
        id_b = 'bbbb0000-cccc-dddd-eeee-ffffffffffff'
        mappings = {}
        for i in range(20):
            mappings[f'groupa{i:02d}x'] = id_a
            mappings[f'groupb{i:02d}x'] = id_b
        self.identifier.load_existing_mappings(mappings)
        components = self._all_components()
        assert 'groupa00x' not in components
        assert 'groupb00x' not in components

    def test_uuid_components_dont_count_as_hostnames(self):
        """UUID-format components in a group don't count toward the hostname limit."""
        asset_id = 'aaaa0000-bbbb-cccc-dddd-eeeeeeeeeeee'
        mappings = {}
        # 10 hostnames (under limit)
        for i in range(10):
            mappings[f'server{i:02d}aa'] = asset_id
        # 10 UUID-format components (should not count as hostnames)
        for i in range(10):
            mappings[f'{i:08d}-0000-0000-0000-000000000000'] = asset_id
        self.identifier.load_existing_mappings(mappings)
        components = self._all_components()
        assert 'server00aa' in components
