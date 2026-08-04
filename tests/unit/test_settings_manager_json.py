# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for JSON type support in SettingsManager."""

from src.api.modules.settings.settings_manager import SettingsManager


class TestParseValueJson:
    """Test _parse_value handles json type."""

    def test_parse_json_dict(self):
        sm = SettingsManager.__new__(SettingsManager)
        result = sm._parse_value('{"a": 1, "b": 2}', 'json')
        assert result == {"a": 1, "b": 2}

    def test_parse_json_returns_dict_not_string(self):
        sm = SettingsManager.__new__(SettingsManager)
        result = sm._parse_value('{"key": "value"}', 'json')
        assert isinstance(result, dict)

    def test_parse_json_invalid_falls_back(self):
        sm = SettingsManager.__new__(SettingsManager)
        result = sm._parse_value('not json', 'json')
        assert result == 'not json'


class TestSerializeValueJson:
    """Test _serialize_value handles json type."""

    def test_serialize_json_dict(self):
        sm = SettingsManager.__new__(SettingsManager)
        result = sm._serialize_value({"a": 1}, 'json')
        assert result == '{"a": 1}'

    def test_serialize_json_roundtrips(self):
        sm = SettingsManager.__new__(SettingsManager)
        original = {"endpoint security": 0.95, "other": 0.50}
        serialized = sm._serialize_value(original, 'json')
        parsed = sm._parse_value(serialized, 'json')
        assert parsed == original
