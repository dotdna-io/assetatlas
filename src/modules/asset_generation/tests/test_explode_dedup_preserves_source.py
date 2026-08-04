# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression tests for cross-integration source loss in _explode_array_identifiers.

Bug: the post-explode dedup in AssetValidator._explode_array_identifiers keyed only on
the identifier columns (hostname/fqdn/ip/mac/sn). When two integrations reported the same
host with an identical identifier tuple, the two rows collapsed to one — silently dropping
the later-ordered integration's data_source attribution. Which source survived depended
purely on row order (the asset_generation loader sorts ``ORDER BY raw_asset_data.id``, i.e.
ingestion order). Real-world impact on production: a connected SentinelOne agent vanished from
asset 7d52a2a7 (VGBGILGRCON01) because ServiceNow reported the same hostname/ip/mac/sn and
was ingested first.

The dedup is intended only to remove the intra-row cross-product created by exploding
multiple array-valued columns within a SINGLE source record — those duplicates share the
same source, so keying the dedup on a source-distinguishing column preserves the legitimate
multi-source rows while still collapsing the cross-product noise.

Note: the dedup only runs when identifier columns contain lists (as raw_asset_data text[]
columns are loaded), so these tests deliberately use list-valued identifier columns to
exercise that code path.
"""

import pandas as pd

from src.modules.asset_generation.domain.asset_validator import AssetValidator


class TestExplodeDedupPreservesDataSource:
    def test_same_identity_from_two_integrations_keeps_both_sources(self):
        """Two integrations reporting an identical (hostname, ip, mac, sn) tuple must
        BOTH survive create_asset_field — neither integration's row may be dropped as a
        'duplicate', or that integration's data_source is lost from the asset."""
        validator = AssetValidator()
        df = pd.DataFrame({
            'hostname': [['HOSTX'], ['HOSTX']],
            'fqdn': [None, None],
            'ip': [['10.0.0.5'], ['10.0.0.5']],
            'mac': [['aa:bb:cc:dd:ee:01'], ['aa:bb:cc:dd:ee:01']],
            'sn': [['REALSERIAL123'], ['REALSERIAL123']],
            'data_source': ['ServiceNow', 'SentinelOne'],
        })

        result = validator.create_asset_field(df)

        sources = set(result['data_source'])
        assert sources == {'ServiceNow', 'SentinelOne'}, (
            "Both integrations' rows must survive the explode dedup so each contributes "
            f"its data_source; got {sources}"
        )

    def test_survival_independent_of_row_order(self):
        """The surviving source must not depend on row order — both orderings must keep
        both sources. (The bug made survival depend on raw_asset_data.id order.)"""
        validator = AssetValidator()
        base = {
            'hostname': [['HOSTX'], ['HOSTX']],
            'fqdn': [None, None],
            'ip': [['10.0.0.5'], ['10.0.0.5']],
            'mac': [['aa:bb:cc:dd:ee:01'], ['aa:bb:cc:dd:ee:01']],
            'sn': [['REALSERIAL123'], ['REALSERIAL123']],
        }
        df_a = pd.DataFrame({**base, 'data_source': ['ServiceNow', 'SentinelOne']})
        df_b = pd.DataFrame({**base, 'data_source': ['SentinelOne', 'ServiceNow']})

        sources_a = set(AssetValidator().create_asset_field(df_a)['data_source'])
        sources_b = set(validator.create_asset_field(df_b)['data_source'])

        assert sources_a == {'ServiceNow', 'SentinelOne'}
        assert sources_b == {'ServiceNow', 'SentinelOne'}

    def test_intra_row_cross_product_is_still_deduplicated(self):
        """Guard: the original intent must be preserved. A single source record whose
        array columns explode into a cross-product with exact-duplicate identifier tuples
        must still be collapsed (no row explosion noise)."""
        validator = AssetValidator()
        # One source record, hostname repeated in the array -> explode yields duplicate
        # (hostname, ip, mac, sn) rows that share the same data_source.
        df = pd.DataFrame({
            'hostname': [['HOSTX', 'HOSTX']],
            'fqdn': [None],
            'ip': [['10.0.0.5']],
            'mac': [['aa:bb:cc:dd:ee:01']],
            'sn': [['REALSERIAL123']],
            'data_source': ['ServiceNow'],
        })

        result = validator.create_asset_field(df)

        assert len(result) == 1, (
            f"Intra-record cross-product duplicates must still collapse; got {len(result)} rows"
        )
