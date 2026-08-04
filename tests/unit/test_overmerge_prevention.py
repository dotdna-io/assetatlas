# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for over-merge prevention: IP exclusion, frequency filtering, post-merge safety."""
import pytest
import pandas as pd

from src.modules.asset_generation.domain.asset_identifier import AssetIdentifier
from src.modules.asset_generation.domain.asset_validator import AssetValidator


class TestIPExclusionFromClassifyRow:
    """IPs are always removed from UnionFind components."""

    def setup_method(self):
        self.validator = AssetValidator()

    def test_ip_removed_even_without_persistent_identifiers(self):
        """An IP-only row should produce no components (excluded)."""
        # Simulate _classify_row logic: IPs should always be filtered
        components = ["192.168.1.1"]
        filtered = [c for c in components if not self.validator.is_valid_ip_address(c)]
        assert filtered == []

    def test_ip_removed_alongside_hostname(self):
        """IP is removed but hostname is kept."""
        components = ["server01", "10.0.0.5"]
        filtered = [c for c in components if not self.validator.is_valid_ip_address(c)]
        assert filtered == ["server01"]

    def test_ip_removed_alongside_sn_and_mac(self):
        """IP is removed while SN and MAC are preserved."""
        components = ["sn:ABC123", "aa:bb:cc:dd:ee:ff", "172.16.0.1"]
        filtered = [c for c in components if not self.validator.is_valid_ip_address(c)]
        assert "172.16.0.1" not in filtered
        assert "sn:ABC123" in filtered
        assert "aa:bb:cc:dd:ee:ff" in filtered

    def test_ipv6_removed(self):
        """IPv6 addresses are also filtered."""
        components = ["server01", "fe80::1"]
        filtered = [c for c in components if not self.validator.is_valid_ip_address(c)]
        assert "fe80::1" not in filtered
        assert "server01" in filtered


class TestIPExclusionFromMappings:
    """IP components are cleaned from loaded mappings."""

    def setup_method(self):
        self.identifier = AssetIdentifier()

    def test_ip_components_removed_on_load(self):
        """IPs in existing mappings are stripped during load."""
        asset_id = "aaaa0000-bbbb-cccc-dddd-eeeeeeeeeeee"
        mappings = {
            "server01": asset_id,
            "192.168.1.100": asset_id,
            "10.0.0.5": asset_id,
            "sn:serial123": asset_id,
        }
        self.identifier.load_existing_mappings(mappings)

        # IPs should not be in the parent map
        assert "192.168.1.100" not in self.identifier.union_find.parent
        assert "10.0.0.5" not in self.identifier.union_find.parent
        # Non-IP components should remain
        assert "server01" in self.identifier.union_find.parent
        assert "sn:serial123" in self.identifier.union_find.parent

    def test_no_ip_components_is_noop(self):
        """Load without IPs doesn't break anything."""
        asset_id = "aaaa0000-bbbb-cccc-dddd-eeeeeeeeeeee"
        mappings = {
            "host1": asset_id,
            "sn:SN001": asset_id,
        }
        self.identifier.load_existing_mappings(mappings)
        assert "host1" in self.identifier.union_find.parent
        assert "sn:sn001" in self.identifier.union_find.parent


