# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for last_time aggregation and lifecycle flow.

Verifies that last_time (max timestamp from providers) flows correctly through
the aggregation helpers and into the lifecycle metadata that feeds recency scoring.
"""

import pandas as pd


class TestTimestampAggregationHelpers:
    """
    Test the get_min_timestamp / get_max_timestamp helpers used by
    _convert_arrays_to_scalars() in asset_generation_service.py.

    These helpers convert aggregated list-of-timestamp-strings into scalars.
    Replicated here to test in isolation without needing a DB-backed service.
    """

    @staticmethod
    def get_min_timestamp(arr):
        """Replica of the helper in _convert_arrays_to_scalars."""
        if not isinstance(arr, list) or len(arr) == 0:
            return None
        valid = [x for x in arr if x]
        return min(valid) if valid else None

    @staticmethod
    def get_max_timestamp(arr):
        """Replica of the helper in _convert_arrays_to_scalars."""
        if not isinstance(arr, list) or len(arr) == 0:
            return None
        valid = [x for x in arr if x]
        return max(valid) if valid else None

    def test_max_single_timestamp(self):
        """Single-element list returns that timestamp."""
        result = self.get_max_timestamp(["2025-06-15 10:00:00+00:00"])
        assert result == "2025-06-15 10:00:00+00:00"

    def test_max_picks_latest(self):
        """Multiple timestamps return the latest."""
        result = self.get_max_timestamp([
            "2025-01-01 00:00:00+00:00",
            "2025-06-15 10:00:00+00:00",
            "2025-03-10 12:30:00+00:00",
        ])
        assert result == "2025-06-15 10:00:00+00:00"

    def test_min_picks_earliest(self):
        """Multiple timestamps return the earliest."""
        result = self.get_min_timestamp([
            "2025-06-15 10:00:00+00:00",
            "2025-01-01 00:00:00+00:00",
            "2025-03-10 12:30:00+00:00",
        ])
        assert result == "2025-01-01 00:00:00+00:00"

    def test_empty_list_returns_none(self):
        assert self.get_max_timestamp([]) is None
        assert self.get_min_timestamp([]) is None

    def test_non_list_returns_none(self):
        assert self.get_max_timestamp(None) is None
        assert self.get_max_timestamp("not-a-list") is None

    def test_list_with_falsy_elements_skipped(self):
        """Empty strings and None in the list are filtered out."""
        result = self.get_max_timestamp(["", None, "2025-03-10 12:30:00+00:00"])
        assert result == "2025-03-10 12:30:00+00:00"

    def test_all_falsy_returns_none(self):
        assert self.get_max_timestamp(["", None]) is None


class TestLastTimeAggregationMax:
    """
    Test that aggregation computes max(last_time) per group — the same logic
    used in _aggregate_assets() step 3.
    """

    def test_max_last_time_across_providers(self):
        """Simulates the aggregation loop that picks max(last_time) per group."""
        # Two rows for the same asset from different providers with different last_time
        df = pd.DataFrame([
            {"id": "asset-1", "last_time": pd.Timestamp("2025-01-10 08:00:00", tz="UTC")},
            {"id": "asset-1", "last_time": pd.Timestamp("2025-06-15 14:30:00", tz="UTC")},
        ])

        # Simulate the aggregation logic from lines 1580-1588
        result = df.groupby("id")["last_time"].max().reset_index()
        assert result["last_time"].iloc[0] == pd.Timestamp("2025-06-15 14:30:00", tz="UTC")

    def test_min_first_time_across_providers(self):
        """Aggregation picks min(first_time) per group."""
        df = pd.DataFrame([
            {"id": "asset-1", "first_time": pd.Timestamp("2025-01-10 08:00:00", tz="UTC")},
            {"id": "asset-1", "first_time": pd.Timestamp("2025-06-15 14:30:00", tz="UTC")},
        ])

        result = df.groupby("id")["first_time"].min().reset_index()
        assert result["first_time"].iloc[0] == pd.Timestamp("2025-01-10 08:00:00", tz="UTC")

    def test_nat_values_ignored_in_max(self):
        """NaT values should not override real timestamps in max()."""
        df = pd.DataFrame([
            {"id": "asset-1", "last_time": pd.Timestamp("2025-03-10 12:00:00", tz="UTC")},
            {"id": "asset-1", "last_time": pd.NaT},
        ])

        result = df.groupby("id")["last_time"].max().reset_index()
        assert result["last_time"].iloc[0] == pd.Timestamp("2025-03-10 12:00:00", tz="UTC")

    def test_all_nat_returns_nat(self):
        """If all providers have NaT, aggregation should return NaT."""
        df = pd.DataFrame([
            {"id": "asset-1", "last_time": pd.NaT},
            {"id": "asset-1", "last_time": pd.NaT},
        ])

        result = df.groupby("id")["last_time"].max().reset_index()
        assert pd.isna(result["last_time"].iloc[0])


class TestLastTimeToLastSeenConversion:
    """
    Test that last_time string values correctly convert to last_seen datetime
    via pd.to_datetime (the logic in _populate_lifecycle_metadata).
    """

    def test_iso_string_parsed_to_datetime(self):
        """ISO timestamp string from aggregation is correctly parsed."""
        df = pd.DataFrame([{"last_time": "2025-06-15 14:30:00+00:00"}])
        df["last_seen"] = pd.to_datetime(df["last_time"], errors="coerce", utc=True)

        assert df["last_seen"].iloc[0] == pd.Timestamp("2025-06-15 14:30:00", tz="UTC")

    def test_none_last_time_becomes_nat(self):
        """None last_time produces NaT last_seen."""
        df = pd.DataFrame([{"last_time": None}])
        df["last_seen"] = pd.to_datetime(df["last_time"], errors="coerce", utc=True)

        assert pd.isna(df["last_seen"].iloc[0])

    def test_pandas_timestamp_str_roundtrips(self):
        """
        Verify that str(pd.Timestamp(...)) produces a string parseable by pd.to_datetime.
        This is the actual roundtrip: aggregation does str(val), lifecycle does pd.to_datetime.
        """
        original = pd.Timestamp("2025-06-15 14:30:00", tz="UTC")
        stringified = str(original)
        parsed = pd.to_datetime(stringified, errors="coerce", utc=True)

        assert parsed == original

    def test_mixed_precision_timestamps_all_parsed(self):
        """
        Regression: pd.to_datetime without format='ISO8601' infers a fixed format
        from the first value. If the first value has second precision and later values
        have microsecond precision (or vice versa), the mismatched ones get silently
        coerced to NaT. This was the root cause of 6,299 assets losing last_seen.
        """
        # Simulate the last_time column after _convert_arrays_to_scalars:
        # some values have microseconds (from sources like Armis), some don't
        df = pd.DataFrame({
            "last_time": [
                "2025-06-15 14:30:00+00:00",          # no microseconds
                "2025-06-15 14:30:08.817102+00:00",    # microseconds
                None,
                "2025-03-23 11:33:17.905357+00:00",    # microseconds
                "2025-01-10 08:00:00+00:00",           # no microseconds
            ]
        })

        # The fix: use format='ISO8601' to handle variable precision
        df["last_seen"] = pd.to_datetime(
            df["last_time"], errors="coerce", utc=True, format="ISO8601"
        )

        assert df["last_seen"].notna().sum() == 4  # all non-None values parsed
        assert pd.isna(df["last_seen"].iloc[2])    # None stays NaT

        # Verify exact values
        assert df["last_seen"].iloc[0] == pd.Timestamp("2025-06-15 14:30:00", tz="UTC")
        assert df["last_seen"].iloc[1] == pd.Timestamp("2025-06-15 14:30:08.817102", tz="UTC")
        assert df["last_seen"].iloc[3] == pd.Timestamp("2025-03-23 11:33:17.905357", tz="UTC")
        assert df["last_seen"].iloc[4] == pd.Timestamp("2025-01-10 08:00:00", tz="UTC")

    def test_mixed_precision_without_format_drops_values(self):
        """
        Demonstrate the bug: without format='ISO8601', mixed-precision timestamps
        cause silent data loss via coercion to NaT.
        """
        df = pd.DataFrame({
            "last_time": [
                "2025-06-15 14:30:00+00:00",          # no microseconds
                "2025-06-15 14:30:08.817102+00:00",    # microseconds
            ]
        })

        # BUG: second value gets coerced to NaT because it doesn't match
        # the format inferred from the first value
        result = pd.to_datetime(df["last_time"], errors="coerce", utc=True)
        assert result.notna().sum() < 2  # at least one value lost
