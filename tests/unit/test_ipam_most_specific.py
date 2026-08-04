# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for IPAM most-specific network matching — #681 B1 (sub-item 2)."""

import pandas as pd

from src.modules.asset_generation.domain.asset_enricher import AssetEnricher


class TestIpamMostSpecific:
    def test_most_specific_network_wins_over_broader_overlaps(self):
        enricher = AssetEnricher()
        # Several nested networks all containing 10.1.2.5; the /28 is most specific.
        ipam_df = pd.DataFrame([
            {"network": "10.0.0.0/8",   "location": "Region"},
            {"network": "10.1.0.0/16",  "location": "Site"},
            {"network": "10.1.2.0/24",  "location": "Building"},
            {"network": "10.1.2.0/28",  "location": "Closet-A"},
        ])
        out = enricher.match_ipam_location(pd.DataFrame({"ip": ["10.1.2.5"]}), ipam_df)
        assert out["ipam_location"].iloc[0] == "Closet-A"   # most-specific, not arbitrary

    def test_most_specific_independent_of_row_order(self):
        enricher = AssetEnricher()
        # Same overlaps, inserted specific-first. The /28 must still win — the pick
        # is by range size (longest prefix), NOT row/set-iteration order. Without
        # this case a regression to "first/last-inserted wins" could pass the
        # broad-first fixture above by coincidence. (#681 B1)
        ipam_df = pd.DataFrame([
            {"network": "10.1.2.0/28",  "location": "Closet-A"},
            {"network": "10.1.2.0/24",  "location": "Building"},
            {"network": "10.1.0.0/16",  "location": "Site"},
            {"network": "10.0.0.0/8",   "location": "Region"},
        ])
        out = enricher.match_ipam_location(pd.DataFrame({"ip": ["10.1.2.5"]}), ipam_df)
        assert out["ipam_location"].iloc[0] == "Closet-A"

    def test_single_match_unchanged(self):
        enricher = AssetEnricher()
        ipam_df = pd.DataFrame([{"network": "192.168.0.0/16", "location": "Branch"}])
        out = enricher.match_ipam_location(pd.DataFrame({"ip": ["192.168.5.5"]}), ipam_df)
        assert out["ipam_location"].iloc[0] == "Branch"

    def test_no_match_is_none(self):
        enricher = AssetEnricher()
        ipam_df = pd.DataFrame([{"network": "10.0.0.0/8", "location": "HQ"}])
        out = enricher.match_ipam_location(pd.DataFrame({"ip": ["8.8.8.8"]}), ipam_df)
        val = out["ipam_location"].iloc[0]
        assert val is None or pd.isna(val)
