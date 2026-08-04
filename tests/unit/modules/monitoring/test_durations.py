# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for Prometheus duration parsing/validation."""

import pytest

from src.modules.monitoring.durations import (
    parse_prometheus_duration,
    is_writable_duration,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("30d", 2592000),
        ("15d", 1296000),
        ("12h", 43200),
        ("1y", 31536000),
        ("2w", 1209600),
        ("90s", 90),
        ("0", 0),
        ("4w2d", 2592000),  # compound == 30d (Prometheus's normalized form)
        ("500ms", 0),  # rounds down to whole seconds
    ],
)
def test_parse_valid(value, expected):
    assert parse_prometheus_duration(value) == expected


@pytest.mark.parametrize(
    "value", ["", "abc", "30", "d", "30dd", "1x", "-5d", "30 d", "٣٠d", None]
)
def test_parse_invalid_returns_none(value):
    # "٣٠d" — Arabic-Indic digits: rejected so parse stays ASCII-only, matching
    # the CLI's [0-9]+ (Python's \d and int() would otherwise accept them).
    assert parse_prometheus_duration(value) is None


def test_compound_equals_single_semantically():
    assert parse_prometheus_duration("4w2d") == parse_prometheus_duration("30d")


@pytest.mark.parametrize("value", ["30d", "15d", "1y", "12h", "500ms", "0s"])
def test_is_writable_accepts_single_unit(value):
    assert is_writable_duration(value) is True


@pytest.mark.parametrize(
    "value", ["", "4w2d", "1y2w", "30", "abc", "30 d", "٣٠d", None]
)
def test_is_writable_rejects_compound_and_junk(value):
    # "٣٠d" — Arabic-Indic digits must be rejected so a UI-accepted value can
    # never be one the CLI's ^[0-9]+(...)$ regex later refuses on the host.
    assert is_writable_duration(value) is False
