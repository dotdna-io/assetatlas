# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for cron expression validation and utilities.

Tests is_valid_cron, expand_preset, calculate_next_run,
get_cron_description, and validate_timezone.
"""

import pytest
from datetime import datetime

import pytz

from src.modules.integrations.shared.cron_validator import (
    is_valid_cron,
    expand_preset,
    calculate_next_run,
    get_cron_description,
    validate_timezone,
)


# ---------------------------------------------------------------------------
# is_valid_cron
# ---------------------------------------------------------------------------

class TestIsValidCron:

    @pytest.mark.parametrize("expr", [
        "0 * * * *",
        "*/5 * * * *",
        "0 0 * * *",
        "0 0 1 * *",
        "30 2 * * 1-5",
    ])
    def test_valid_expressions(self, expr):
        assert is_valid_cron(expr) is True

    @pytest.mark.parametrize("expr", [
        "invalid",
        "* * *",
        "60 * * * *",
        "",
    ])
    def test_invalid_expressions(self, expr):
        assert is_valid_cron(expr) is False

    def test_none_is_invalid(self):
        assert is_valid_cron(None) is False

    def test_non_string_is_invalid(self):
        assert is_valid_cron(123) is False

    @pytest.mark.parametrize("preset", [
        "@hourly",
        "@daily",
        "@weekly",
        "@monthly",
        "@yearly",
        "@annually",
    ])
    def test_presets_are_valid(self, preset):
        assert is_valid_cron(preset) is True


# ---------------------------------------------------------------------------
# expand_preset
# ---------------------------------------------------------------------------

class TestExpandPreset:

    def test_hourly(self):
        assert expand_preset("@hourly") == "0 * * * *"

    def test_daily(self):
        assert expand_preset("@daily") == "0 0 * * *"

    def test_weekly(self):
        assert expand_preset("@weekly") == "0 0 * * 0"

    def test_monthly(self):
        assert expand_preset("@monthly") == "0 0 1 * *"

    def test_yearly(self):
        assert expand_preset("@yearly") == "0 0 1 1 *"

    def test_annually_same_as_yearly(self):
        assert expand_preset("@annually") == expand_preset("@yearly")

    def test_non_preset_passes_through(self):
        expr = "*/10 * * * *"
        assert expand_preset(expr) == expr

    def test_case_insensitive(self):
        assert expand_preset("@HOURLY") == "0 * * * *"


# ---------------------------------------------------------------------------
# calculate_next_run
# ---------------------------------------------------------------------------

class TestCalculateNextRun:

    def test_returns_datetime(self):
        result = calculate_next_run("0 * * * *")
        assert isinstance(result, datetime)

    def test_result_is_in_future(self):
        now = datetime.now(pytz.UTC)
        result = calculate_next_run("0 * * * *", "UTC")
        assert result > now

    def test_with_preset(self):
        result = calculate_next_run("@hourly")
        assert isinstance(result, datetime)

    def test_with_explicit_base_time(self):
        base = datetime(2025, 6, 15, 10, 30, 0, tzinfo=pytz.UTC)
        result = calculate_next_run("0 * * * *", "UTC", base_time=base)
        assert result > base
        # Next hour at :00 from 10:30 is 11:00
        assert result.hour == 11
        assert result.minute == 0

    def test_with_naive_base_time(self):
        base = datetime(2025, 6, 15, 10, 30, 0)
        result = calculate_next_run("0 * * * *", "UTC", base_time=base)
        assert result is not None

    def test_invalid_expression_returns_none(self):
        assert calculate_next_run("invalid") is None

    def test_invalid_timezone_returns_none(self):
        assert calculate_next_run("0 * * * *", "Not/A/Timezone") is None

    def test_timezone_aware_result(self):
        result = calculate_next_run("0 * * * *", "US/Eastern")
        assert result.tzinfo is not None


# ---------------------------------------------------------------------------
# get_cron_description
# ---------------------------------------------------------------------------

class TestGetCronDescription:

    def test_returns_non_empty_string(self):
        desc = get_cron_description("0 * * * *")
        assert isinstance(desc, str)
        assert len(desc) > 0

    def test_preset_hourly(self):
        assert get_cron_description("@hourly") == "Every hour at :00"

    def test_preset_daily(self):
        assert get_cron_description("@daily") == "Daily at midnight"

    def test_every_minute(self):
        desc = get_cron_description("* * * * *")
        assert "minute" in desc.lower()

    def test_every_5_minutes(self):
        desc = get_cron_description("*/5 * * * *")
        assert "5" in desc

    def test_daily_at_specific_hour(self):
        desc = get_cron_description("0 14 * * *")
        assert "14" in desc


# ---------------------------------------------------------------------------
# validate_timezone
# ---------------------------------------------------------------------------

class TestValidateTimezone:

    def test_utc(self):
        assert validate_timezone("UTC") is True

    def test_us_eastern(self):
        assert validate_timezone("US/Eastern") is True

    def test_america_new_york(self):
        assert validate_timezone("America/New_York") is True

    def test_nonsense_fails(self):
        assert validate_timezone("Nonsense/Timezone") is False

    def test_empty_string_fails(self):
        assert validate_timezone("") is False
