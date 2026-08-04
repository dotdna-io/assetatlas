# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for OSEOLEnricher from os_eol_enricher.py.

calculate_eol_status() tests are pure date-logic. enrich_asset() tests mock the
DB-touching _lookup_eol_with_cache seam. We instantiate OSEOLEnricher with a mock
eol_service since neither path needs a live database.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from src.modules.asset_generation.domain.os_eol_enricher import (
    OSEOLEnricher,
    EOL_APPROACHING_THRESHOLD_DAYS,
)
from src.modules.asset_generation.services.eol_data_service import EOLData


@pytest.fixture
def enricher():
    """Create an OSEOLEnricher with a dummy eol_service."""
    mock_service = MagicMock()
    return OSEOLEnricher(eol_service=mock_service)


class TestCalculateEolStatus:
    """Tests for calculate_eol_status()."""

    def test_none_returns_unknown(self, enricher):
        assert enricher.calculate_eol_status(None) == "unknown"

    def test_past_date_returns_end_of_life(self, enricher):
        past = date.today() - timedelta(days=365)
        assert enricher.calculate_eol_status(past) == "end_of_life"

    def test_yesterday_returns_end_of_life(self, enricher):
        yesterday = date.today() - timedelta(days=1)
        assert enricher.calculate_eol_status(yesterday) == "end_of_life"

    def test_far_future_returns_active(self, enricher):
        future = date.today() + timedelta(days=365)
        assert enricher.calculate_eol_status(future) == "active"

    def test_within_threshold_returns_approaching_eol(self, enricher):
        """A date within the 180-day threshold should be approaching_eol."""
        within = date.today() + timedelta(days=90)
        assert enricher.calculate_eol_status(within) == "approaching_eol"

    def test_exactly_at_threshold_returns_approaching_eol(self, enricher):
        """Exactly at the threshold boundary (180 days) is approaching_eol."""
        boundary = date.today() + timedelta(days=EOL_APPROACHING_THRESHOLD_DAYS)
        assert enricher.calculate_eol_status(boundary) == "approaching_eol"

    def test_one_past_threshold_returns_active(self, enricher):
        """One day past the threshold boundary is active."""
        beyond = date.today() + timedelta(days=EOL_APPROACHING_THRESHOLD_DAYS + 1)
        assert enricher.calculate_eol_status(beyond) == "active"

    def test_today_returns_approaching_eol(self, enricher):
        """EOL date = today means days_until_eol=0, which is NOT < 0,
        so it falls into the approaching_eol bucket (<= 180)."""
        assert enricher.calculate_eol_status(date.today()) == "approaching_eol"

    def test_tomorrow_returns_approaching_eol(self, enricher):
        tomorrow = date.today() + timedelta(days=1)
        assert enricher.calculate_eol_status(tomorrow) == "approaching_eol"

    def test_threshold_constant_is_180(self):
        """Verify the threshold constant matches expected value."""
        assert EOL_APPROACHING_THRESHOLD_DAYS == 180


class TestEnrichAssetResolvesFromNameWithoutVersion:
    """#681 A10: enrich_asset must attempt the EOL lookup whenever os_name is
    present, even when no version token can be parsed out of os_normalized.

    Windows Server / iOS / Android carry the version inside os_name, and
    EOLDataService.lookup_eol_data resolves them from os_name alone. Bailing to
    'unknown' before the lookup (the old `if not os_name or not os_version`)
    silently dropped EOL coverage for that security-relevant class of assets.
    """

    def test_windows_server_resolves_via_name_only_lookup(self, enricher):
        """'windows server 2019 standard' has no parseable version token (the
        last token 'standard' has no digit), yet the lookup resolves the year
        from os_name. The enricher must call the lookup with os_version=None and
        apply the resolved status instead of bailing to 'unknown'.

        Fails on pre-fix code: the bail fires, the lookup is never called, and
        os_eol_status stays 'unknown'.
        """
        resolved = EOLData(
            product_name="windows-server",
            cycle="2019",
            eol_date=date(2020, 1, 14),  # firmly past -> deterministic end_of_life
            support_end_date=date(2019, 1, 9),
            lts=False,
        )
        enricher._lookup_eol_with_cache = MagicMock(return_value=resolved)

        asset = {
            "os_normalized": "windows server 2019 standard",
            # os_enrichment present but carries no 'version' key
            "os_enrichment": {"raw_os": "Windows Server 2019 Standard"},
        }

        result = enricher.enrich_asset(asset)

        # Lookup is attempted with the name and a None version; lookup_eol_data
        # resolves the year from os_name for the windows-server product.
        enricher._lookup_eol_with_cache.assert_called_once_with(
            "windows server 2019 standard", None
        )
        # Resolved status flows through to the asset — NOT 'unknown'.
        assert result["os_eol_status"] == "end_of_life"
        assert result["os_eol_date"] == date(2020, 1, 14)

    def test_unresolvable_os_still_falls_back_to_unknown(self, enricher):
        """Control: when os_name is present but the lookup itself can't resolve
        it (returns None), the asset still falls back to 'unknown'. Proves the
        fix attempts the lookup AND preserves the genuine no-match path."""
        enricher._lookup_eol_with_cache = MagicMock(return_value=None)

        asset = {
            "os_normalized": "windows server 2019 standard",
            "os_enrichment": {"raw_os": "Windows Server 2019 Standard"},
        }

        result = enricher.enrich_asset(asset)

        enricher._lookup_eol_with_cache.assert_called_once_with(
            "windows server 2019 standard", None
        )
        assert result["os_eol_status"] == "unknown"
        assert result["os_eol_date"] is None

    def test_missing_os_name_bails_without_calling_lookup(self, enricher):
        """When there is no os_name at all, there is nothing to look up: bail to
        'unknown' without calling the lookup. Guards the remaining bail branch."""
        enricher._lookup_eol_with_cache = MagicMock(return_value=None)

        asset = {"os_normalized": None, "os_enrichment": None}

        result = enricher.enrich_asset(asset)

        enricher._lookup_eol_with_cache.assert_not_called()
        assert result["os_eol_status"] == "unknown"
        assert result["os_eol_date"] is None

    def test_nan_os_name_bails_without_calling_lookup(self, enricher):
        """A missing os_normalized arrives as float('nan') (NOT None) when the
        column holds an explicit np.nan or gains gaps via reindex/merge —
        df.to_dict('records') (used by enrich_dataframe) preserves NaN. NaN is
        truthy, so it must be coerced to 'no name' and bail. Otherwise it slips
        past `if not os_name` into the lookup, where map_os_to_product raises
        AttributeError on nan.lower(), and enrich_os_eol's broad except then
        wipes EOL data for the WHOLE batch.

        Fails on an unguarded `if not os_name:` — NaN reaches the lookup."""
        enricher._lookup_eol_with_cache = MagicMock(return_value=None)

        asset = {"os_normalized": float("nan"), "os_enrichment": None}

        result = enricher.enrich_asset(asset)

        enricher._lookup_eol_with_cache.assert_not_called()
        assert result["os_eol_status"] == "unknown"
        assert result["os_eol_date"] is None
