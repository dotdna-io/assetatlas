# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for #287 — sub-categorize 'Insufficient data quality' exclusion reasons.

Verifies that _determine_exclusion_reason produces specific reasons instead
of the catch-all, and that _determine_exclusion_category maps them correctly.

Uses mock-based import to avoid pulling in heavy deps (joblib, etc.)
from the full AssetGenerationService module.
"""

import sys
import types
from unittest.mock import MagicMock

import pandas as pd
import pytest

# Stub out heavy transitive imports so we can load the service module
_STUBS = [
    'joblib', 'catboost', 'sklearn', 'sklearn.tree', 'sklearn.preprocessing',
    'sentence_transformers',
]
_saved = {}
for mod_name in _STUBS:
    if mod_name not in sys.modules:
        _saved[mod_name] = None
        sys.modules[mod_name] = types.ModuleType(mod_name)
    else:
        _saved[mod_name] = sys.modules[mod_name]


from src.modules.asset_generation.domain.asset_validator import AssetValidator  # noqa: E402


# Import only the class; constructor won't run (we bind methods to a mock)
from src.modules.asset_generation.services.asset_generation_service import (  # noqa: E402
    AssetGenerationService,
)

# Restore original modules
for mod_name, original in _saved.items():
    if original is None:
        sys.modules.pop(mod_name, None)
    else:
        sys.modules[mod_name] = original


_EXC_SETS = {
    'hostnames': {'multi-sn-host'},
    'fqdns': set(),
    'ips': {'10.0.0.1'},
    'macs': {'aa:bb:cc:dd:ee:ff'},
    'sns': {'overused123'},
}

_EMPTY_EXC_SETS = {
    'hostnames': set(), 'fqdns': set(), 'ips': set(),
    'macs': set(), 'sns': set(),
}


@pytest.fixture
def service():
    """Create a thin mock with real validator and bound exclusion methods."""
    validator = AssetValidator()

    svc = MagicMock()
    svc.validator = validator
    svc._determine_exclusion_reason = (
        AssetGenerationService._determine_exclusion_reason.__get__(svc)
    )
    svc._determine_exclusion_category = (
        AssetGenerationService._determine_exclusion_category.__get__(svc)
    )
    return svc


def _row(**kwargs):
    """Build a pd.Series representing an excluded row (asset field empty)."""
    defaults = {
        'hostname': pd.NA,
        'ip': pd.NA,
        'mac': pd.NA,
        'sn': pd.NA,
        'fqdn': pd.NA,
        'asset': '',
        'manufacturer': pd.NA,
    }
    defaults.update(kwargs)
    return pd.Series(defaults)


class TestExclusionReasonSubcategorization:
    """Verify specific reasons replace the catch-all."""

    def test_all_null_fields(self, service):
        reason = service._determine_exclusion_reason(_row(), _EXC_SETS)
        assert "All identifier fields null" in reason

    def test_hostname_too_short(self, service):
        reason = service._determine_exclusion_reason(_row(hostname='abc'), _EXC_SETS)
        assert "Hostname too short" in reason

    def test_generic_placeholder_hostname(self, service):
        reason = service._determine_exclusion_reason(_row(hostname='iphone'), _EXC_SETS)
        assert "Generic placeholder hostname" in reason

    def test_hostname_no_letters(self, service):
        reason = service._determine_exclusion_reason(_row(hostname='12345'), _EXC_SETS)
        assert "Hostname has no letters" in reason

    def test_hostname_date_pattern(self, service):
        reason = service._determine_exclusion_reason(_row(hostname='host-5/8/2025'), _EXC_SETS)
        assert "Hostname contains date pattern" in reason

    def test_hostname_matched_placeholder_pattern(self, service):
        reason = service._determine_exclusion_reason(
            _row(hostname='DESKTOP-ABC1234'), _EXC_SETS
        )
        assert "Hostname matched placeholder pattern" in reason

    def test_hostname_excluded_multiple_sns(self, service):
        reason = service._determine_exclusion_reason(
            _row(hostname='multi-sn-host'), _EXC_SETS
        )
        assert "associated with multiple serial numbers" in reason

    def test_hostname_matches_manufacturer(self, service):
        reason = service._determine_exclusion_reason(
            _row(hostname='LENOVO', manufacturer='Lenovo'), _EXC_SETS
        )
        assert "matches manufacturer name" in reason

    def test_sn_overused(self, service):
        reason = service._determine_exclusion_reason(
            _row(sn='OVERUSED123'), _EXC_SETS
        )
        assert "overused across hostnames" in reason

    def test_sn_suspicious_placeholder(self, service):
        reason = service._determine_exclusion_reason(
            _row(sn='unknown'), _EXC_SETS
        )
        assert "suspicious/placeholder" in reason

    def test_ip_loopback(self, service):
        reason = service._determine_exclusion_reason(
            _row(ip='127.0.0.1'), _EXC_SETS
        )
        assert "loopback/APIPA" in reason

    def test_ip_excluded_multiple_hostnames(self, service):
        reason = service._determine_exclusion_reason(
            _row(ip='10.0.0.1'), _EXC_SETS
        )
        assert "associated with multiple hostnames" in reason

    def test_mac_broadcast(self, service):
        reason = service._determine_exclusion_reason(
            _row(mac='ff:ff:ff:ff:ff:ff'), _EXC_SETS
        )
        assert "broadcast/zero" in reason

    def test_mac_excluded_multiple_hostnames(self, service):
        reason = service._determine_exclusion_reason(
            _row(mac='aa:bb:cc:dd:ee:ff'), _EXC_SETS
        )
        assert "associated with multiple hostnames" in reason

    def test_ip_only_transient(self, service):
        reason = service._determine_exclusion_reason(
            _row(ip='192.168.1.1'), _EXC_SETS
        )
        assert "Only IP address (transient identifier)" in reason

    def test_missing_hostname_and_sn(self, service):
        """Has MAC but no hostname/SN — should say missing stable identifiers."""
        reason = service._determine_exclusion_reason(
            _row(mac='11:22:33:44:55:66'), _EXC_SETS
        )
        assert "Missing hostname and serial number" in reason

    def test_components_filtered_during_validation(self, service):
        """Non-empty asset field → 'Components filtered during validation'."""
        reason = service._determine_exclusion_reason(
            _row(asset='some-host|sn:ABC123'), _EXC_SETS
        )
        assert reason == "Components filtered during validation"

    def test_multiple_reasons_combined(self, service):
        """Record with multiple failing components should list all reasons."""
        reason = service._determine_exclusion_reason(
            _row(hostname='abc', sn='unknown', ip='127.0.0.1'), _EXC_SETS
        )
        assert "Hostname too short" in reason
        assert "suspicious/placeholder" in reason
        assert "loopback/APIPA" in reason

    def test_no_catch_all_for_known_patterns(self, service):
        """Verify known exclusion patterns do NOT produce 'Insufficient data quality'."""
        cases = [
            _row(),  # all null
            _row(hostname='abc'),  # too short
            _row(hostname='iphone'),  # generic
            _row(hostname='multi-sn-host'),  # cross-ref excluded
            _row(sn='OVERUSED123'),  # overused SN
            _row(sn='unknown'),  # suspicious SN
            _row(ip='127.0.0.1'),  # loopback
            _row(ip='10.0.0.1'),  # cross-ref excluded IP
            _row(mac='ff:ff:ff:ff:ff:ff'),  # broadcast
            _row(mac='aa:bb:cc:dd:ee:ff'),  # cross-ref excluded MAC
            _row(ip='192.168.1.1'),  # IP-only
            _row(mac='11:22:33:44:55:66'),  # MAC-only, no stable IDs
            _row(hostname='DESKTOP-ABC1234'),  # placeholder pattern
        ]
        for row in cases:
            reason = service._determine_exclusion_reason(row, _EXC_SETS)
            assert reason != "Insufficient data quality", (
                f"Got catch-all for: {row.to_dict()}"
            )

    def test_fallback_with_empty_exclusion_sets(self, service):
        """Without exclusion sets, valid components still get diagnosed."""
        reason = service._determine_exclusion_reason(
            _row(sn='unknown'), _EMPTY_EXC_SETS
        )
        assert "suspicious/placeholder" in reason


class TestExclusionCategory:
    """Verify _determine_exclusion_category maps new reasons correctly."""

    def test_hostname_placeholder_pattern(self, service):
        cat = service._determine_exclusion_category(
            "Hostname matched placeholder pattern ('DESKTOP-ABC1234')"
        )
        assert cat == "hostname_quality"

    def test_hostname_manufacturer_match(self, service):
        cat = service._determine_exclusion_category(
            "Hostname matches manufacturer name ('LENOVO')"
        )
        assert cat == "hostname_quality"

    def test_hostname_format_validation(self, service):
        cat = service._determine_exclusion_category(
            "Hostname failed format validation ('bad host')"
        )
        assert cat == "hostname_quality"

    def test_hostname_cross_ref(self, service):
        cat = service._determine_exclusion_category(
            "Hostname excluded (associated with multiple serial numbers)"
        )
        assert cat == "cross_reference_exclusion"

    def test_sn_quality(self, service):
        cat = service._determine_exclusion_category(
            "Serial number failed validation (suspicious/placeholder)"
        )
        assert cat == "sn_quality"

    def test_sn_overused(self, service):
        cat = service._determine_exclusion_category(
            "Serial number excluded (overused across hostnames)"
        )
        assert cat == "sn_quality"

    def test_ip_cross_ref(self, service):
        cat = service._determine_exclusion_category(
            "IP excluded (associated with multiple hostnames)"
        )
        assert cat == "cross_reference_exclusion"

    def test_mac_cross_ref(self, service):
        cat = service._determine_exclusion_category(
            "MAC excluded (associated with multiple hostnames)"
        )
        assert cat == "cross_reference_exclusion"

    def test_mac_virtual(self, service):
        cat = service._determine_exclusion_category(
            "Virtual MAC address (VMware)"
        )
        assert cat == "mac_quality"

    def test_all_null(self, service):
        cat = service._determine_exclusion_category(
            "All identifier fields null"
        )
        assert cat == "all_null"

    def test_transient(self, service):
        cat = service._determine_exclusion_category(
            "Only IP address (transient identifier)"
        )
        assert cat == "transient_only"

    def test_missing_stable_ids(self, service):
        cat = service._determine_exclusion_category(
            "Missing hostname and serial number"
        )
        assert cat == "insufficient_data"
