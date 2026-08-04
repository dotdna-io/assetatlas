# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for provider-level deduplication (#283).

Verifies that preprocess_for_providers() collapses duplicate provider records
by (asset_id, data_source, hostname, mac, sn, ip) while preserving distinct
records (different NICs, different sources, different IPs).
"""
import pytest
import pandas as pd
from unittest.mock import MagicMock

from src.modules.asset_generation.repositories.provider_repository import (
    ProviderRepository,
)
from src.modules.asset_generation.domain.asset_validator import AssetValidator


@pytest.fixture
def provider_repo():
    db_adapter = MagicMock()
    return ProviderRepository(db_adapter)


def _make_provider_row(**overrides):
    """Build a minimal enriched-df row that survives preprocess_for_providers."""
    base = {
        'id': 'aaaaaaaa-1111-2222-3333-444444444444',
        'data_source': 'Acme Ranger',
        'data_source_category': 'network',
        'integration_id': 'int-001',
        'hostname': 'watch',
        'fqdn': None,
        'ip': '10.0.0.1',
        'mac': '00:11:22:33:44:55',
        'sn': None,
        'os': 'watchos',
        'manufacturer': 'Apple',
        'model': 'Apple Watch',
        'vendor_category': 'Smart Watch',
        'os_normalized': 'watchOS',
    }
    base.update(overrides)
    return base


class TestProviderDedup:
    """preprocess_for_providers should collapse duplicate provider records."""

    def test_identical_ranger_records_collapsed(self, provider_repo):
        """41 identical Ranger rows for same device → 1 provider record."""
        row = _make_provider_row()
        df = pd.DataFrame([row] * 41)
        result = provider_repo.preprocess_for_providers(df)
        assert len(result) == 1

    def test_keeps_last_record(self, provider_repo):
        """Dedup keeps the last (most recently ingested) record."""
        rows = [
            _make_provider_row(os='watchos 9'),
            _make_provider_row(os='watchos 10'),
            _make_provider_row(os='watchos 11'),
        ]
        df = pd.DataFrame(rows)
        result = provider_repo.preprocess_for_providers(df)
        assert len(result) == 1
        assert result.iloc[0]['os'] == 'watchos 11'

    def test_different_ip_preserved(self, provider_repo):
        """Same device, different IPs (DHCP) → separate provider records."""
        rows = [
            _make_provider_row(ip='10.0.0.1'),
            _make_provider_row(ip='10.0.0.2'),
        ]
        df = pd.DataFrame(rows)
        result = provider_repo.preprocess_for_providers(df)
        assert len(result) == 2

    def test_different_mac_preserved(self, provider_repo):
        """Same device, different MACs (multi-NIC mvexpand) → kept."""
        rows = [
            _make_provider_row(mac='00:11:22:33:44:55', ip='10.0.0.1'),
            _make_provider_row(mac='AA:BB:CC:DD:EE:FF', ip='10.0.0.2'),
        ]
        df = pd.DataFrame(rows)
        result = provider_repo.preprocess_for_providers(df)
        assert len(result) == 2

    def test_different_source_preserved(self, provider_repo):
        """Same asset from different sources → kept."""
        rows = [
            _make_provider_row(data_source='Acme Ranger'),
            _make_provider_row(data_source='Acme SentinelOne'),
        ]
        df = pd.DataFrame(rows)
        result = provider_repo.preprocess_for_providers(df)
        assert len(result) == 2

    def test_different_asset_id_preserved(self, provider_repo):
        """Different assets, same source → kept."""
        rows = [
            _make_provider_row(id='aaaaaaaa-1111-2222-3333-444444444444'),
            _make_provider_row(id='bbbbbbbb-1111-2222-3333-444444444444'),
        ]
        df = pd.DataFrame(rows)
        result = provider_repo.preprocess_for_providers(df)
        assert len(result) == 2

    def test_mixed_duplicates_and_unique(self, provider_repo):
        """Mix: 5 identical Ranger + 3 identical S1 + 1 unique → 3 records."""
        rows = (
            [_make_provider_row()] * 5
            + [_make_provider_row(
                data_source='Acme SentinelOne',
                hostname='iMac, uMac, weMac',
                mac='AA:BB:CC:DD:EE:FF',
                os='mac os x',
            )] * 3
            + [_make_provider_row(
                hostname='workstation01',
                mac='11:22:33:44:55:66',
                ip='10.1.1.1',
            )]
        )
        df = pd.DataFrame(rows)
        result = provider_repo.preprocess_for_providers(df)
        assert len(result) == 3


class TestWatchGenericHostname:
    """'watch' should be in the generic hostname blocklist (#283)."""

    def test_watch_in_generic_hostnames(self):
        validator = AssetValidator()
        assert 'watch' in validator.generic_hostnames

    def test_watch_rejected_by_is_valid_hostname(self):
        validator = AssetValidator()
        assert validator.is_valid_hostname('watch') is False
        assert validator.is_valid_hostname('Watch') is False
        assert validator.is_valid_hostname('WATCH') is False

    def test_watch_prefix_still_valid(self):
        """Hostnames starting with 'watch' but longer should still be valid."""
        validator = AssetValidator()
        assert validator.is_valid_hostname('watchguard-fw01') is True
        assert validator.is_valid_hostname('watchtower') is True
