# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for AssetEnricher._parse_timestamps YYYYMMDD handling — #681 B1 (sub-item 1)."""

import pandas as pd

from src.modules.asset_generation.domain.asset_enricher import AssetEnricher


class TestParseTimestampsYYYYMMDD:
    def test_yyyymmdd_string_is_a_date_not_1970_epoch(self):
        # Pre-fix: "20250615" -> 1970-08-23 (parsed as epoch seconds). FAIL-FIRST.
        result = AssetEnricher._parse_timestamps(pd.Series(["20250615"]))
        assert result.iloc[0] == pd.Timestamp("2025-06-15", tz="UTC")

    def test_yyyymmdd_int_is_a_date(self):
        result = AssetEnricher._parse_timestamps(pd.Series([20250615]))
        assert result.iloc[0] == pd.Timestamp("2025-06-15", tz="UTC")

    def test_invalid_8digit_falls_back_to_epoch(self):
        # 20259999 is not a valid %Y%m%d (month 99) -> NaT for date -> epoch path.
        # As epoch seconds this is ~1970; the point is it is NOT a valid date.
        result = AssetEnricher._parse_timestamps(pd.Series([20259999]))
        assert result.iloc[0].year == 1970   # epoch-seconds fallback, not a 2025 date

    # --- regression guards: the existing paths must be unchanged ---
    def test_real_epoch_seconds_still_epoch(self):
        # 1718409600 = 2024-06-15 (10-digit epoch seconds). Must stay epoch.
        result = AssetEnricher._parse_timestamps(pd.Series([1718409600]))
        assert result.iloc[0] == pd.Timestamp("2024-06-15 00:00:00", tz="UTC")

    def test_epoch_millis_still_epoch(self):
        result = AssetEnricher._parse_timestamps(pd.Series([1718409600000]))
        assert result.iloc[0].year == 2024

    def test_iso_string_still_parsed(self):
        result = AssetEnricher._parse_timestamps(pd.Series(["2025-06-15T10:00:00Z"]))
        assert result.iloc[0] == pd.Timestamp("2025-06-15 10:00:00", tz="UTC")

    def test_empty_and_null_safe(self):
        assert AssetEnricher._parse_timestamps(pd.Series([], dtype=object)).empty
        out = AssetEnricher._parse_timestamps(pd.Series([None, "20250615"]))
        assert pd.isna(out.iloc[0]) and out.iloc[1] == pd.Timestamp("2025-06-15", tz="UTC")

    # --- crash regression: a non-integer float in the YYYYMMDD range must not
    #     blow up the whole-batch parse (#681 B1 review). A bare YYYYMMDD is
    #     always an integer; a fractional numeric is not one, so it stays on the
    #     epoch path (same instant as before the fix), it does NOT crash. ---
    def test_fractional_float_in_ymd_range_does_not_crash(self):
        result = AssetEnricher._parse_timestamps(pd.Series([20250615.5]))
        assert result.iloc[0].year == 1970   # epoch-seconds fallback, no TypeError

    def test_one_fractional_value_does_not_destroy_the_whole_series(self):
        # The bug aborted parsing for EVERY row when one value was fractional;
        # the good rows must still resolve.
        out = AssetEnricher._parse_timestamps(
            pd.Series(["20250615.5", "20250615", "1718409600"])
        )
        assert out.iloc[1] == pd.Timestamp("2025-06-15", tz="UTC")   # YYYYMMDD date
        assert out.iloc[2] == pd.Timestamp("2024-06-15", tz="UTC")   # epoch seconds
        assert out.iloc[0].year == 1970                              # fractional -> epoch

    def test_below_range_is_epoch_at_floor_is_date(self):
        # Locks the lower carve-out constant against silent drift: 18991231
        # (< 19000101) stays on the epoch path; 19000101 (the floor) is a date.
        out = AssetEnricher._parse_timestamps(pd.Series([18991231, 19000101]))
        assert out.iloc[0].year == 1970                              # below floor -> epoch
        assert out.iloc[1] == pd.Timestamp("1900-01-01", tz="UTC")   # at floor -> date
