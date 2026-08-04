# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Comprehensive unit tests for AssetScorer V2.

Extends coverage beyond the existing test_asset_scorer.py to achieve 85%+
coverage of asset_scorer.py. Tests all gradient methods, gate methods,
utility methods, and integration scenarios.
"""

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from src.modules.asset_generation.domain.asset_scorer import AssetScorer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def scorer():
    """Default AssetScorer with all gates enabled."""
    return AssetScorer()


@pytest.fixture
def scorer_no_gates():
    """AssetScorer with all gates disabled."""
    return AssetScorer(
        enable_recency_penalty=False,
        enable_identifier_cap=False,
        enable_consistency_penalty=False,
    )


def _make_row(**kwargs):
    """Helper: create a pd.Series that looks like an asset row."""
    return pd.Series(kwargs)


# ---------------------------------------------------------------------------
# _calculate_source_reliability
# ---------------------------------------------------------------------------

class TestSourceReliability:
    """Tests for _calculate_source_reliability."""

    def test_no_source_info_returns_neutral(self, scorer):
        """No data_source_category returns 0.5."""
        row = _make_row(data_source=[], data_source_category=[])
        assert scorer._calculate_source_reliability(row) == 0.5

    def test_single_authoritative_source_without_penalty(self, scorer):
        """Single MDM source gets base score — no single-source penalty."""
        row = _make_row(
            data_source=["intune"],
            data_source_category=["mobile device management"],
        )
        result = scorer._calculate_source_reliability(row)
        # MDM base = 0.85, no single-source penalty applied
        assert result == pytest.approx(0.85, abs=0.01)

    def test_multiple_authoritative_sources_bonus(self, scorer):
        """Two authoritative sources get +0.10 bonus."""
        row = _make_row(
            data_source=["intune", "azure"],
            data_source_category=["mobile device management", "cloud platform"],
        )
        result = scorer._calculate_source_reliability(row)
        base = np.mean([0.85, 0.90])  # 0.875
        expected = min(1.0, base + 0.10)  # 0.975
        assert result == pytest.approx(expected, abs=0.01)

    def test_three_diverse_categories_bonus(self, scorer):
        """Three or more unique categories get +0.05 bonus when all categories covered."""
        row = _make_row(
            data_source=["src1", "src2", "src3"],
            data_source_category=[
                "endpoint security",
                "vulnerability management",
                "observability platform",
            ],
        )
        # With available_cats=3, diversity threshold = max(2, 3-1) = 2, so 3 qualifies
        # available_auth=2 so partial auth bonus doesn't trigger (auth_count=1 < 2)
        result = scorer._calculate_source_reliability(row, available_cats=3, available_auth=2)
        base = np.mean([0.95, 0.70, 0.70])  # ~0.7833
        expected = min(1.0, base + 0.05)     # ~0.8333
        assert result == pytest.approx(expected, abs=0.01)

    def test_authoritative_and_diversity_bonuses_stack(self, scorer):
        """Both bonuses apply additively, capped once at end (#56)."""
        row = _make_row(
            data_source=["intune", "azure", "defender"],
            data_source_category=[
                "mobile device management",
                "cloud platform",
                "endpoint security",
            ],
        )
        result = scorer._calculate_source_reliability(row)
        base = np.mean([0.85, 0.90, 0.95])  # 0.90
        base += 0.10                          # auth bonus (2+ authoritative)
        base += 0.05                          # diversity bonus (3 categories)
        expected = min(1.0, base)             # cap once at end -> 1.0
        assert result == pytest.approx(expected, abs=0.01)

    def test_unknown_category_falls_back_to_0_5(self, scorer):
        """An unknown category key gets fallback score 0.5."""
        row = _make_row(
            data_source=["custom_tool", "another"],
            data_source_category=["totally_unknown", "also_unknown"],
        )
        result = scorer._calculate_source_reliability(row)
        # mean([0.5, 0.5]) = 0.5, no bonuses, no single-source penalty (2 sources)
        assert result == pytest.approx(0.5, abs=0.01)

    def test_string_data_source_converted_to_list(self, scorer):
        """Scalar data_source string is wrapped in a list."""
        row = _make_row(
            data_source="intune",
            data_source_category="mobile device management",
        )
        result = scorer._calculate_source_reliability(row)
        # Single source: MDM base = 0.85, no penalty
        assert result == pytest.approx(0.85, abs=0.01)

    def test_nan_data_source_treated_as_empty(self, scorer):
        """NaN data_source should be treated as empty list."""
        row = _make_row(
            data_source=np.nan,
            data_source_category=np.nan,
        )
        result = scorer._calculate_source_reliability(row)
        assert result == 0.5

    def test_missing_data_source_fields(self, scorer):
        """Row with no data_source or data_source_category fields."""
        row = _make_row(hostname="test-host")
        result = scorer._calculate_source_reliability(row)
        assert result == 0.5

    def test_custom_source_scores_used(self):
        """Custom source_reliability_scores override defaults."""
        custom = {"custom_category": 0.99}
        scorer = AssetScorer(source_reliability_scores=custom)
        row = _make_row(
            data_source=["tool1", "tool2"],
            data_source_category=["custom_category", "custom_category"],
        )
        result = scorer._calculate_source_reliability(row)
        assert result == pytest.approx(0.99, abs=0.01)


# ---------------------------------------------------------------------------
# _calculate_source_count_score
# ---------------------------------------------------------------------------

class TestSourceCountScore:
    """Tests for _calculate_source_count_score."""

    def test_zero_sources(self, scorer):
        row = _make_row(data_source=[])
        assert scorer._calculate_source_count_score(row) == 0.0

    def test_one_source(self, scorer):
        row = _make_row(data_source=["src1"])
        expected = min(1.0, math.sqrt(1) / math.sqrt(5))  # ~0.4472
        assert scorer._calculate_source_count_score(row) == pytest.approx(expected, abs=0.01)

    def test_two_sources(self, scorer):
        row = _make_row(data_source=["src1", "src2"])
        expected = min(1.0, math.sqrt(2) / math.sqrt(5))  # ~0.6325
        assert scorer._calculate_source_count_score(row) == pytest.approx(expected, abs=0.01)

    def test_five_sources_equals_one(self, scorer):
        row = _make_row(data_source=[f"src{i}" for i in range(5)])
        assert scorer._calculate_source_count_score(row) == pytest.approx(1.0, abs=0.01)

    def test_ten_sources_still_capped_at_one(self, scorer):
        row = _make_row(data_source=[f"src{i}" for i in range(10)])
        assert scorer._calculate_source_count_score(row) == pytest.approx(1.0, abs=0.01)

    def test_non_list_data_source(self, scorer):
        """Scalar data_source string is wrapped in a list (1 source)."""
        row = _make_row(data_source="single_source")
        expected = min(1.0, math.sqrt(1) / math.sqrt(5))
        assert scorer._calculate_source_count_score(row) == pytest.approx(expected, abs=0.01)

    def test_nan_data_source(self, scorer):
        """NaN data_source should be treated as empty."""
        row = _make_row(data_source=np.nan)
        assert scorer._calculate_source_count_score(row) == 0.0

    def test_missing_data_source_field(self, scorer):
        """No data_source field at all defaults to empty."""
        row = _make_row(hostname="test")
        assert scorer._calculate_source_count_score(row) == 0.0


# ---------------------------------------------------------------------------
# _calculate_data_completeness
# ---------------------------------------------------------------------------

class TestDataCompleteness:
    """Tests for _calculate_data_completeness."""

    def test_all_fields_present(self, scorer):
        """All tier 1/2/3 fields present gives 1.0."""
        row = _make_row(
            sn="SN001", hostname="host1", fqdn="host1.example.com", mac="AA:BB:CC:DD:EE:FF",
            ip="10.0.0.1", os_normalized="Windows 10", manufacturer="Dell", model="OptiPlex",
            location="NYC", owner="jdoe", category="Laptop",
            os_eol_date="2025-10-14", vendor_category="Enterprise",
        )
        assert scorer._calculate_data_completeness(row) == pytest.approx(1.0, abs=0.01)

    def test_no_fields_present(self, scorer):
        """No relevant fields gives 0.0."""
        row = _make_row(some_other_field="value")
        assert scorer._calculate_data_completeness(row) == 0.0

    def test_only_tier1_fields(self, scorer):
        """Only tier 1 fields: 4*3 = 12 out of 25."""
        row = _make_row(
            sn="SN001", hostname="host1", fqdn="host1.example.com", mac="AA:BB:CC:DD:EE:FF",
        )
        assert scorer._calculate_data_completeness(row) == pytest.approx(12 / 25, abs=0.01)

    def test_only_tier2_fields(self, scorer):
        """Only tier 2 fields: 4*2 = 8 out of 25."""
        row = _make_row(
            ip="10.0.0.1", os_normalized="Ubuntu", manufacturer="Lenovo", model="T14",
        )
        assert scorer._calculate_data_completeness(row) == pytest.approx(8 / 25, abs=0.01)

    def test_only_tier3_fields(self, scorer):
        """Only tier 3 fields: 5*1 = 5 out of 25."""
        row = _make_row(
            location="London", owner="admin", category="Server",
            os_eol_date="2025-01-01", vendor_category="Business",
        )
        assert scorer._calculate_data_completeness(row) == pytest.approx(5 / 25, abs=0.01)

    def test_mixed_tiers(self, scorer):
        """Mix of tiers: 2 tier1 + 1 tier2 + 1 tier3 = 6+2+1 = 9/25."""
        row = _make_row(
            sn="SN001", hostname="host1",   # tier1: 2*3 = 6
            ip="10.0.0.1",                  # tier2: 1*2 = 2
            owner="jdoe",                   # tier3: 1*1 = 1
        )
        assert scorer._calculate_data_completeness(row) == pytest.approx(9 / 25, abs=0.01)

    def test_nan_field_not_counted(self, scorer):
        """NaN values are not counted as present."""
        row = _make_row(sn=np.nan, hostname="host1")
        # Only hostname counted: 3/25
        assert scorer._calculate_data_completeness(row) == pytest.approx(3 / 25, abs=0.01)

    def test_empty_string_not_counted(self, scorer):
        """Empty strings are not counted as present."""
        row = _make_row(sn="", hostname="host1")
        assert scorer._calculate_data_completeness(row) == pytest.approx(3 / 25, abs=0.01)

    def test_list_field_with_values(self, scorer):
        """List fields with valid values are counted."""
        row = _make_row(sn=["SN001"], hostname=["host1"])
        assert scorer._calculate_data_completeness(row) == pytest.approx(6 / 25, abs=0.01)

    def test_empty_list_not_counted(self, scorer):
        """Empty list fields are not counted."""
        row = _make_row(sn=[], hostname="host1")
        assert scorer._calculate_data_completeness(row) == pytest.approx(3 / 25, abs=0.01)


# ---------------------------------------------------------------------------
# _calculate_consistency_penalty
# ---------------------------------------------------------------------------

class TestConsistencyPenalty:
    """Tests for _calculate_consistency_penalty."""

    def test_single_provider_no_penalty(self, scorer):
        """Single provider row returns 1.0."""
        providers_df = pd.DataFrame([
            {"id": "a1", "sn": "SN001", "hostname": "host1"},
        ])
        assert scorer._calculate_consistency_penalty(providers_df, ["a1"]) == 1.0

    def test_two_providers_agree(self, scorer):
        """Two providers with identical data return 1.0."""
        providers_df = pd.DataFrame([
            {"id": "a1", "sn": "SN001", "hostname": "host1"},
            {"id": "a2", "sn": "SN001", "hostname": "host1"},
        ])
        result = scorer._calculate_consistency_penalty(providers_df, ["a1", "a2"])
        assert result == 1.0

    def test_sn_disagreement(self, scorer):
        """SN disagreement applies 0.30 multiplier."""
        providers_df = pd.DataFrame([
            {"id": "a1", "sn": "SN001", "hostname": "host1"},
            {"id": "a2", "sn": "SN999", "hostname": "host1"},
        ])
        result = scorer._calculate_consistency_penalty(providers_df, ["a1", "a2"])
        assert result == pytest.approx(0.30, abs=0.01)

    def test_hostname_disagreement(self, scorer):
        """Hostname disagreement applies 0.60 multiplier."""
        providers_df = pd.DataFrame([
            {"id": "a1", "sn": "SN001", "hostname": "host1"},
            {"id": "a2", "sn": "SN001", "hostname": "host2"},
        ])
        result = scorer._calculate_consistency_penalty(providers_df, ["a1", "a2"])
        assert result == pytest.approx(0.60, abs=0.01)

    def test_multiple_disagreements_compound(self, scorer):
        """Multiple field disagreements multiply together."""
        providers_df = pd.DataFrame([
            {"id": "a1", "sn": "SN001", "hostname": "host1", "mac": "AA:BB:CC:DD:EE:01"},
            {"id": "a2", "sn": "SN999", "hostname": "host2", "mac": "AA:BB:CC:DD:EE:02"},
        ])
        result = scorer._calculate_consistency_penalty(providers_df, ["a1", "a2"])
        # sn=0.30 * hostname=0.60 * mac=0.95 = 0.171
        assert result == pytest.approx(0.30 * 0.60 * 0.95, abs=0.01)

    def test_three_unique_sn_values_full_penalty(self, scorer):
        """3 unique SN values (no majority) get full penalty."""
        providers_df = pd.DataFrame([
            {"id": "a1", "sn": "SN001"},
            {"id": "a2", "sn": "SN002"},
            {"id": "a3", "sn": "SN003"},
        ])
        result = scorer._calculate_consistency_penalty(providers_df, ["a1", "a2", "a3"])
        # All differ (agreement_ratio=0.33) -> full SN penalty = 0.30
        assert result == pytest.approx(0.30, abs=0.01)

    def test_three_unique_hostname_values_full_penalty(self, scorer):
        """3 unique hostname values (no majority) get full penalty."""
        providers_df = pd.DataFrame([
            {"id": "a1", "hostname": "host1"},
            {"id": "a2", "hostname": "host2"},
            {"id": "a3", "hostname": "host3"},
        ])
        result = scorer._calculate_consistency_penalty(providers_df, ["a1", "a2", "a3"])
        # All differ (agreement_ratio=0.33) -> full hostname penalty = 0.60
        assert result == pytest.approx(0.60, abs=0.01)

    def test_case_insensitive_hostname_comparison(self, scorer):
        """Hostname comparison is case-insensitive (no false mismatch)."""
        providers_df = pd.DataFrame([
            {"id": "a1", "hostname": "HostName"},
            {"id": "a2", "hostname": "hostname"},
        ])
        result = scorer._calculate_consistency_penalty(providers_df, ["a1", "a2"])
        assert result == 1.0

    def test_case_insensitive_fqdn_comparison(self, scorer):
        """FQDN comparison is case-insensitive."""
        providers_df = pd.DataFrame([
            {"id": "a1", "fqdn": "Host.Example.COM"},
            {"id": "a2", "fqdn": "host.example.com"},
        ])
        result = scorer._calculate_consistency_penalty(providers_df, ["a1", "a2"])
        assert result == 1.0

    def test_case_insensitive_os_normalized(self, scorer):
        """os_normalized comparison is case-insensitive."""
        providers_df = pd.DataFrame([
            {"id": "a1", "os_normalized": "Windows 10"},
            {"id": "a2", "os_normalized": "windows 10"},
        ])
        result = scorer._calculate_consistency_penalty(providers_df, ["a1", "a2"])
        assert result == 1.0

    def test_no_matching_providers(self, scorer):
        """No matching provider IDs returns 1.0 (empty filtered df, <=1 row)."""
        providers_df = pd.DataFrame([
            {"id": "other1", "sn": "SN001"},
        ])
        result = scorer._calculate_consistency_penalty(providers_df, ["a1"])
        assert result == 1.0

    def test_nan_values_excluded_from_comparison(self, scorer):
        """NaN values are dropped before comparison, no false disagreement."""
        providers_df = pd.DataFrame([
            {"id": "a1", "sn": "SN001", "hostname": "host1"},
            {"id": "a2", "sn": "SN001", "hostname": np.nan},
        ])
        result = scorer._calculate_consistency_penalty(providers_df, ["a1", "a2"])
        # hostname has only 1 non-null unique value -> no penalty
        assert result == 1.0

    def test_ip_disagreement_minor_penalty(self, scorer):
        """IP disagreement is minor (0.95 multiplier)."""
        providers_df = pd.DataFrame([
            {"id": "a1", "ip": "10.0.0.1"},
            {"id": "a2", "ip": "10.0.0.2"},
        ])
        result = scorer._calculate_consistency_penalty(providers_df, ["a1", "a2"])
        assert result == pytest.approx(0.95, abs=0.01)

    def test_category_disagreement_minor_penalty(self, scorer):
        """Category disagreement is minor (0.90 multiplier), case-insensitive."""
        providers_df = pd.DataFrame([
            {"id": "a1", "category": "Laptop"},
            {"id": "a2", "category": "Desktop"},
        ])
        result = scorer._calculate_consistency_penalty(providers_df, ["a1", "a2"])
        assert result == pytest.approx(0.90, abs=0.01)


# ---------------------------------------------------------------------------
# _calculate_recency_penalty
# ---------------------------------------------------------------------------

class TestRecencyPenalty:
    """Tests for _calculate_recency_penalty (smooth exponential decay, 90-day half-life)."""

    def test_fresh_data_no_penalty(self, scorer):
        """Data less than 1 day old returns 1.0."""
        last_seen = datetime.now(timezone.utc) - timedelta(hours=6)
        assert scorer._calculate_recency_penalty(last_seen) == 1.0

    def test_three_days_old(self, scorer):
        """3 days old ~ 0.977 (smooth decay)."""
        last_seen = datetime.now(timezone.utc) - timedelta(days=3)
        result = scorer._calculate_recency_penalty(last_seen)
        assert result == pytest.approx(0.977, abs=0.01)

    def test_one_day_boundary(self, scorer):
        """1 day old ~ 0.992."""
        last_seen = datetime.now(timezone.utc) - timedelta(days=1)
        result = scorer._calculate_recency_penalty(last_seen)
        assert result == pytest.approx(0.992, abs=0.01)

    def test_fifteen_days_old(self, scorer):
        """15 days old ~ 0.891."""
        last_seen = datetime.now(timezone.utc) - timedelta(days=15)
        result = scorer._calculate_recency_penalty(last_seen)
        assert result == pytest.approx(0.891, abs=0.01)

    def test_seven_day_boundary(self, scorer):
        """7 days old ~ 0.947."""
        last_seen = datetime.now(timezone.utc) - timedelta(days=7)
        result = scorer._calculate_recency_penalty(last_seen)
        assert result == pytest.approx(0.947, abs=0.01)

    def test_sixty_days_old(self, scorer):
        """60 days old ~ 0.630."""
        last_seen = datetime.now(timezone.utc) - timedelta(days=60)
        result = scorer._calculate_recency_penalty(last_seen)
        assert result == pytest.approx(0.630, abs=0.01)

    def test_thirty_day_boundary(self, scorer):
        """30 days old ~ 0.794."""
        last_seen = datetime.now(timezone.utc) - timedelta(days=30)
        result = scorer._calculate_recency_penalty(last_seen)
        assert result == pytest.approx(0.794, abs=0.01)

    def test_over_ninety_days(self, scorer):
        """120 days old hits floor at 0.40."""
        last_seen = datetime.now(timezone.utc) - timedelta(days=120)
        result = scorer._calculate_recency_penalty(last_seen)
        assert result == pytest.approx(0.40, abs=0.01)

    def test_ninety_day_boundary(self, scorer):
        """90 days old ~ 0.50 (half-life)."""
        last_seen = datetime.now(timezone.utc) - timedelta(days=90)
        result = scorer._calculate_recency_penalty(last_seen)
        assert result == pytest.approx(0.50, abs=0.01)

    def test_nan_last_seen(self, scorer):
        """NaN last_seen returns 0.95 (minor unknown-freshness penalty)."""
        assert scorer._calculate_recency_penalty(pd.NaT) == pytest.approx(0.95, abs=0.01)

    def test_timezone_naive_datetime(self, scorer):
        """Timezone-naive datetime is treated as UTC."""
        last_seen = datetime.now() - timedelta(days=3)  # naive
        result = scorer._calculate_recency_penalty(last_seen)
        assert result == pytest.approx(0.977, abs=0.01)

    def test_smooth_monotonic_decay(self, scorer):
        """Penalty decreases monotonically with age."""
        penalties = []
        for days in [0, 7, 15, 30, 60, 90, 120, 180]:
            last_seen = datetime.now(timezone.utc) - timedelta(days=days)
            penalties.append(scorer._calculate_recency_penalty(last_seen))
        # Each should be <= the previous
        for i in range(1, len(penalties)):
            assert penalties[i] <= penalties[i-1]

    def test_nan_last_seen_with_now(self, scorer):
        """NaN last_seen returns 0.95 via _with_now variant too."""
        now = datetime.now(timezone.utc)
        assert scorer._calculate_recency_penalty_with_now(pd.NaT, now) == pytest.approx(0.95, abs=0.01)

    def test_pre_2000_date_treated_as_unknown(self, scorer):
        """Dates before 2000 are impossible — treat as unknown freshness."""
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        assert scorer._calculate_recency_penalty(epoch) == pytest.approx(0.90, abs=0.01)

    def test_pre_2000_date_with_now(self, scorer):
        """Dates before 2000 via _with_now variant also return 0.90."""
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        assert scorer._calculate_recency_penalty_with_now(epoch, now) == pytest.approx(0.90, abs=0.01)


# ---------------------------------------------------------------------------
# _calculate_identifier_cap
# ---------------------------------------------------------------------------

class TestIdentifierCap:
    """Tests for _calculate_identifier_cap."""

    def test_sn_present_returns_1_0(self, scorer):
        """Serial number present = gold standard, cap 1.0."""
        row = _make_row(sn="SN123", hostname="host1", fqdn="host1.example.com", mac="AA:BB:CC:DD:EE:FF", ip="10.0.0.1")
        assert scorer._calculate_identifier_cap(row) == 1.0

    def test_fqdn_with_physical_mac_returns_0_95(self, scorer):
        """FQDN + physical MAC without SN returns 0.95."""
        row = _make_row(fqdn="host1.example.com", hostname="host1", mac="AA:BB:CC:DD:EE:FF", ip="10.0.0.1")
        assert scorer._calculate_identifier_cap(row) == 0.95

    def test_hostname_plus_physical_mac_returns_0_90(self, scorer):
        """Hostname + physical MAC (no SN, no FQDN) returns 0.90."""
        row = _make_row(hostname="host1", mac="AA:BB:CC:DD:EE:FF", ip="10.0.0.1")
        assert scorer._calculate_identifier_cap(row) == 0.90

    def test_hostname_only_returns_0_80(self, scorer):
        """Hostname only (no SN, FQDN, or MAC) returns 0.80."""
        row = _make_row(hostname="host1", ip="10.0.0.1")
        assert scorer._calculate_identifier_cap(row) == 0.80

    def test_hostname_with_virtual_mac_returns_0_80(self, scorer):
        """Hostname + virtual MAC returns 0.80 (virtual MAC not considered physical)."""
        row = _make_row(hostname="host1", mac="00:50:56:AA:BB:CC")  # VMware prefix
        assert scorer._calculate_identifier_cap(row) == 0.80

    def test_physical_mac_only_returns_0_70(self, scorer):
        """Physical MAC only (no SN, FQDN, hostname) returns 0.70."""
        row = _make_row(mac="AA:BB:CC:DD:EE:FF", ip="10.0.0.1")
        assert scorer._calculate_identifier_cap(row) == 0.70

    def test_ip_only_returns_0_40(self, scorer):
        """IP only (no other identifiers) returns 0.40."""
        row = _make_row(ip="10.0.0.1")
        assert scorer._calculate_identifier_cap(row) == 0.40

    def test_nothing_returns_0_30(self, scorer):
        """No identifiers at all returns 0.30."""
        row = _make_row(location="NYC")
        assert scorer._calculate_identifier_cap(row) == 0.30

    def test_sn_as_list(self, scorer):
        """SN in list form is still valid."""
        row = _make_row(sn=["SN123"], hostname="host1")
        assert scorer._calculate_identifier_cap(row) == 1.0

    def test_mac_as_list(self, scorer):
        """MAC in list form: first element checked for virtual prefix."""
        row = _make_row(hostname="host1", mac=["AA:BB:CC:DD:EE:FF"])
        assert scorer._calculate_identifier_cap(row) == 0.90

    def test_mac_as_list_virtual(self, scorer):
        """MAC list with virtual MAC first element."""
        row = _make_row(hostname="host1", mac=["00:50:56:AA:BB:CC"])
        # Virtual MAC, so has_physical_mac=False -> hostname only = 0.80
        assert scorer._calculate_identifier_cap(row) == 0.80

    def test_virtual_mac_only_returns_0_30(self, scorer):
        """Virtual MAC only (no hostname, no SN, no FQDN) returns 0.30.

        has_mac=True but has_physical_mac=False, no hostname/sn/fqdn.
        Falls to the 'else' branch because MAC-only branch requires physical MAC.
        IP is also not present, so it goes to minimal.
        """
        row = _make_row(mac="00:50:56:AA:BB:CC")
        result = scorer._calculate_identifier_cap(row)
        # has_mac=True, virtual -> has_physical_mac=False
        # no sn, no fqdn, no hostname, no physical mac
        # has_ip=False -> else branch = 0.30
        assert result == 0.30

    def test_nan_sn_not_counted(self, scorer):
        """NaN SN is not counted as valid."""
        row = _make_row(sn=np.nan, hostname="host1", mac="AA:BB:CC:DD:EE:FF")
        assert scorer._calculate_identifier_cap(row) == 0.90

    def test_empty_string_sn_not_counted(self, scorer):
        """Empty string SN is not counted as valid."""
        row = _make_row(sn="", hostname="host1")
        assert scorer._calculate_identifier_cap(row) == 0.80


# ---------------------------------------------------------------------------
# _is_virtual_mac
# ---------------------------------------------------------------------------

class TestIsVirtualMac:
    """Tests for _is_virtual_mac."""

    def test_vmware_prefix_005056(self, scorer):
        assert scorer._is_virtual_mac("00:50:56:AA:BB:CC") is True

    def test_vmware_prefix_000c29(self, scorer):
        assert scorer._is_virtual_mac("00:0C:29:AA:BB:CC") is True

    def test_vmware_prefix_000569(self, scorer):
        assert scorer._is_virtual_mac("00:05:69:AA:BB:CC") is True

    def test_vmware_prefix_001c14(self, scorer):
        assert scorer._is_virtual_mac("00:1C:14:AA:BB:CC") is True

    def test_virtualbox_prefix(self, scorer):
        assert scorer._is_virtual_mac("08:00:27:AA:BB:CC") is True

    def test_hyperv_prefix(self, scorer):
        assert scorer._is_virtual_mac("00:15:5D:AA:BB:CC") is True

    def test_physical_mac(self, scorer):
        assert scorer._is_virtual_mac("AA:BB:CC:DD:EE:FF") is False

    def test_none_returns_false(self, scorer):
        assert scorer._is_virtual_mac(None) is False

    def test_nan_returns_false(self, scorer):
        assert scorer._is_virtual_mac(np.nan) is False

    def test_dash_separator(self, scorer):
        """MAC with dash separators also detected."""
        assert scorer._is_virtual_mac("00-50-56-AA-BB-CC") is True

    def test_lowercase_mac(self, scorer):
        """Lowercase MAC also detected (uppercased internally)."""
        assert scorer._is_virtual_mac("00:50:56:aa:bb:cc") is True

    def test_kvm_qemu_mac_is_virtual(self, scorer):
        assert scorer._is_virtual_mac('52:54:00:12:34:56') is True

    def test_xen_mac_is_virtual(self, scorer):
        assert scorer._is_virtual_mac('00:16:3E:12:34:56') is True

    def test_docker_mac_is_virtual(self, scorer):
        assert scorer._is_virtual_mac('02:42:AC:11:00:02') is True

    def test_azure_mac_is_virtual(self, scorer):
        assert scorer._is_virtual_mac('00:0D:3A:12:34:56') is True

    def test_gcp_mac_is_virtual(self, scorer):
        assert scorer._is_virtual_mac('42:01:0A:12:34:56') is True

    def test_proxmox_mac_is_virtual(self, scorer):
        assert scorer._is_virtual_mac('BC:24:11:12:34:56') is True


# ---------------------------------------------------------------------------
# _has_data
# ---------------------------------------------------------------------------

class TestHasData:
    """Tests for _has_data utility method."""

    def test_field_present_with_value(self, scorer):
        row = _make_row(hostname="host1")
        assert scorer._has_data(row, "hostname") is True

    def test_field_missing(self, scorer):
        row = _make_row(hostname="host1")
        assert scorer._has_data(row, "sn") is False

    def test_field_nan(self, scorer):
        row = _make_row(sn=np.nan)
        assert scorer._has_data(row, "sn") is False

    def test_field_empty_string(self, scorer):
        row = _make_row(sn="")
        assert scorer._has_data(row, "sn") is False

    def test_field_whitespace_only(self, scorer):
        row = _make_row(sn="   ")
        assert scorer._has_data(row, "sn") is False

    def test_field_list_with_values(self, scorer):
        row = _make_row(sn=["SN001", "SN002"])
        assert scorer._has_data(row, "sn") is True

    def test_field_empty_list(self, scorer):
        row = _make_row(sn=[])
        assert scorer._has_data(row, "sn") is False

    def test_field_list_with_nan(self, scorer):
        row = _make_row(sn=[np.nan])
        assert scorer._has_data(row, "sn") is False

    def test_field_numpy_array(self, scorer):
        row = _make_row(sn=np.array(["SN001"]))
        assert scorer._has_data(row, "sn") is True

    def test_field_numpy_empty_array(self, scorer):
        row = _make_row(sn=np.array([]))
        assert scorer._has_data(row, "sn") is False


# ---------------------------------------------------------------------------
# _has_valid_value
# ---------------------------------------------------------------------------

class TestHasValidValue:
    """Tests for _has_valid_value utility method."""

    def test_valid_string(self, scorer):
        row = _make_row(hostname="host1")
        assert scorer._has_valid_value(row, "hostname") is True

    def test_missing_field(self, scorer):
        row = _make_row(hostname="host1")
        assert scorer._has_valid_value(row, "sn") is False

    def test_nan_value(self, scorer):
        row = _make_row(sn=np.nan)
        assert scorer._has_valid_value(row, "sn") is False

    def test_empty_string(self, scorer):
        row = _make_row(sn="")
        assert scorer._has_valid_value(row, "sn") is False

    def test_list_with_valid_value(self, scorer):
        row = _make_row(mac=["AA:BB:CC:DD:EE:FF"])
        assert scorer._has_valid_value(row, "mac") is True

    def test_empty_list(self, scorer):
        row = _make_row(mac=[])
        assert scorer._has_valid_value(row, "mac") is False

    def test_list_with_only_nan(self, scorer):
        row = _make_row(mac=[np.nan])
        assert scorer._has_valid_value(row, "mac") is False

    def test_numpy_array_with_value(self, scorer):
        row = _make_row(mac=np.array(["AA:BB:CC:DD:EE:FF"]))
        assert scorer._has_valid_value(row, "mac") is True


# ---------------------------------------------------------------------------
# calculate_confidence (integration)
# ---------------------------------------------------------------------------

class TestCalculateConfidenceIntegration:
    """Integration tests for the full calculate_confidence pipeline."""

    def _make_full_asset(self, **overrides):
        """Create a fully populated asset row dict."""
        base = {
            "id": "asset-001",
            "sn": ["SN123"],
            "hostname": ["server01"],
            "fqdn": ["server01.example.com"],
            "mac": ["AA:BB:CC:DD:EE:FF"],
            "ip": ["10.0.0.1"],
            "os_normalized": ["Windows 10"],
            "manufacturer": ["Dell"],
            "model": ["OptiPlex 7090"],
            "location": ["NYC"],
            "owner": ["jdoe"],
            "category": ["Laptop"],
            "os_eol_date": ["2025-10-14"],
            "vendor_category": ["Enterprise"],
            "data_source": ["intune", "azure"],
            "data_source_category": ["mobile device management", "cloud platform"],
            "last_seen": datetime.now(timezone.utc) - timedelta(hours=2),
        }
        base.update(overrides)
        return base

    def test_full_calculation_all_gates_enabled(self, scorer):
        """Full pipeline with all gates active."""
        asset_data = self._make_full_asset()
        assets_df = pd.DataFrame([asset_data])
        providers_df = pd.DataFrame([
            {"id": "asset-001", "sn": "SN123", "hostname": "server01"},
        ])

        result = scorer.calculate_confidence(assets_df, providers_df)

        assert "confidence" in result.columns
        assert "confidence_base" in result.columns
        assert "confidence_consistency_penalty" in result.columns
        assert "confidence_recency_penalty" in result.columns
        assert "confidence_identifier_cap" in result.columns

        conf = result["confidence"].iloc[0]
        assert 0.0 < conf <= 1.0

    def test_full_calculation_all_gates_disabled(self, scorer_no_gates):
        """Full pipeline with all gates disabled gives base score only."""
        asset_data = {
            "id": "asset-001",
            "sn": ["SN123"],
            "hostname": ["server01"],
            "data_source": ["intune"],
            "data_source_category": ["mobile device management"],
        }
        assets_df = pd.DataFrame([asset_data])
        providers_df = pd.DataFrame([{"id": "asset-001", "sn": "SN123"}])

        result = scorer_no_gates.calculate_confidence(assets_df, providers_df)

        assert result["confidence_consistency_penalty"].iloc[0] == 1.0
        assert result["confidence_recency_penalty"].iloc[0] == 1.0
        assert result["confidence_identifier_cap"].iloc[0] == 1.0
        # raw_confidence == base_score when all gates are 1.0
        breakdown = result["confidence_breakdown"].iloc[0]
        assert breakdown["raw_confidence"] == pytest.approx(
            result["confidence_base"].iloc[0], abs=0.001
        )

    def test_high_confidence_asset(self, scorer):
        """Well-identified, multi-source, recent asset gets high confidence."""
        asset_data = self._make_full_asset()
        assets_df = pd.DataFrame([asset_data])
        providers_df = pd.DataFrame([
            {"id": "asset-001", "sn": "SN123", "hostname": "server01"},
        ])

        result = scorer.calculate_confidence(assets_df, providers_df)
        conf = result["confidence"].iloc[0]
        # Should be high: good sources, good identifiers, fresh, no conflicts
        assert conf > 0.6

    def test_low_confidence_asset(self, scorer):
        """Poorly identified, single-source, stale asset gets low confidence."""
        asset_data = {
            "id": "asset-bad",
            "ip": ["10.0.0.99"],
            "data_source": ["unknown_tool"],
            "data_source_category": ["other"],
            "last_seen": datetime.now(timezone.utc) - timedelta(days=120),
        }
        assets_df = pd.DataFrame([asset_data])
        providers_df = pd.DataFrame([{"id": "asset-bad", "ip": "10.0.0.99"}])

        result = scorer.calculate_confidence(assets_df, providers_df)
        conf = result["confidence"].iloc[0]
        assert conf < 0.3

    def test_multiple_assets(self, scorer_no_gates):
        """Multiple assets each get their own confidence score."""
        assets_df = pd.DataFrame([
            {
                "id": "a1",
                "sn": ["SN001"],
                "hostname": ["host1"],
                "data_source": ["src1"],
                "data_source_category": ["endpoint security"],
            },
            {
                "id": "a2",
                "ip": ["10.0.0.1"],
                "data_source": ["src2"],
                "data_source_category": ["other"],
            },
        ])
        providers_df = pd.DataFrame([
            {"id": "a1", "sn": "SN001"},
            {"id": "a2", "ip": "10.0.0.1"},
        ])

        result = scorer_no_gates.calculate_confidence(assets_df, providers_df)
        assert len(result) == 2
        # First asset should have higher confidence than second
        assert result["confidence"].iloc[0] > result["confidence"].iloc[1]

    def test_missing_last_seen_column_gets_added(self, scorer):
        """If last_seen column is missing, it gets added as NaT (unknown freshness)."""
        assets_df = pd.DataFrame([{
            "id": "a1",
            "sn": ["SN001"],
            "data_source": ["src1"],
            "data_source_category": ["endpoint security"],
        }])
        providers_df = pd.DataFrame([{"id": "a1", "sn": "SN001"}])

        result = scorer.calculate_confidence(assets_df, providers_df)
        assert "last_seen" in result.columns
        # Recency penalty should be 0.95 (unknown freshness) since last_seen is NaT
        assert result["confidence_recency_penalty"].iloc[0] == pytest.approx(0.95, abs=0.01)

    def test_asset_id_as_list(self, scorer):
        """Asset with id as a list of component IDs."""
        assets_df = pd.DataFrame([{
            "id": ["comp1", "comp2"],
            "sn": ["SN001"],
            "hostname": ["host1"],
            "data_source": ["src1", "src2"],
            "data_source_category": ["endpoint security", "cloud platform"],
            "last_seen": datetime.now(timezone.utc),
        }])
        providers_df = pd.DataFrame([
            {"id": "comp1", "sn": "SN001", "hostname": "host1"},
            {"id": "comp2", "sn": "SN001", "hostname": "host1"},
        ])

        result = scorer.calculate_confidence(assets_df, providers_df)
        assert "confidence" in result.columns
        assert result["confidence"].iloc[0] > 0.0

    def test_asset_id_as_scalar(self, scorer):
        """Asset with scalar (non-list) id."""
        assets_df = pd.DataFrame([{
            "id": "single-id",
            "sn": ["SN001"],
            "data_source": ["src1"],
            "data_source_category": ["endpoint security"],
            "last_seen": datetime.now(timezone.utc),
        }])
        providers_df = pd.DataFrame([
            {"id": "single-id", "sn": "SN001"},
        ])

        result = scorer.calculate_confidence(assets_df, providers_df)
        assert result["confidence_consistency_penalty"].iloc[0] == 1.0


# ---------------------------------------------------------------------------
# get_scoring_stats
# ---------------------------------------------------------------------------

class TestGetScoringStats:
    """Tests for get_scoring_stats."""

    def test_structure(self, scorer):
        stats = scorer.get_scoring_stats()
        assert stats["version"] == 2
        assert "source_reliability_scores" in stats
        assert "field_tiers" in stats
        assert "consistency_severity" in stats
        assert "weights" in stats
        assert "gates_enabled" in stats

    def test_weights_sum_to_one(self, scorer):
        stats = scorer.get_scoring_stats()
        weights = stats["weights"]
        for weight_set in ("standard", "single_source"):
            w = weights[weight_set]
            total = w["source_reliability"] + w["source_count"] + w["data_completeness"]
            assert total == pytest.approx(1.0), f"{weight_set} weights don't sum to 1.0"

    def test_gates_reflect_constructor(self):
        scorer = AssetScorer(
            enable_recency_penalty=False,
            enable_identifier_cap=True,
            enable_consistency_penalty=False,
        )
        stats = scorer.get_scoring_stats()
        assert stats["gates_enabled"]["recency_penalty"] is False
        assert stats["gates_enabled"]["identifier_cap"] is True
        assert stats["gates_enabled"]["consistency_penalty"] is False

    def test_custom_scores_reflected(self):
        custom = {"my_source": 0.77}
        scorer = AssetScorer(source_reliability_scores=custom)
        stats = scorer.get_scoring_stats()
        assert stats["source_reliability_scores"]["my_source"] == 0.77


# ---------------------------------------------------------------------------
# _calculate_identifier_cap — updated values (#53+#54)
# ---------------------------------------------------------------------------

class TestIdentifierCapUpdates:
    """Test updated identifier cap values (#53+#54)."""

    def test_fqdn_plus_physical_mac_cap_is_095(self, scorer):
        """NEW: FQDN + physical MAC should cap at 0.95."""
        row = _make_row(fqdn="host1.company.com", mac="AA:BB:CC:DD:EE:FF")
        result = scorer._calculate_identifier_cap(row)
        assert result == 0.95

    def test_fqdn_only_cap_remains_090(self, scorer):
        """FQDN alone (no physical MAC) stays at 0.90."""
        row = _make_row(fqdn="host1.company.com")
        result = scorer._calculate_identifier_cap(row)
        assert result == 0.90

    def test_fqdn_plus_virtual_mac_cap_is_090(self, scorer):
        """FQDN + virtual MAC should still be 0.90 (virtual MAC ignored)."""
        row = _make_row(fqdn="host1.company.com", mac="00:50:56:AA:BB:CC")
        result = scorer._calculate_identifier_cap(row)
        assert result == 0.90

    def test_hostname_plus_physical_mac_cap_is_090(self, scorer):
        """RAISED: was 0.85, now 0.90."""
        row = _make_row(hostname="host1", mac="AA:BB:CC:DD:EE:FF")
        result = scorer._calculate_identifier_cap(row)
        assert result == 0.90

    def test_hostname_only_cap_is_080(self, scorer):
        """RAISED: was 0.75, now 0.80."""
        row = _make_row(hostname="host1")
        result = scorer._calculate_identifier_cap(row)
        assert result == 0.80

    def test_physical_mac_only_cap_is_070(self, scorer):
        """RAISED: was 0.65, now 0.70."""
        row = _make_row(mac="AA:BB:CC:DD:EE:FF")
        result = scorer._calculate_identifier_cap(row)
        assert result == 0.70

    def test_sn_cap_unchanged_at_100(self, scorer):
        """Serial number cap unchanged at 1.0."""
        row = _make_row(sn="ABC123456")
        result = scorer._calculate_identifier_cap(row)
        assert result == 1.0

    def test_ip_only_cap_unchanged_at_040(self, scorer):
        """IP-only cap unchanged at 0.40."""
        row = _make_row(ip="10.0.0.1")
        result = scorer._calculate_identifier_cap(row)
        assert result == 0.40


# ---------------------------------------------------------------------------
# Field value quality filter (#84)
# ---------------------------------------------------------------------------

class TestFieldValueQuality:
    """Tests that low-quality field values don't count as 'has data' (#84)."""

    def test_localhost_hostname_not_counted(self, scorer):
        row = pd.Series({'hostname': 'localhost'})
        assert scorer._has_data(row, 'hostname') is False

    def test_unknown_hostname_not_counted(self, scorer):
        row = pd.Series({'hostname': 'unknown'})
        assert scorer._has_data(row, 'hostname') is False

    def test_pc_hostname_not_counted(self, scorer):
        row = pd.Series({'hostname': 'PC'})
        assert scorer._has_data(row, 'hostname') is False

    def test_zero_ip_not_counted(self, scorer):
        row = pd.Series({'ip': '0.0.0.0'})
        assert scorer._has_data(row, 'ip') is False

    def test_loopback_ip_not_counted(self, scorer):
        row = pd.Series({'ip': '127.0.0.1'})
        assert scorer._has_data(row, 'ip') is False

    def test_broadcast_mac_not_counted(self, scorer):
        row = pd.Series({'mac': 'FF:FF:FF:FF:FF:FF'})
        assert scorer._has_data(row, 'mac') is False

    def test_zero_mac_not_counted(self, scorer):
        row = pd.Series({'mac': '00:00:00:00:00:00'})
        assert scorer._has_data(row, 'mac') is False

    def test_unknown_sn_not_counted(self, scorer):
        row = pd.Series({'sn': 'Unknown'})
        assert scorer._has_data(row, 'sn') is False

    def test_oem_placeholder_sn_not_counted(self, scorer):
        row = pd.Series({'sn': 'To Be Filled By O.E.M.'})
        assert scorer._has_data(row, 'sn') is False

    def test_valid_hostname_still_counted(self, scorer):
        row = pd.Series({'hostname': 'server-web-01'})
        assert scorer._has_data(row, 'hostname') is True

    def test_valid_ip_still_counted(self, scorer):
        row = pd.Series({'ip': '10.0.1.50'})
        assert scorer._has_data(row, 'ip') is True

    def test_valid_mac_still_counted(self, scorer):
        row = pd.Series({'mac': 'AA:BB:CC:DD:EE:FF'})
        assert scorer._has_data(row, 'mac') is True

    def test_valid_sn_still_counted(self, scorer):
        row = pd.Series({'sn': 'FXRT823'})
        assert scorer._has_data(row, 'sn') is True

    def test_unlisted_field_not_affected(self, scorer):
        """Fields not in LOW_QUALITY_VALUES should not be filtered."""
        row = pd.Series({'os_normalized': 'unknown'})
        assert scorer._has_data(row, 'os_normalized') is True


# ---------------------------------------------------------------------------
# Agreement strength — graduated consistency penalty (#85)
# ---------------------------------------------------------------------------

class TestAgreementStrength:
    """Tests for graduated consistency penalty based on agreement ratio (#85)."""

    def test_two_of_three_agree_less_penalty_than_all_disagree(self, scorer):
        """2/3 agreeing should result in less penalty than 3 completely different values."""
        # 2 of 3 agree on hostname
        providers_2of3 = pd.DataFrame({
            'id': ['c1', 'c2', 'c3'],
            'hostname': ['server01', 'server01', 'server02']
        })
        penalty_2of3 = scorer._calculate_consistency_penalty(providers_2of3, ['c1', 'c2', 'c3'])

        # All 3 disagree on hostname
        providers_all_diff = pd.DataFrame({
            'id': ['c1', 'c2', 'c3'],
            'hostname': ['server01', 'server02', 'server03']
        })
        penalty_all_diff = scorer._calculate_consistency_penalty(providers_all_diff, ['c1', 'c2', 'c3'])

        # 2-of-3 agreement should produce LESS penalty (higher multiplier)
        assert penalty_2of3 > penalty_all_diff

    def test_four_of_five_agree_minimal_penalty(self, scorer):
        """4/5 agreeing (80%) should get minimal penalty."""
        providers = pd.DataFrame({
            'id': ['c1', 'c2', 'c3', 'c4', 'c5'],
            'hostname': ['server01', 'server01', 'server01', 'server01', 'server02']
        })
        penalty = scorer._calculate_consistency_penalty(providers, ['c1', 'c2', 'c3', 'c4', 'c5'])
        # Should be close to 1.0 (very mild penalty)
        assert penalty > 0.85

    def test_full_agreement_no_penalty(self, scorer):
        """All sources agree -> multiplier = 1.0."""
        providers = pd.DataFrame({
            'id': ['c1', 'c2', 'c3'],
            'hostname': ['server01', 'server01', 'server01']
        })
        penalty = scorer._calculate_consistency_penalty(providers, ['c1', 'c2', 'c3'])
        assert penalty == 1.0

    def test_single_source_no_penalty(self, scorer):
        """Single source -> 1.0."""
        providers = pd.DataFrame({
            'id': ['c1'],
            'hostname': ['server01']
        })
        penalty = scorer._calculate_consistency_penalty(providers, ['c1'])
        assert penalty == 1.0


# ---------------------------------------------------------------------------
# Weighted consistency by source reliability (#58)
# ---------------------------------------------------------------------------

class TestWeightedConsistencyByReliability:
    """Tests that high-reliability source agreements reduce penalty (#58)."""

    def test_high_reliability_majority_less_penalty(self, scorer):
        """High-reliability sources agreeing should produce less penalty than low-reliability."""
        # High-reliability majority (MDM + cloud agree, other disagrees)
        providers_high = pd.DataFrame({
            'id': ['c1', 'c2', 'c3'],
            'hostname': ['server01', 'server01', 'server02'],
            'data_source_category': ['mobile device management', 'cloud platform', 'other']
        })
        penalty_high = scorer._calculate_consistency_penalty(providers_high, ['c1', 'c2', 'c3'])

        # Low-reliability majority (two 'other' agree, MDM disagrees)
        providers_low = pd.DataFrame({
            'id': ['c1', 'c2', 'c3'],
            'hostname': ['server02', 'server01', 'server01'],
            'data_source_category': ['mobile device management', 'other', 'other']
        })
        penalty_low = scorer._calculate_consistency_penalty(providers_low, ['c1', 'c2', 'c3'])

        # High-reliability majority should produce LESS penalty (higher multiplier)
        assert penalty_high > penalty_low

    def test_no_data_source_category_column_still_works(self, scorer):
        """Without data_source_category column, should still work (no reliability bonus)."""
        providers = pd.DataFrame({
            'id': ['c1', 'c2', 'c3'],
            'hostname': ['server01', 'server01', 'server02']
        })
        penalty = scorer._calculate_consistency_penalty(providers, ['c1', 'c2', 'c3'])
        assert 0.0 < penalty < 1.0  # Some penalty, no crash


# ---------------------------------------------------------------------------
# Confidence calibration curve (#50)
# ---------------------------------------------------------------------------

class TestCalibrationCurve:
    """Tests for confidence calibration support (#50)."""

    def test_no_calibration_returns_raw(self):
        """Without calibration curve, raw confidence is returned."""
        scorer = AssetScorer(enable_calibration=False)
        assert scorer._apply_calibration(0.75) == 0.75

    def test_calibration_exact_bucket(self):
        """Calibration curve maps exact bucket values."""
        scorer = AssetScorer(calibration_curve={0.5: 0.6, 0.8: 0.7})
        assert scorer._apply_calibration(0.5) == 0.6
        assert scorer._apply_calibration(0.8) == 0.7

    def test_calibration_interpolation(self):
        """Calibration curve interpolates between buckets."""
        scorer = AssetScorer(calibration_curve={0.4: 0.5, 0.6: 0.7})
        result = scorer._apply_calibration(0.5)
        assert 0.5 < result < 0.7  # Should be interpolated

    def test_calibration_empty_curve(self):
        """Empty calibration curve returns raw confidence."""
        scorer = AssetScorer(calibration_curve={}, enable_calibration=False)
        assert scorer._apply_calibration(0.75) == 0.75

    def test_calibration_clamp_below_range(self):
        """Value below all buckets returns lowest bucket value."""
        scorer = AssetScorer(calibration_curve={0.3: 0.4, 0.7: 0.8})
        assert scorer._apply_calibration(0.1) == 0.4

    def test_calibration_clamp_above_range(self):
        """Value above all buckets returns highest bucket value."""
        scorer = AssetScorer(calibration_curve={0.3: 0.4, 0.7: 0.8})
        assert scorer._apply_calibration(0.9) == 0.8

    def test_calibration_none_curve(self):
        """Disabled calibration returns raw confidence."""
        scorer = AssetScorer(enable_calibration=False)
        assert scorer._apply_calibration(0.65) == 0.65


# ---------------------------------------------------------------------------
# Source reliability bonus cap fix (#56)
# ---------------------------------------------------------------------------

class TestSourceReliabilityBonusCap:
    """Auth + diversity bonuses should both apply additively (#56)."""

    def test_diversity_bonus_applies_after_auth(self, scorer):
        """When auth bonus would push to 1.0 under old logic, diversity should still add."""
        # 2 authoritative sources AND 3 unique categories
        row = _make_row(
            data_source=["intune", "azure", "defender"],
            data_source_category=[
                "mobile device management",
                "cloud platform",
                "endpoint security",
            ],
        )
        result = scorer._calculate_source_reliability(row)
        # Base ~0.9067 + 0.10 auth + 0.05 diversity = 1.0567, capped to 1.0
        assert result >= 0.95  # Both bonuses had effect

    def test_bonuses_additive_not_individually_capped(self, scorer):
        """Bonuses should accumulate before a single cap at the end."""
        # Use lower-reliability authoritative sources so base doesn't reach 1.0 alone
        custom_scores = {
            'mobile device management': 0.80,
            'cloud platform': 0.78,
            'endpoint security': 0.70,
        }
        custom_scorer = AssetScorer(source_reliability_scores=custom_scores)
        row = _make_row(
            data_source=["intune", "azure", "defender"],
            data_source_category=[
                "mobile device management",
                "cloud platform",
                "endpoint security",
            ],
        )
        # available_cats=3 so diversity threshold=max(2,3-1)=2, available_auth=3
        result = custom_scorer._calculate_source_reliability(row, available_cats=3, available_auth=3)
        # Base = mean([0.80, 0.78, 0.70]) = 0.76
        # + 0.10 auth + 0.05 diversity = 0.91
        expected = np.mean([0.80, 0.78, 0.70]) + 0.10 + 0.05
        assert result == pytest.approx(min(1.0, expected), abs=0.01)


# ---------------------------------------------------------------------------
# Conflicted fields reduce completeness (#57)
# ---------------------------------------------------------------------------

class TestConflictedFieldCompleteness:
    """Conflicted fields reduce completeness slightly (#57)."""

    def test_conflicts_reduce_completeness(self, scorer):
        """Assets with field conflicts should get lower base score than identical asset without."""
        # Asset with conflicting hostnames
        asset_conflict = {
            "id": ["c1", "c2"],
            "sn": ["SN001"],
            "hostname": ["host1"],
            "fqdn": ["host1.example.com"],
            "mac": ["AA:BB:CC:DD:EE:FF"],
            "ip": ["10.0.0.1"],
            "os_normalized": ["Windows 10"],
            "manufacturer": ["Dell"],
            "model": ["OptiPlex"],
            "location": ["NYC"],
            "owner": ["jdoe"],
            "category": ["Laptop"],
            "os_eol_date": ["2025-10-14"],
            "vendor_category": ["Enterprise"],
            "data_source": ["src1", "src2"],
            "data_source_category": ["endpoint security", "cloud platform"],
            "last_seen": datetime.now(timezone.utc),
        }
        # Providers disagree on hostname
        providers_conflict = pd.DataFrame([
            {"id": "c1", "hostname": "host1"},
            {"id": "c2", "hostname": "host2"},
        ])

        # Same asset without conflict
        asset_no_conflict = dict(asset_conflict)
        providers_no_conflict = pd.DataFrame([
            {"id": "c1", "hostname": "host1"},
            {"id": "c2", "hostname": "host1"},
        ])

        df_conflict = pd.DataFrame([asset_conflict])
        df_no_conflict = pd.DataFrame([asset_no_conflict])

        result_conflict = scorer.calculate_confidence(df_conflict, providers_conflict)
        result_no_conflict = scorer.calculate_confidence(df_no_conflict, providers_no_conflict)

        # The conflicted asset should have lower confidence
        assert result_conflict["confidence"].iloc[0] < result_no_conflict["confidence"].iloc[0]
        # But only slightly lower base score (the completeness reduction is gentle)
        assert result_conflict["confidence_base"].iloc[0] < result_no_conflict["confidence_base"].iloc[0]

    def test_no_conflict_no_reduction(self, scorer):
        """Without conflicts (penalty=1.0), completeness is unaffected."""
        asset = {
            "id": ["c1", "c2"],
            "sn": ["SN001"],
            "hostname": ["host1"],
            "data_source": ["src1", "src2"],
            "data_source_category": ["endpoint security", "cloud platform"],
            "last_seen": datetime.now(timezone.utc),
        }
        providers = pd.DataFrame([
            {"id": "c1", "sn": "SN001", "hostname": "host1"},
            {"id": "c2", "sn": "SN001", "hostname": "host1"},
        ])

        df = pd.DataFrame([asset])
        result = scorer.calculate_confidence(df, providers)
        # consistency_penalty should be 1.0 (no conflicts)
        assert result["confidence_consistency_penalty"].iloc[0] == 1.0

    def test_severe_conflicts_larger_reduction(self, scorer):
        """Severe conflicts (low penalty) cause larger completeness reduction."""
        # SN disagreement causes severe penalty (0.30)
        asset = {
            "id": ["c1", "c2"],
            "sn": ["SN001"],
            "hostname": ["host1"],
            "fqdn": ["host1.example.com"],
            "mac": ["AA:BB:CC:DD:EE:FF"],
            "data_source": ["src1", "src2"],
            "data_source_category": ["endpoint security", "cloud platform"],
            "last_seen": datetime.now(timezone.utc),
        }
        providers_sn_conflict = pd.DataFrame([
            {"id": "c1", "sn": "SN001", "hostname": "host1"},
            {"id": "c2", "sn": "SN999", "hostname": "host1"},
        ])
        # Hostname disagreement only (less severe)
        providers_hostname_conflict = pd.DataFrame([
            {"id": "c1", "sn": "SN001", "hostname": "host1"},
            {"id": "c2", "sn": "SN001", "hostname": "host2"},
        ])

        df_sn = pd.DataFrame([dict(asset)])
        df_host = pd.DataFrame([dict(asset)])

        result_sn = scorer.calculate_confidence(df_sn, providers_sn_conflict)
        result_host = scorer.calculate_confidence(df_host, providers_hostname_conflict)

        # SN conflict is more severe -> lower base score
        assert result_sn["confidence_base"].iloc[0] < result_host["confidence_base"].iloc[0]


# ---------------------------------------------------------------------------
# Single-source weight rebalancing (#286)
# ---------------------------------------------------------------------------

class TestSingleSourceWeightRebalancing:
    """Single-source assets use 0.40/0.15/0.45 weights instead of 0.40/0.30/0.30."""

    def test_single_source_uses_rebalanced_weights(self, scorer_no_gates):
        """Single-source asset should use completeness-heavy weights."""
        asset_data = {
            "id": "asset-ss-1",
            "sn": ["SN001"],
            "hostname": ["host1"],
            "fqdn": ["host1.example.com"],
            "mac": ["AA:BB:CC:DD:EE:FF"],
            "ip": ["10.0.0.1"],
            "os_normalized": ["Windows 11"],
            "manufacturer": ["Dell"],
            "model": ["OptiPlex 7090"],
            "location": [None],
            "owner": [None],
            "category": ["Desktop"],
            "os_eol_date": [None],
            "vendor_category": [None],
            "data_source": ["intune"],
            "data_source_category": ["mobile device management"],
            "last_seen": datetime.now(timezone.utc),
        }
        assets_df = pd.DataFrame([asset_data])
        providers_df = pd.DataFrame([{"id": "asset-ss-1", "sn": "SN001"}])
        result = scorer_no_gates.calculate_confidence(assets_df, providers_df)
        breakdown = result['confidence_breakdown'].iloc[0]
        assert breakdown['weights_used'] == 'single_source'

    def test_multi_source_uses_standard_weights(self, scorer_no_gates):
        """Multi-source asset should use standard 0.40/0.30/0.30 weights."""
        asset_data = {
            "id": "asset-ms-1",
            "sn": ["SN001"],
            "hostname": ["host1"],
            "fqdn": ["host1.example.com"],
            "mac": ["AA:BB:CC:DD:EE:FF"],
            "ip": ["10.0.0.1"],
            "os_normalized": ["Windows 11"],
            "manufacturer": ["Dell"],
            "model": ["OptiPlex 7090"],
            "location": [None],
            "owner": [None],
            "category": ["Desktop"],
            "os_eol_date": [None],
            "vendor_category": [None],
            "data_source": [["intune", "defender"]],
            "data_source_category": [["mobile device management", "endpoint security"]],
            "last_seen": datetime.now(timezone.utc),
        }
        assets_df = pd.DataFrame(asset_data)
        providers_df = pd.DataFrame([
            {"id": "asset-ms-1", "sn": "SN001", "hostname": "host1"},
        ])
        result = scorer_no_gates.calculate_confidence(assets_df, providers_df)
        breakdown = result['confidence_breakdown'].iloc[0]
        assert breakdown['weights_used'] == 'standard'

    def test_single_source_high_completeness_beats_low_completeness(self, scorer_no_gates):
        """Single-source asset with rich data should score higher than sparse one."""
        rich_asset = {
            "id": "asset-rich",
            "sn": ["SN001"],
            "hostname": ["host1"],
            "fqdn": ["host1.example.com"],
            "mac": ["AA:BB:CC:DD:EE:FF"],
            "ip": ["10.0.0.1"],
            "os_normalized": ["Windows 11"],
            "manufacturer": ["Dell"],
            "model": ["OptiPlex 7090"],
            "location": [None],
            "owner": [None],
            "category": ["Desktop"],
            "os_eol_date": [None],
            "vendor_category": [None],
            "data_source": ["intune"],
            "data_source_category": ["mobile device management"],
            "last_seen": datetime.now(timezone.utc),
        }
        sparse_asset = {
            "id": "asset-sparse",
            "sn": [None],
            "hostname": ["host2"],
            "fqdn": [None],
            "mac": [None],
            "ip": [None],
            "os_normalized": ["Windows"],
            "manufacturer": [None],
            "model": [None],
            "location": [None],
            "owner": [None],
            "category": ["Desktop"],
            "os_eol_date": [None],
            "vendor_category": [None],
            "data_source": ["intune"],
            "data_source_category": ["mobile device management"],
            "last_seen": datetime.now(timezone.utc),
        }
        rich_df = pd.DataFrame([rich_asset])
        sparse_df = pd.DataFrame([sparse_asset])
        providers_empty = pd.DataFrame()

        rich_result = scorer_no_gates.calculate_confidence(rich_df, providers_empty)
        sparse_result = scorer_no_gates.calculate_confidence(sparse_df, providers_empty)

        rich_score = rich_result['confidence'].iloc[0]
        sparse_score = sparse_result['confidence'].iloc[0]

        # Rich should score meaningfully higher (not just marginally)
        assert rich_score > sparse_score + 0.10


# ---------------------------------------------------------------------------
# Future last_seen dates (#76)
# ---------------------------------------------------------------------------

class TestFutureLastSeen:
    """Future last_seen dates should not inflate scores (#76)."""

    def test_future_date_not_above_one(self, scorer):
        """Recency penalty for future date should not exceed 1.0."""
        future = datetime.now(timezone.utc) + timedelta(days=30)
        result = scorer._calculate_recency_penalty(future)
        assert result <= 1.0

    def test_future_date_treated_as_recent(self, scorer):
        """Future dates should be treated as 'just seen' (penalty = 1.0 or close)."""
        future = datetime.now(timezone.utc) + timedelta(days=5)
        result = scorer._calculate_recency_penalty(future)
        assert result >= 0.95  # Should be treated as very recent

    def test_far_future_date_still_capped(self, scorer):
        """Even far-future dates should not produce penalty > 1.0."""
        future = datetime.now(timezone.utc) + timedelta(days=365)
        result = scorer._calculate_recency_penalty(future)
        assert result <= 1.0
        assert result >= 0.95


# ---------------------------------------------------------------------------
# All-empty-string fields (#77)
# ---------------------------------------------------------------------------

class TestAllEmptyStrings:
    """All-empty-string fields should get zero completeness (#77)."""

    def test_empty_strings_zero_completeness(self, scorer):
        """Empty strings for all fields should result in 0.0 completeness."""
        row = pd.Series({
            'sn': '', 'hostname': '', 'fqdn': '', 'mac': '',
            'ip': '', 'os_normalized': '', 'manufacturer': '', 'model': '',
            'location': '', 'owner': '', 'category': '', 'os_eol_date': '',
            'vendor_category': '',
        })
        result = scorer._calculate_data_completeness(row)
        assert result == 0.0

    def test_whitespace_only_zero_completeness(self, scorer):
        """Whitespace-only strings should also result in 0.0 completeness."""
        row = pd.Series({
            'sn': '  ', 'hostname': '  ', 'fqdn': '  ', 'mac': '  ',
            'ip': '  ', 'os_normalized': '  ', 'manufacturer': '  ', 'model': '  ',
            'location': '  ', 'owner': '  ', 'category': '  ', 'os_eol_date': '  ',
            'vendor_category': '  ',
        })
        result = scorer._calculate_data_completeness(row)
        assert result == 0.0

    def test_mix_of_empty_and_whitespace(self, scorer):
        """Mix of empty strings and whitespace-only should still be 0.0."""
        row = pd.Series({
            'sn': '', 'hostname': '   ', 'fqdn': '', 'mac': ' ',
            'ip': '  ', 'os_normalized': '', 'manufacturer': '  ', 'model': '',
            'location': ' ', 'owner': '', 'category': '   ', 'os_eol_date': '',
            'vendor_category': '',
        })
        result = scorer._calculate_data_completeness(row)
        assert result == 0.0


# ---------------------------------------------------------------------------
# Empty providers DataFrame (#78)
# ---------------------------------------------------------------------------

class TestEmptyProviders:
    """Empty providers should result in no consistency penalty (#78)."""

    def test_empty_providers_no_penalty(self, scorer):
        """Empty providers DataFrame should return 1.0 (no penalty)."""
        providers_df = pd.DataFrame(columns=['id', 'sn', 'hostname'])
        result = scorer._calculate_consistency_penalty(providers_df, ['nonexistent_id'])
        assert result == 1.0

    def test_single_provider_no_conflict(self, scorer):
        """Single provider can't conflict with itself."""
        providers_df = pd.DataFrame({
            'id': ['asset1'],
            'sn': ['ABC123'],
            'hostname': ['server1'],
        })
        result = scorer._calculate_consistency_penalty(providers_df, ['asset1'])
        assert result == 1.0

    def test_no_matching_ids_in_providers(self, scorer):
        """No matching asset IDs in providers DataFrame returns 1.0."""
        providers_df = pd.DataFrame({
            'id': ['other1', 'other2'],
            'sn': ['SN001', 'SN002'],
            'hostname': ['host1', 'host2'],
        })
        result = scorer._calculate_consistency_penalty(providers_df, ['nonexistent'])
        assert result == 1.0


# ---------------------------------------------------------------------------
# confidence_breakdown dict (Task 2)
# ---------------------------------------------------------------------------

class TestConfidenceBreakdown:
    """Tests for confidence_breakdown dict built during calculate_confidence."""

    def _make_full_asset(self, **overrides):
        """Create a fully populated asset row dict."""
        base = {
            "id": "asset-001",
            "sn": ["SN123"],
            "hostname": ["server01"],
            "fqdn": ["server01.example.com"],
            "mac": ["AA:BB:CC:DD:EE:FF"],
            "ip": ["10.0.0.1"],
            "os_normalized": ["Windows 10"],
            "manufacturer": ["Dell"],
            "model": ["OptiPlex 7090"],
            "location": ["NYC"],
            "owner": ["jdoe"],
            "category": ["Laptop"],
            "os_eol_date": ["2025-10-14"],
            "vendor_category": ["Enterprise"],
            "data_source": ["intune", "azure"],
            "data_source_category": ["mobile device management", "cloud platform"],
            "last_seen": datetime.now(timezone.utc) - timedelta(hours=2),
        }
        base.update(overrides)
        return base

    def test_breakdown_columns_present(self, scorer):
        """Verify confidence_breakdown column exists and has all expected keys."""
        asset_data = self._make_full_asset()
        assets_df = pd.DataFrame([asset_data])
        providers_df = pd.DataFrame([
            {"id": "asset-001", "sn": "SN123", "hostname": "server01"},
        ])

        result = scorer.calculate_confidence(assets_df, providers_df)

        assert "confidence_breakdown" in result.columns
        breakdown = result["confidence_breakdown"].iloc[0]
        assert isinstance(breakdown, dict)

        expected_keys = {
            'version', 'raw_confidence', 'calibrated',
            'base_score', 'source_reliability', 'source_count_score',
            'data_completeness', 'consistency_penalty', 'recency_penalty',
            'identifier_cap', 'source_count', 'weights_used', 'available_sources',
            'identifier_type', 'missing_fields', 'conflicting_fields',
            'days_since_seen',
        }
        assert set(breakdown.keys()) == expected_keys
        assert breakdown['version'] == 2
        assert isinstance(breakdown['raw_confidence'], float)
        assert isinstance(breakdown['calibrated'], bool)
        # Numeric sub-scores should be floats rounded to 4 decimals
        for key in ['base_score', 'source_reliability', 'source_count_score',
                     'data_completeness', 'consistency_penalty', 'recency_penalty',
                     'identifier_cap']:
            assert isinstance(breakdown[key], float)

    def test_breakdown_identifier_type_serial_number(self, scorer):
        """SN present should yield identifier_type 'serial_number'."""
        asset_data = self._make_full_asset()
        assets_df = pd.DataFrame([asset_data])
        providers_df = pd.DataFrame([
            {"id": "asset-001", "sn": "SN123", "hostname": "server01"},
        ])

        result = scorer.calculate_confidence(assets_df, providers_df)
        breakdown = result["confidence_breakdown"].iloc[0]
        assert breakdown['identifier_type'] == 'serial_number'

    def test_breakdown_identifier_type_fqdn_and_mac(self, scorer):
        """FQDN + physical MAC without SN should yield 'fqdn_and_mac'."""
        asset_data = self._make_full_asset(sn=np.nan)
        assets_df = pd.DataFrame([asset_data])
        providers_df = pd.DataFrame([
            {"id": "asset-001", "hostname": "server01"},
        ])

        result = scorer.calculate_confidence(assets_df, providers_df)
        breakdown = result["confidence_breakdown"].iloc[0]
        assert breakdown['identifier_type'] == 'fqdn_and_mac'

    def test_breakdown_identifier_type_hostname(self, scorer):
        """Hostname only (no SN, no FQDN, no physical MAC) should yield 'hostname'."""
        asset_data = self._make_full_asset(
            sn=np.nan, fqdn=np.nan, mac=np.nan,
        )
        assets_df = pd.DataFrame([asset_data])
        providers_df = pd.DataFrame([
            {"id": "asset-001", "hostname": "server01"},
        ])

        result = scorer.calculate_confidence(assets_df, providers_df)
        breakdown = result["confidence_breakdown"].iloc[0]
        assert breakdown['identifier_type'] == 'hostname'

    def test_breakdown_identifier_type_ip_only(self, scorer):
        """IP-only asset should yield 'ip_only'."""
        asset_data = {
            "id": "asset-ip",
            "ip": ["10.0.0.99"],
            "data_source": ["scanner"],
            "data_source_category": ["other"],
            "last_seen": datetime.now(timezone.utc),
        }
        assets_df = pd.DataFrame([asset_data])
        providers_df = pd.DataFrame([{"id": "asset-ip", "ip": "10.0.0.99"}])

        result = scorer.calculate_confidence(assets_df, providers_df)
        breakdown = result["confidence_breakdown"].iloc[0]
        assert breakdown['identifier_type'] == 'ip_only'

    def test_breakdown_identifier_type_minimal(self, scorer):
        """No identifiers at all should yield 'minimal'."""
        asset_data = {
            "id": "asset-minimal",
            "location": ["NYC"],
            "data_source": ["manual"],
            "data_source_category": ["other"],
            "last_seen": datetime.now(timezone.utc),
        }
        assets_df = pd.DataFrame([asset_data])
        providers_df = pd.DataFrame([{"id": "asset-minimal"}])

        result = scorer.calculate_confidence(assets_df, providers_df)
        breakdown = result["confidence_breakdown"].iloc[0]
        assert breakdown['identifier_type'] == 'minimal'

    def test_breakdown_missing_fields_populated(self, scorer):
        """Missing tier1 fields appear in missing_fields list."""
        # Asset missing sn and fqdn (tier1) and manufacturer/model (tier2)
        asset_data = {
            "id": "asset-sparse",
            "hostname": ["host1"],
            "mac": ["AA:BB:CC:DD:EE:FF"],
            "ip": ["10.0.0.1"],
            "data_source": ["src1"],
            "data_source_category": ["endpoint security"],
            "last_seen": datetime.now(timezone.utc),
        }
        assets_df = pd.DataFrame([asset_data])
        providers_df = pd.DataFrame([{"id": "asset-sparse", "hostname": "host1"}])

        result = scorer.calculate_confidence(assets_df, providers_df)
        breakdown = result["confidence_breakdown"].iloc[0]
        missing = breakdown['missing_fields']
        assert isinstance(missing, list)
        # sn and fqdn are tier1 fields not present
        assert 'sn' in missing
        assert 'fqdn' in missing
        # hostname and mac are present, should NOT be in missing
        assert 'hostname' not in missing
        assert 'mac' not in missing

    def test_breakdown_missing_fields_empty_when_all_present(self, scorer):
        """When all tier1+tier2 fields are present, missing_fields should be empty."""
        asset_data = self._make_full_asset()
        assets_df = pd.DataFrame([asset_data])
        providers_df = pd.DataFrame([
            {"id": "asset-001", "sn": "SN123", "hostname": "server01"},
        ])

        result = scorer.calculate_confidence(assets_df, providers_df)
        breakdown = result["confidence_breakdown"].iloc[0]
        assert breakdown['missing_fields'] == []

    def test_breakdown_conflicting_fields_populated(self, scorer):
        """Disagreeing providers should produce conflicting field names in list."""
        asset_data = {
            "id": ["comp1", "comp2"],
            "sn": ["SN001"],
            "hostname": ["host1"],
            "data_source": ["src1", "src2"],
            "data_source_category": ["endpoint security", "cloud platform"],
            "last_seen": datetime.now(timezone.utc),
        }
        assets_df = pd.DataFrame([asset_data])
        providers_df = pd.DataFrame([
            {"id": "comp1", "sn": "SN001", "hostname": "host1"},
            {"id": "comp2", "sn": "SN001", "hostname": "host2"},
        ])

        result = scorer.calculate_confidence(assets_df, providers_df)
        breakdown = result["confidence_breakdown"].iloc[0]
        conflicting = breakdown['conflicting_fields']
        assert isinstance(conflicting, list)
        assert 'hostname' in conflicting
        # sn agrees, should NOT be in conflicting
        assert 'sn' not in conflicting

    def test_breakdown_conflicting_fields_empty_when_all_agree(self, scorer):
        """When all providers agree, conflicting_fields should be empty."""
        asset_data = {
            "id": ["comp1", "comp2"],
            "sn": ["SN001"],
            "hostname": ["host1"],
            "data_source": ["src1", "src2"],
            "data_source_category": ["endpoint security", "cloud platform"],
            "last_seen": datetime.now(timezone.utc),
        }
        assets_df = pd.DataFrame([asset_data])
        providers_df = pd.DataFrame([
            {"id": "comp1", "sn": "SN001", "hostname": "host1"},
            {"id": "comp2", "sn": "SN001", "hostname": "host1"},
        ])

        result = scorer.calculate_confidence(assets_df, providers_df)
        breakdown = result["confidence_breakdown"].iloc[0]
        assert breakdown['conflicting_fields'] == []

    def test_breakdown_conflicting_fields_with_consistency_disabled(self):
        """When consistency is disabled, conflicting_fields should be empty."""
        scorer = AssetScorer(enable_consistency_penalty=False)
        asset_data = {
            "id": "asset-001",
            "sn": ["SN001"],
            "hostname": ["host1"],
            "data_source": ["src1"],
            "data_source_category": ["endpoint security"],
            "last_seen": datetime.now(timezone.utc),
        }
        assets_df = pd.DataFrame([asset_data])
        providers_df = pd.DataFrame([
            {"id": "asset-001", "sn": "SN001", "hostname": "host1"},
        ])

        result = scorer.calculate_confidence(assets_df, providers_df)
        breakdown = result["confidence_breakdown"].iloc[0]
        assert breakdown['conflicting_fields'] == []

    def test_breakdown_source_count(self, scorer):
        """source_count should match the number of data sources."""
        asset_data = self._make_full_asset(
            data_source=["intune", "azure", "defender"],
            data_source_category=["mobile device management", "cloud platform", "endpoint security"],
        )
        assets_df = pd.DataFrame([asset_data])
        providers_df = pd.DataFrame([
            {"id": "asset-001", "sn": "SN123", "hostname": "server01"},
        ])

        result = scorer.calculate_confidence(assets_df, providers_df)
        breakdown = result["confidence_breakdown"].iloc[0]
        assert breakdown['source_count'] == 3

    def test_breakdown_days_since_seen(self, scorer):
        """days_since_seen should reflect how old the data is."""
        last_seen = datetime.now(timezone.utc) - timedelta(days=15)
        asset_data = self._make_full_asset(last_seen=last_seen)
        assets_df = pd.DataFrame([asset_data])
        providers_df = pd.DataFrame([
            {"id": "asset-001", "sn": "SN123", "hostname": "server01"},
        ])

        result = scorer.calculate_confidence(assets_df, providers_df)
        breakdown = result["confidence_breakdown"].iloc[0]
        # Should be approximately 15 days
        assert 14 <= breakdown['days_since_seen'] <= 16

    def test_breakdown_days_since_seen_null_when_nat(self, scorer):
        """days_since_seen should be None when last_seen is NaT."""
        asset_data = self._make_full_asset(last_seen=pd.NaT)
        assets_df = pd.DataFrame([asset_data])
        providers_df = pd.DataFrame([
            {"id": "asset-001", "sn": "SN123", "hostname": "server01"},
        ])

        result = scorer.calculate_confidence(assets_df, providers_df)
        breakdown = result["confidence_breakdown"].iloc[0]
        assert breakdown['days_since_seen'] is None
        assert breakdown['recency_penalty'] == pytest.approx(0.95, abs=0.01)

    def test_breakdown_days_since_seen_null_when_pre_2000(self, scorer):
        """days_since_seen should be None when last_seen is a pre-2000 date."""
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        asset_data = self._make_full_asset(last_seen=epoch)
        assets_df = pd.DataFrame([asset_data])
        providers_df = pd.DataFrame([
            {"id": "asset-001", "sn": "SN123", "hostname": "server01"},
        ])

        result = scorer.calculate_confidence(assets_df, providers_df)
        breakdown = result["confidence_breakdown"].iloc[0]
        assert breakdown['days_since_seen'] is None
        assert breakdown['recency_penalty'] == pytest.approx(0.90, abs=0.01)


# ---------------------------------------------------------------------------
# FQDN Prefix Normalization Tests
# ---------------------------------------------------------------------------

class TestNormalizeFqdnPrefixes:
    """Tests for _normalize_fqdn_prefixes static method."""

    def test_bare_hostname_remapped_to_fqdn(self):
        """Bare hostname matching the start of an FQDN should be remapped."""
        values = pd.Series(["ncopbib1", "ncopbib1.primprod.net", "ncopbib1.primprod.net"])
        result = AssetScorer._normalize_fqdn_prefixes(values)
        assert result.nunique() == 1
        assert result.iloc[0] == "ncopbib1.primprod.net"

    def test_all_fqdns_agree_no_change(self):
        """When all values are the same FQDN, nothing changes."""
        values = pd.Series(["host.domain.com", "host.domain.com"])
        result = AssetScorer._normalize_fqdn_prefixes(values)
        assert result.nunique() == 1

    def test_different_domains_remain_conflicting(self):
        """host1.domain.com vs host1.other.com should NOT be merged."""
        values = pd.Series(["host1.domain.com", "host1.other.com"])
        result = AssetScorer._normalize_fqdn_prefixes(values)
        assert result.nunique() == 2

    def test_bare_hostname_no_matching_fqdn(self):
        """Bare hostname that doesn't prefix any FQDN stays unchanged."""
        values = pd.Series(["serverA", "serverB.domain.com"])
        result = AssetScorer._normalize_fqdn_prefixes(values)
        assert result.nunique() == 2

    def test_bare_hostname_matches_correct_fqdn(self):
        """When bare hostname matches an FQDN prefix, it maps to that FQDN."""
        values = pd.Series(["myhost", "myhost.corp.local", "myhost.corp.local"])
        result = AssetScorer._normalize_fqdn_prefixes(values)
        assert result.nunique() == 1
        assert (result == "myhost.corp.local").all()

    def test_single_value_unchanged(self):
        """Single value series returns unchanged."""
        values = pd.Series(["host.domain.com"])
        result = AssetScorer._normalize_fqdn_prefixes(values)
        assert len(result) == 1

    def test_all_bare_hostnames_different(self):
        """Multiple bare hostnames with no dots stay as conflicts."""
        values = pd.Series(["hostA", "hostB"])
        result = AssetScorer._normalize_fqdn_prefixes(values)
        assert result.nunique() == 2

    def test_partial_name_not_matched(self):
        """'nco' should NOT match 'ncopbib1.domain.com' (not a hostname boundary)."""
        values = pd.Series(["nco", "ncopbib1.domain.com"])
        result = AssetScorer._normalize_fqdn_prefixes(values)
        # "nco" doesn't match because ncopbib1.domain.com doesn't start with "nco."
        assert result.nunique() == 2


# ---------------------------------------------------------------------------
# Mixed IP Range Detection Tests
# ---------------------------------------------------------------------------

class TestHasMixedIpRanges:
    """Tests for _has_mixed_ip_ranges static method."""

    def test_private_and_public_ip(self):
        """Private 10.x + public IP → mixed."""
        values = pd.Series(["10.0.0.35", "52.146.85.165"])
        assert AssetScorer._has_mixed_ip_ranges(values) is True

    def test_private_192_and_public(self):
        """Private 192.168.x + public → mixed."""
        values = pd.Series(["192.168.1.100", "8.8.8.8"])
        assert AssetScorer._has_mixed_ip_ranges(values) is True

    def test_private_172_and_public(self):
        """Private 172.16-31.x + public → mixed."""
        values = pd.Series(["172.16.0.1", "203.0.113.5"])
        assert AssetScorer._has_mixed_ip_ranges(values) is True

    def test_all_private_same_range(self):
        """All private IPs from same range → not mixed."""
        values = pd.Series(["10.0.0.1", "10.0.0.2"])
        assert AssetScorer._has_mixed_ip_ranges(values) is False

    def test_all_public(self):
        """All public IPs → not mixed."""
        values = pd.Series(["52.146.85.165", "40.112.72.205"])
        assert AssetScorer._has_mixed_ip_ranges(values) is False

    def test_cidr_notation_stripped(self):
        """CIDR notation like /32 should be stripped before checking."""
        values = pd.Series(["10.0.0.35/32", "52.146.85.165/32"])
        assert AssetScorer._has_mixed_ip_ranges(values) is True

    def test_single_ip(self):
        """Single IP → not mixed."""
        values = pd.Series(["10.0.0.1"])
        assert AssetScorer._has_mixed_ip_ranges(values) is False

    def test_172_outside_private_range(self):
        """172.32.x is NOT private (only 172.16-31 is)."""
        values = pd.Series(["172.32.0.1", "8.8.8.8"])
        # Both are public
        assert AssetScorer._has_mixed_ip_ranges(values) is False


# ---------------------------------------------------------------------------
# Integration: Consistency Penalty with FQDN & IP Improvements
# ---------------------------------------------------------------------------

class TestConsistencyPenaltyImprovements:
    """Integration tests verifying the FQDN and IP improvements reduce false penalties."""

    def test_fqdn_prefix_no_penalty(self, scorer):
        """Asset with bare hostname FQDN + full FQDN should have no FQDN penalty."""
        providers_df = pd.DataFrame([
            {"id": "a1", "fqdn": "NCOPBIB1", "data_source_category": "cloud platform"},
            {"id": "a1", "fqdn": "ncopbib1.primprod.net", "data_source_category": "identity and access management"},
            {"id": "a1", "fqdn": "NCOPBIB1.primprod.net", "data_source_category": "endpoint security"},
        ])
        penalty = scorer._calculate_consistency_penalty(providers_df, ["a1"])
        assert penalty == 1.0  # No conflict after normalization

    def test_mixed_ip_halved_penalty(self, scorer):
        """Private + public IP should get halved penalty vs same-range conflict.

        Uses no data_source_category to avoid the reliability bonus masking
        the small IP penalty (5% max).
        """
        # Mixed ranges (private + public) — penalty should be halved
        providers_mixed = pd.DataFrame([
            {"id": "a1", "ip": "10.0.0.35"},
            {"id": "a1", "ip": "52.146.85.165"},
        ])
        penalty_mixed = scorer._calculate_consistency_penalty(providers_mixed, ["a1"])

        # Same-range conflict (all public, different IPs) — full penalty
        providers_same = pd.DataFrame([
            {"id": "a1", "ip": "52.146.85.165"},
            {"id": "a1", "ip": "40.112.72.205"},
        ])
        penalty_same = scorer._calculate_consistency_penalty(providers_same, ["a1"])

        # Mixed should be less penalized than same-range conflict
        assert penalty_mixed > penalty_same
        # Mixed penalty should be closer to 1.0 (halved)
        assert penalty_mixed >= 0.975

    def test_fqdn_conflict_still_penalized_different_domains(self, scorer):
        """Real FQDN conflicts (different domains) should still be penalized."""
        providers_df = pd.DataFrame([
            {"id": "a1", "fqdn": "host1.domain.com"},
            {"id": "a1", "fqdn": "host1.other.com"},
        ])
        penalty = scorer._calculate_consistency_penalty(providers_df, ["a1"])
        assert penalty < 1.0  # Should still be penalized

    def test_conflicting_fields_reflects_fqdn_normalization(self, scorer):
        """_get_conflicting_fields should NOT report fqdn when prefix matches."""
        providers_df = pd.DataFrame([
            {"id": "a1", "fqdn": "server1", "hostname": "server1"},
            {"id": "a1", "fqdn": "server1.corp.local", "hostname": "server1"},
        ])
        conflicts = scorer._get_conflicting_fields(providers_df, ["a1"])
        assert "fqdn" not in conflicts


class TestHostnameFqdnCorrelation:
    """Tests for hostname/FQDN correlation dedup (#278)."""

    def test_fqdn_penalty_skipped_when_hostname_conflicts(self, scorer):
        """When both hostname and FQDN conflict, only hostname penalty applies."""
        providers_df = pd.DataFrame([
            {"id": "a1", "hostname": "host1", "fqdn": "host1.domain.com"},
            {"id": "a1", "hostname": "host2", "fqdn": "host2.domain.com"},
        ])
        grouped = {aid: g for aid, g in providers_df.groupby("id")}
        multiplier, conflicts = scorer._calculate_consistency_and_conflicts(grouped, ["a1"])

        # hostname penalty = 0.60, FQDN should be skipped
        assert multiplier == pytest.approx(0.60, abs=0.01)
        assert "hostname" in conflicts
        assert "fqdn" not in conflicts

    def test_fqdn_penalty_applied_when_hostname_agrees(self, scorer):
        """When hostname agrees but FQDN conflicts, FQDN penalty still applies."""
        providers_df = pd.DataFrame([
            {"id": "a1", "hostname": "host1", "fqdn": "host1.domain.com"},
            {"id": "a1", "hostname": "host1", "fqdn": "host1.other.com"},
        ])
        grouped = {aid: g for aid, g in providers_df.groupby("id")}
        multiplier, conflicts = scorer._calculate_consistency_and_conflicts(grouped, ["a1"])

        assert multiplier == pytest.approx(0.65, abs=0.01)
        assert "fqdn" in conflicts
        assert "hostname" not in conflicts


class TestSourceCountFloor:
    """Tests for source-count-aware consistency floor (#279)."""

    def test_five_source_floor_prevents_crush(self, scorer):
        """5 sources with heavy conflicts should not drop below 0.50 floor."""
        providers_df = pd.DataFrame([
            {"id": "a1", "data_source": "src1", "sn": "SN1", "hostname": "h1", "mac": "AA:01", "ip": "10.0.0.1"},
            {"id": "a1", "data_source": "src2", "sn": "SN2", "hostname": "h2", "mac": "AA:02", "ip": "10.0.0.2"},
            {"id": "a1", "data_source": "src3", "sn": "SN3", "hostname": "h3", "mac": "AA:03", "ip": "10.0.0.3"},
            {"id": "a1", "data_source": "src4", "sn": "SN4", "hostname": "h4", "mac": "AA:04", "ip": "10.0.0.4"},
            {"id": "a1", "data_source": "src5", "sn": "SN5", "hostname": "h5", "mac": "AA:05", "ip": "10.0.0.5"},
        ])
        grouped = {aid: g for aid, g in providers_df.groupby("id")}
        multiplier, _ = scorer._calculate_consistency_and_conflicts(grouped, ["a1"])

        # Without floor, sn*hostname*mac*ip = 0.30*0.60*0.95*0.95 = 0.1625
        # With floor for 5 unique sources: max(0.50, 0.1625) = 0.50
        assert multiplier >= 0.50

    def test_two_source_floor(self, scorer):
        """2 sources with sn conflict: floor is 0.20, natural penalty is 0.30 — floor not applied."""
        providers_df = pd.DataFrame([
            {"id": "a1", "data_source": "src1", "sn": "SN1"},
            {"id": "a1", "data_source": "src2", "sn": "SN2"},
        ])
        grouped = {aid: g for aid, g in providers_df.groupby("id")}
        multiplier, _ = scorer._calculate_consistency_and_conflicts(grouped, ["a1"])

        # Natural penalty 0.30 > floor 0.20, so floor has no effect
        assert multiplier == pytest.approx(0.30, abs=0.01)

    def test_no_conflicts_floor_irrelevant(self, scorer):
        """When sources agree, multiplier is 1.0 — floor is irrelevant."""
        providers_df = pd.DataFrame([
            {"id": "a1", "data_source": "src1", "sn": "SN1", "hostname": "host1"},
            {"id": "a1", "data_source": "src2", "sn": "SN1", "hostname": "host1"},
            {"id": "a1", "data_source": "src3", "sn": "SN1", "hostname": "host1"},
        ])
        grouped = {aid: g for aid, g in providers_df.groupby("id")}
        multiplier, conflicts = scorer._calculate_consistency_and_conflicts(grouped, ["a1"])

        assert multiplier == 1.0
        assert conflicts == []


# ---------------------------------------------------------------------------
# last_time → last_seen → recency penalty flow
# ---------------------------------------------------------------------------

class TestLastTimeRecencyFlow:
    """Verify that last_seen derived from last_time feeds recency scoring correctly."""

    def test_last_seen_from_last_time_applies_recency(self, scorer):
        """When last_seen has a real timestamp, scorer applies proper recency penalty."""
        old_time = datetime.now(timezone.utc) - timedelta(days=60)
        assets_df = pd.DataFrame([{
            "id": "a1",
            "sn": ["SN001"],
            "data_source": ["src1"],
            "data_source_category": ["endpoint security"],
            "last_seen": old_time,
        }])
        providers_df = pd.DataFrame([{"id": "a1", "sn": "SN001"}])

        result = scorer.calculate_confidence(assets_df, providers_df)
        penalty = result["confidence_recency_penalty"].iloc[0]
        # 60 days old should yield a meaningful penalty (< 1.0)
        assert penalty < 0.90
        assert penalty > 0.40  # floor is 0.40

        breakdown = result["confidence_breakdown"].iloc[0]
        assert 59 <= breakdown["days_since_seen"] <= 61

    def test_nat_last_seen_gets_unknown_freshness_penalty(self, scorer):
        """NaT last_seen yields the 0.95 unknown-freshness penalty, not 1.0."""
        assets_df = pd.DataFrame([{
            "id": "a1",
            "sn": ["SN001"],
            "data_source": ["src1"],
            "data_source_category": ["endpoint security"],
            "last_seen": pd.NaT,
        }])
        providers_df = pd.DataFrame([{"id": "a1", "sn": "SN001"}])

        result = scorer.calculate_confidence(assets_df, providers_df)
        assert result["confidence_recency_penalty"].iloc[0] == pytest.approx(0.95, abs=0.01)
        assert result["confidence_breakdown"].iloc[0]["days_since_seen"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
