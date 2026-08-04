# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests that build_registry forwards the configured confidence thresholds
to the sectioned get_dashboard_impl (which both get_dashboard and the
deprecated get_data_quality shim now call).

The closures are async; we drive them with asyncio.run() — the same
pattern used by tests in tests/unit/blueprints/chat/llm/.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from src.api.blueprints.chat.tool_bindings import build_registry


class _FakeSettingsManager:
    """Minimal settings_manager stub that returns 0.50/0.90 thresholds."""

    def get(self, category, key, default=None):
        if key == "confidence_threshold_medium":
            return 0.50
        if key == "confidence_threshold_high":
            return 0.90
        return default


def test_get_dashboard_forwards_configured_thresholds():
    fake_sm = _FakeSettingsManager()

    with patch(
        "src.api.blueprints.chat.tool_bindings.get_dashboard_impl",
        return_value="",
    ) as mock_impl:
        registry = build_registry(db=MagicMock(), settings_manager=fake_sm)
        handler, _ = registry._tools["get_dashboard"]
        asyncio.run(handler())

    mock_impl.assert_called_once()
    _, kwargs = mock_impl.call_args
    assert kwargs.get("medium") == 0.50, f"medium={kwargs.get('medium')!r}"
    assert kwargs.get("high") == 0.90, f"high={kwargs.get('high')!r}"


def test_get_data_quality_shim_forwards_thresholds_and_section():
    """get_data_quality is now a deprecated shim onto the sectioned
    get_dashboard_impl: it must still forward the configured thresholds AND the
    category, pinned to section='data_quality'."""
    fake_sm = _FakeSettingsManager()

    with patch(
        "src.api.blueprints.chat.tool_bindings.get_dashboard_impl",
        return_value=("", None),
    ) as mock_impl:
        registry = build_registry(db=MagicMock(), settings_manager=fake_sm)
        handler, _ = registry._tools["get_data_quality"]
        asyncio.run(handler(category="server"))

    mock_impl.assert_called_once()
    _, kwargs = mock_impl.call_args
    assert kwargs.get("medium") == 0.50, f"medium={kwargs.get('medium')!r}"
    assert kwargs.get("high") == 0.90, f"high={kwargs.get('high')!r}"
    assert kwargs.get("category") == "server"
    assert kwargs.get("section") == "data_quality"


def test_get_dashboard_uses_defaults_when_no_settings_manager():
    """When settings_manager=None the accessor returns (0.45, 0.70) defaults."""
    with patch(
        "src.api.blueprints.chat.tool_bindings.get_dashboard_impl",
        return_value="",
    ) as mock_impl:
        registry = build_registry(db=MagicMock(), settings_manager=None)
        handler, _ = registry._tools["get_dashboard"]
        asyncio.run(handler())

    mock_impl.assert_called_once()
    _, kwargs = mock_impl.call_args
    assert kwargs.get("medium") == 0.45
    assert kwargs.get("high") == 0.70