class TestFrequencyExclusion:
    """MACs/SNs appearing on >3 hostnames are frequency-excluded."""

    def test_mac_on_many_hostnames_detected(self):
        """A MAC appearing on 4+ hostnames should be identified for exclusion."""
        data = {
            "mac": ["aa:bb:cc:dd:ee:ff"] * 4,
            "hostname": ["host1", "host2", "host3", "host4"],
        }
        df = pd.DataFrame(data)
        mac_host_df = df.dropna(subset=["mac", "hostname"])[
            ["mac", "hostname"]
        ].drop_duplicates()
        mac_counts = mac_host_df.groupby("mac")["hostname"].nunique()
        freq_excluded = set(mac_counts[mac_counts > 3].index.str.lower())
        assert "aa:bb:cc:dd:ee:ff" in freq_excluded

    def test_mac_on_few_hostnames_not_excluded(self):
        """A MAC on <= 3 hostnames should not be excluded."""
        data = {
            "mac": ["aa:bb:cc:dd:ee:ff"] * 3,
            "hostname": ["host1", "host2", "host3"],
        }
        df = pd.DataFrame(data)
        mac_host_df = df.dropna(subset=["mac", "hostname"])[
            ["mac", "hostname"]
        ].drop_duplicates()
        mac_counts = mac_host_df.groupby("mac")["hostname"].nunique()
        freq_excluded = set(mac_counts[mac_counts > 3].index.str.lower())
        assert "aa:bb:cc:dd:ee:ff" not in freq_excluded

    def test_sn_frequency_exclusion(self):
        """An SN appearing on >3 hostnames should be excluded."""
        data = {
            "sn": ["SHARED-SN"] * 5,
            "hostname": ["host1", "host2", "host3", "host4", "host5"],
        }
        df = pd.DataFrame(data)
        sn_host_df = df.dropna(subset=["sn", "hostname"])[
            ["sn", "hostname"]
        ].drop_duplicates()
        sn_counts = sn_host_df.groupby("sn")["hostname"].nunique()
        freq_excluded = set(sn_counts[sn_counts > 3].index.str.lower())
        assert "shared-sn" in freq_excluded

    def test_mixed_macs_only_frequent_excluded(self):
        """Only the high-frequency MAC is excluded, normal MACs remain."""
        data = {
            "mac": (
                ["aa:bb:cc:dd:ee:ff"] * 4  # 4 hostnames -> excluded
                + ["11:22:33:44:55:66"] * 2  # 2 hostnames -> kept
            ),
            "hostname": ["h1", "h2", "h3", "h4", "h5", "h6"],
        }
        df = pd.DataFrame(data)
        mac_host_df = df.dropna(subset=["mac", "hostname"])[
            ["mac", "hostname"]
        ].drop_duplicates()
        mac_counts = mac_host_df.groupby("mac")["hostname"].nunique()
        freq_excluded = set(mac_counts[mac_counts > 3].index.str.lower())
        assert "aa:bb:cc:dd:ee:ff" in freq_excluded
        assert "11:22:33:44:55:66" not in freq_excluded


