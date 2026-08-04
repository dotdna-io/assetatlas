# SPDX-License-Identifier: AGPL-3.0-or-later
from src.api.modules.settings.settings_schema import (
    get_confidence_thresholds,
    DEFAULT_CONFIDENCE_THRESHOLDS,
)


class _FakeSettings:
    def __init__(self, medium, high):
        self._vals = {
            ("asset_generation", "confidence_threshold_medium"): medium,
            ("asset_generation", "confidence_threshold_high"): high,
        }

    def get(self, category, key, default=None):
        return self._vals.get((category, key), default)


def test_returns_defaults_when_no_manager():
    # WHY: every surface must have a safe fallback outside app/MCP context
    # (pipeline, scripts) so it degrades to the documented default, not a crash.
    assert get_confidence_thresholds(None) == DEFAULT_CONFIDENCE_THRESHOLDS


def test_reads_configured_values():
    # WHY: this is the whole point — a non-default slider value must flow through.
    assert get_confidence_thresholds(_FakeSettings(0.50, 0.90)) == (0.50, 0.90)


def test_falls_back_when_value_missing():
    # WHY: a half-populated settings table must not yield a (None, 0.9) range
    # that makes every comparison fail.
    assert get_confidence_thresholds(_FakeSettings(None, 0.90)) == DEFAULT_CONFIDENCE_THRESHOLDS


def test_falls_back_when_value_non_numeric():
    # WHY: a corrupt non-numeric stored value must degrade to the documented
    # default, not raise ValueError and 500 every caller — the float() cast is
    # inside the try/except for exactly this reason.
    assert get_confidence_thresholds(_FakeSettings("abc", 0.90)) == DEFAULT_CONFIDENCE_THRESHOLDS


def test_asset_service_thresholds_fallback_outside_app_context():
    # WHY: the reference implementation must degrade to the documented default
    # when there's no Flask context (e.g. the pipeline), and it must do so via
    # the shared accessor so it can never drift from the other surfaces.
    from src.modules.assets.services.asset_service import AssetService
    svc = AssetService.__new__(AssetService)  # bypass __init__; method uses only current_app
    assert svc._get_confidence_thresholds() == DEFAULT_CONFIDENCE_THRESHOLDS
