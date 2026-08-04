# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for AssetEnricher.enrich_ip_classification().

Covers: private/public/mixed/none classification, CGNAT (RFC 6598),
IPv6 ULA, invalid IPs silently dropped, multiple rows.
"""

import pandas as pd
import pytest

from src.modules.asset_generation.domain.asset_enricher import AssetEnricher


@pytest.fixture
def enricher():
    return AssetEnricher(postgres_adapter=None)


# ---------------------------------------------------------------------------
# Private-only cases
# ---------------------------------------------------------------------------

class TestPrivateOnly:
    def test_rfc1918_10_network(self, enricher):
        df = pd.DataFrame({"ip": [["10.0.1.5", "10.255.255.255"]]})
        result = enricher.enrich_ip_classification(df)
        assert result["ip_type"].iloc[0] == "private_only"

    def test_rfc1918_172_network(self, enricher):
        df = pd.DataFrame({"ip": [["172.16.0.1", "172.31.255.255"]]})
        result = enricher.enrich_ip_classification(df)
        assert result["ip_type"].iloc[0] == "private_only"

    def test_rfc1918_192_network(self, enricher):
        df = pd.DataFrame({"ip": [["192.168.1.1"]]})
        result = enricher.enrich_ip_classification(df)
        assert result["ip_type"].iloc[0] == "private_only"

    def test_cgnat_rfc6598(self, enricher):
        df = pd.DataFrame({"ip": [["100.64.0.1", "100.127.255.254"]]})
        result = enricher.enrich_ip_classification(df)
        assert result["ip_type"].iloc[0] == "private_only"

    def test_ipv6_ula(self, enricher):
        df = pd.DataFrame({"ip": [["fd12:3456:789a::1"]]})
        result = enricher.enrich_ip_classification(df)
        assert result["ip_type"].iloc[0] == "private_only"

    def test_cidr_suffix_stripped(self, enricher):
        """IPs with CIDR notation (e.g. /32) are classified correctly."""
        df = pd.DataFrame({"ip": [["10.0.0.1/32", "192.168.1.1/24"]]})
        result = enricher.enrich_ip_classification(df)
        assert result["ip_private"].iloc[0] == ["10.0.0.1/32", "192.168.1.1/24"]
        assert result["ip_type"].iloc[0] == "private_only"

    def test_cidr_mixed(self, enricher):
        df = pd.DataFrame({"ip": [["10.0.0.1/32", "8.8.8.8/32"]]})
        result = enricher.enrich_ip_classification(df)
        assert result["ip_type"].iloc[0] == "mixed"


# ---------------------------------------------------------------------------
# Public-only cases
# ---------------------------------------------------------------------------

class TestPublicOnly:
    def test_public_ips(self, enricher):
        # 203.0.113.42 is a TEST-NET-3 documentation address — not globally routable
        # in Python 3.13. Use addresses that are genuinely globally routable.
        df = pd.DataFrame({"ip": [["8.8.8.8", "1.1.1.1"]]})
        result = enricher.enrich_ip_classification(df)
        assert result["ip_type"].iloc[0] == "public_only"


# ---------------------------------------------------------------------------
# Mixed cases
# ---------------------------------------------------------------------------

class TestMixed:
    def test_mixed_ips(self, enricher):
        df = pd.DataFrame({"ip": [["10.0.1.5", "8.8.8.8"]]})
        result = enricher.enrich_ip_classification(df)
        assert result["ip_type"].iloc[0] == "mixed"

    def test_invalid_ip_skipped(self, enricher):
        """Invalid IPs are silently dropped; valid ones still determine type."""
        df = pd.DataFrame({"ip": [["10.0.1.5", "not-an-ip", "8.8.8.8"]]})
        result = enricher.enrich_ip_classification(df)
        assert result["ip_type"].iloc[0] == "mixed"


# ---------------------------------------------------------------------------
# None cases
# ---------------------------------------------------------------------------

class TestNone:
    def test_empty_ip_array(self, enricher):
        df = pd.DataFrame({"ip": [[]]})
        result = enricher.enrich_ip_classification(df)
        assert result["ip_type"].iloc[0] == "none"

    def test_nan_ip(self, enricher):
        df = pd.DataFrame({"ip": [None]})
        result = enricher.enrich_ip_classification(df)
        assert result["ip_type"].iloc[0] == "none"


# ---------------------------------------------------------------------------
# Multi-row
# ---------------------------------------------------------------------------

class TestMultipleRows:
    def test_multiple_rows(self, enricher):
        """All four ip_type values appear in a multi-row DataFrame."""
        df = pd.DataFrame({
            "ip": [
                ["10.0.0.1"],           # private_only
                ["8.8.8.8"],            # public_only
                ["10.0.0.1", "8.8.8.8"],  # mixed
                [],                     # none
            ]
        })
        result = enricher.enrich_ip_classification(df)
        assert set(result["ip_type"]) == {"private_only", "public_only", "mixed", "none"}


# ---------------------------------------------------------------------------
# Output columns present
# ---------------------------------------------------------------------------

class TestOutputColumns:
    def test_output_columns_exist(self, enricher):
        df = pd.DataFrame({"ip": [["10.0.0.1", "8.8.8.8"]]})
        result = enricher.enrich_ip_classification(df)
        assert "ip_private" in result.columns
        assert "ip_public" in result.columns
        assert "ip_type" in result.columns

    def test_private_public_lists_correct(self, enricher):
        df = pd.DataFrame({"ip": [["10.0.0.1", "8.8.8.8"]]})
        result = enricher.enrich_ip_classification(df)
        assert result["ip_private"].iloc[0] == ["10.0.0.1"]
        assert result["ip_public"].iloc[0] == ["8.8.8.8"]

    def test_does_not_mutate_input(self, enricher):
        df = pd.DataFrame({"ip": [["10.0.0.1"]]})
        original_cols = list(df.columns)
        enricher.enrich_ip_classification(df)
        assert list(df.columns) == original_cols