class TestPostMergeSafetyCheck:
    """Post-merge safety valve splits over-merged assets."""

    def _make_service(self):
        """Create a minimal service instance with just what safety check needs."""
        from src.modules.asset_generation.services.asset_generation_service import (
            AssetGenerationService,
        )
        from unittest.mock import MagicMock

        svc = object.__new__(AssetGenerationService)
        svc.identifier = AssetIdentifier()
        svc.validator = AssetValidator()
        return svc

    def test_split_asset_with_too_many_hostnames(self):
        """Assets with >15 hostnames are split by hostname."""
        svc = self._make_service()
        identifier = svc.identifier

        # Create 20 rows all assigned to the same asset
        asset_id = identifier.create_new_asset_id()
        hostnames = [f"host{i:03d}" for i in range(20)]
        df = pd.DataFrame(
            {
                "id": [asset_id] * 20,
                "hostname": hostnames,
                "manufacturer": ["Dell"] * 20,
            }
        )

        result = svc._post_merge_safety_check(df, identifier)

        # Each hostname should now have its own unique asset ID
        unique_ids = result["id"].unique()
        assert len(unique_ids) == 20
        # None of the IDs should be the original over-merged ID
        assert asset_id not in unique_ids

    def test_preserve_asset_within_hostname_limit(self):
        """Assets with <= 15 hostnames are not split."""
        svc = self._make_service()
        identifier = svc.identifier

        asset_id = identifier.create_new_asset_id()
        hostnames = [f"host{i:03d}" for i in range(10)]
        df = pd.DataFrame(
            {
                "id": [asset_id] * 10,
                "hostname": hostnames,
                "manufacturer": ["Dell"] * 10,
            }
        )

        result = svc._post_merge_safety_check(df, identifier)

        # All rows should still have the same asset ID
        assert (result["id"] == asset_id).all()

    def test_split_asset_with_too_many_manufacturer_families(self):
        """Assets with >2 manufacturer families are split."""
        svc = self._make_service()
        identifier = svc.identifier

        asset_id = identifier.create_new_asset_id()
        # 3 distinct manufacturer families (Dell, HP, Cisco) -> should trigger split
        df = pd.DataFrame(
            {
                "id": [asset_id] * 6,
                "hostname": ["h1", "h2", "h3", "h4", "h5", "h6"],
                "manufacturer": [
                    "Dell Inc.",
                    "Dell Inc.",
                    "Hewlett Packard",
                    "Hewlett Packard",
                    "Cisco Systems",
                    "Cisco Systems",
                ],
            }
        )

        result = svc._post_merge_safety_check(df, identifier)

        # Asset should be split — each hostname gets its own ID
        unique_ids = result["id"].unique()
        assert len(unique_ids) == 6

    def test_two_manufacturer_families_ok(self):
        """Assets with exactly 2 manufacturer families are NOT split."""
        svc = self._make_service()
        identifier = svc.identifier

        asset_id = identifier.create_new_asset_id()
        df = pd.DataFrame(
            {
                "id": [asset_id] * 4,
                "hostname": ["h1", "h2", "h3", "h4"],
                "manufacturer": ["Dell Inc.", "Dell Inc.", "HP Inc.", "HP Inc."],
            }
        )

        result = svc._post_merge_safety_check(df, identifier)

        # Should NOT be split
        assert (result["id"] == asset_id).all()

    def test_na_rows_ignored(self):
        """Rows with <NA> asset_id are not checked."""
        svc = self._make_service()
        identifier = svc.identifier

        df = pd.DataFrame(
            {
                "id": ["<NA>"] * 5,
                "hostname": [f"host{i}" for i in range(5)],
                "manufacturer": ["Dell"] * 5,
            }
        )

        result = svc._post_merge_safety_check(df, identifier)

        # All still <NA>
        assert (result["id"] == "<NA>").all()

    def test_null_hostnames_excluded_on_split(self):
        """When splitting, rows with no hostname are excluded (set to <NA>)."""
        svc = self._make_service()
        identifier = svc.identifier

        asset_id = identifier.create_new_asset_id()
        hostnames = [f"host{i:03d}" for i in range(16)] + [None, None]
        df = pd.DataFrame(
            {
                "id": [asset_id] * 18,
                "hostname": hostnames,
                "manufacturer": ["Dell"] * 18,
            }
        )

        result = svc._post_merge_safety_check(df, identifier)

        # The two null-hostname rows should be excluded
        null_mask = result["hostname"].isna()
        null_ids = result.loc[null_mask, "id"].tolist()
        assert len(null_ids) == 2
        assert all(nid == '<NA>' for nid in null_ids)

    def test_split_produces_deterministic_ids(self):
        """Same hostname should produce same asset ID across runs."""
        svc = self._make_service()
        identifier = svc.identifier

        asset_id = identifier.create_new_asset_id()
        hostnames = [f"host{i:03d}" for i in range(16)]
        df = pd.DataFrame({"id": [asset_id] * 16, "hostname": hostnames, "manufacturer": ["Dell"] * 16})

        result1 = svc._post_merge_safety_check(df.copy(), identifier)

        # Run again with a different initial asset_id
        asset_id2 = identifier.create_new_asset_id()
        df2 = pd.DataFrame({"id": [asset_id2] * 16, "hostname": hostnames, "manufacturer": ["Dell"] * 16})
        result2 = svc._post_merge_safety_check(df2, identifier)

        # Same hostnames should produce same asset IDs
        ids1 = result1.sort_values('hostname')['id'].tolist()
        ids2 = result2.sort_values('hostname')['id'].tolist()
        assert ids1 == ids2
