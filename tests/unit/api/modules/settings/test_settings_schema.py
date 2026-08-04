# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the application settings schema."""
from src.api.modules.settings.settings_schema import APPLICATION_SETTINGS


def test_pipeline_max_expected_duration_setting_registered():
    setting = APPLICATION_SETTINGS["asset_generation"]["pipeline_max_expected_duration_minutes"]
    assert setting.type == "integer"
    assert setting.default == 240
    assert setting.min == 5
    assert setting.max == 1440
    assert "max expected duration" in setting.description.lower()
