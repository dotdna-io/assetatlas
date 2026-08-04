# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the get_reliability MCP tool."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.mcp.tools.reliability import get_reliability_impl


def test_get_reliability_happy_path_formats_sections_and_scales_scores():
    svc = MagicMock()
    svc.get_reliability.return_value = {
        "mostReliableSource": {"name": "Jamf", "score": 0.87},
        "leastReliableSource": {"name": "Legacy CMDB", "score": 0.42},
        "mostReliableCategory": {"name": "Workstation", "score": 92.3},
        "leastReliableCategory": {"name": "Network", "score": 55.1},
    }

    out = get_reliability_impl(svc)

    assert "# Data reliability" in out
    assert "## Data sources" in out
    assert "## Asset categories" in out

    # Source scores are raw 0..1 → scaled to %
    assert "Jamf" in out
    assert "87.0%" in out
    assert "Legacy CMDB" in out
    assert "42.0%" in out

    # Category scores already % → pass-through, 1 decimal
    assert "Workstation" in out
    assert "92.3%" in out
    assert "Network" in out
    assert "55.1%" in out


def test_get_reliability_handles_service_exception():
    svc = MagicMock()
    svc.get_reliability.side_effect = RuntimeError("db connection lost")

    out = get_reliability_impl(svc)

    assert out.startswith("Reliability data unavailable")
    assert "db connection lost" in out


def test_get_reliability_handles_na_placeholders():
    svc = MagicMock()
    svc.get_reliability.return_value = {
        "mostReliableSource": {"name": "N/A", "score": 0},
        "leastReliableSource": {"name": "N/A", "score": 0},
        "mostReliableCategory": {"name": "N/A", "score": 0},
        "leastReliableCategory": {"name": "N/A", "score": 0},
    }

    out = get_reliability_impl(svc)

    assert "# Data reliability" in out
    assert "N/A" in out
    assert "0.0%" in out
