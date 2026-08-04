# SPDX-License-Identifier: AGPL-3.0-or-later
"""QA-14: Test that preprocessing deduplicates identical provider rows.

Assets like prismmedicals11 accumulate 391 provider records from 2 sources
because the connector creates a new row for each scan. The preprocessing
stage should collapse rows where ALL asset attributes are identical, while
preserving rows that differ in any attribute (e.g. different MAC).
"""
import pytest
import pandas as pd
import sys
sys.path.insert(0, '.')

from src.modules.asset_generation.domain.asset_enricher import AssetEnricher


@pytest.fixture
def enricher():
    return AssetEnricher()


class TestProviderDedup:
    """Preprocessing should deduplicate identical rows from the same source."""

    def test_identical_rows_collapsed(self, enricher):
        """Multiple identical rows from same source → keep one."""
        df = pd.DataFrame([
            {'hostname': 'server01', 'ip': '10.0.0.1', 'mac': None,
             'fqdn': None, 'sn': None, 'os': 'Windows',
             'manufacturer': None, 'model': None, 'vendor_category': None,
             'data_source': 'qualys'},
            {'hostname': 'server01', 'ip': '10.0.0.1', 'mac': None,
             'fqdn': None, 'sn': None, 'os': 'Windows',
             'manufacturer': None, 'model': None, 'vendor_category': None,
             'data_source': 'qualys'},
            {'hostname': 'server01', 'ip': '10.0.0.1', 'mac': None,
             'fqdn': None, 'sn': None, 'os': 'Windows',
             'manufacturer': None, 'model': None, 'vendor_category': None,
             'data_source': 'qualys'},
        ])
        result = enricher.preprocess(df)
        assert len(result) == 1

    def test_different_mac_not_collapsed(self, enricher):
        """Same hostname+source but different MAC → keep both."""
        df = pd.DataFrame([
            {'hostname': 'server01', 'ip': '10.0.0.1',
             'mac': '00:11:22:33:44:55', 'fqdn': None, 'sn': None,
             'os': 'Windows', 'manufacturer': None, 'model': None,
             'vendor_category': None, 'data_source': 'qualys'},
            {'hostname': 'server01', 'ip': '10.0.0.1',
             'mac': 'AA:BB:CC:DD:EE:FF', 'fqdn': None, 'sn': None,
             'os': 'Windows', 'manufacturer': None, 'model': None,
             'vendor_category': None, 'data_source': 'qualys'},
        ])
        result = enricher.preprocess(df)
        assert len(result) == 2

    def test_different_source_not_collapsed(self, enricher):
        """Same attributes but different source → keep both."""
        df = pd.DataFrame([
            {'hostname': 'server01', 'ip': '10.0.0.1', 'mac': None,
             'fqdn': None, 'sn': None, 'os': 'Windows',
             'manufacturer': None, 'model': None, 'vendor_category': None,
             'data_source': 'qualys'},
            {'hostname': 'server01', 'ip': '10.0.0.1', 'mac': None,
             'fqdn': None, 'sn': None, 'os': 'Windows',
             'manufacturer': None, 'model': None, 'vendor_category': None,
             'data_source': 'crowdstrike'},
        ])
        result = enricher.preprocess(df)
        assert len(result) == 2

    def test_different_ip_not_collapsed(self, enricher):
        """Same hostname+source but different IP → keep both."""
        df = pd.DataFrame([
            {'hostname': 'server01', 'ip': '10.0.0.1', 'mac': None,
             'fqdn': None, 'sn': None, 'os': 'Windows',
             'manufacturer': None, 'model': None, 'vendor_category': None,
             'data_source': 'qualys'},
            {'hostname': 'server01', 'ip': '10.0.0.2', 'mac': None,
             'fqdn': None, 'sn': None, 'os': 'Windows',
             'manufacturer': None, 'model': None, 'vendor_category': None,
             'data_source': 'qualys'},
        ])
        result = enricher.preprocess(df)
        assert len(result) == 2

    def test_large_dedup_scenario(self, enricher):
        """Simulates Qualys-like accumulation: 200 identical scans → 1 row."""
        row = {
            'hostname': 'prismmedicals11', 'ip': '10.1.2.3', 'mac': None,
            'fqdn': 'prismmedicals11.corp.local', 'sn': None,
            'os': 'Windows Server 2019', 'manufacturer': None,
            'model': None, 'vendor_category': None,
            'data_source': 'qualys',
        }
        df = pd.DataFrame([row] * 200)
        result = enricher.preprocess(df)
        assert len(result) == 1

    def test_mixed_dedup_and_unique(self, enricher):
        """Mix of duplicates and unique rows."""
        df = pd.DataFrame([
            # 3 identical qualys rows
            {'hostname': 'srv01', 'ip': '10.0.0.1', 'mac': None,
             'fqdn': None, 'sn': None, 'os': 'Windows',
             'manufacturer': None, 'model': None, 'vendor_category': None,
             'data_source': 'qualys'},
            {'hostname': 'srv01', 'ip': '10.0.0.1', 'mac': None,
             'fqdn': None, 'sn': None, 'os': 'Windows',
             'manufacturer': None, 'model': None, 'vendor_category': None,
             'data_source': 'qualys'},
            {'hostname': 'srv01', 'ip': '10.0.0.1', 'mac': None,
             'fqdn': None, 'sn': None, 'os': 'Windows',
             'manufacturer': None, 'model': None, 'vendor_category': None,
             'data_source': 'qualys'},
            # 1 unique crowdstrike row
            {'hostname': 'srv01', 'ip': '10.0.0.1', 'mac': None,
             'fqdn': None, 'sn': 'ABC123', 'os': 'Windows',
             'manufacturer': 'Dell', 'model': 'PowerEdge', 'vendor_category': None,
             'data_source': 'crowdstrike'},
            # 2 identical entra rows
            {'hostname': 'srv02', 'ip': None, 'mac': None,
             'fqdn': None, 'sn': None, 'os': 'Windows',
             'manufacturer': None, 'model': None, 'vendor_category': None,
             'data_source': 'entra'},
            {'hostname': 'srv02', 'ip': None, 'mac': None,
             'fqdn': None, 'sn': None, 'os': 'Windows',
             'manufacturer': None, 'model': None, 'vendor_category': None,
             'data_source': 'entra'},
        ])
        result = enricher.preprocess(df)
        # 1 qualys + 1 crowdstrike + 1 entra = 3
        assert len(result) == 3
