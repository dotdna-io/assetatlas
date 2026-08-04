# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for JSON type in settings schema."""

from src.api.modules.settings.settings_schema import (
    SettingDefinition,
    get_setting_definition,
    validate_setting_value,
)


class TestJsonSettingDefinition:
    """Test SettingDefinition supports json type."""

    def test_json_type_validates_dict(self):
        sd = SettingDefinition(
            setting_type='json',
            default={"a": 1},
            description='Test JSON setting',
        )
        assert sd.validate({"a": 1}) is True

    def test_json_type_rejects_string(self):
        sd = SettingDefinition(
            setting_type='json',
            default={"a": 1},
            description='Test JSON setting',
        )
        assert sd.validate("not a dict") is False

    def test_json_type_rejects_list(self):
        sd = SettingDefinition(
            setting_type='json',
            default={"a": 1},
            description='Test JSON setting',
        )
        assert sd.validate([1, 2, 3]) is False


class TestSourceReliabilityScoresSchemaDef:
    """Test the source_reliability_scores setting exists and is correct."""

    def test_setting_exists_in_asset_generation(self):
        defn = get_setting_definition(
            'asset_generation', 'source_reliability_scores'
        )
        assert defn.type == 'json'

    def test_default_matches_scorer_defaults(self):
        """Settings-schema default and AssetScorer.DEFAULT_SOURCE_SCORES must agree.

        Both hold the same canonical category list; drift between them is a bug.
        """
        from src.modules.asset_generation.domain.asset_scorer import AssetScorer

        defn = get_setting_definition(
            'asset_generation', 'source_reliability_scores'
        )
        assert defn.default == AssetScorer.DEFAULT_SOURCE_SCORES

    def test_default_scores_in_range(self):
        defn = get_setting_definition(
            'asset_generation', 'source_reliability_scores'
        )
        for cat, score in defn.default.items():
            assert 0.0 <= score <= 1.0, f"{cat} score {score} out of range"

    def test_validates_valid_scores(self):
        assert validate_setting_value(
            'asset_generation',
            'source_reliability_scores',
            {'endpoint security': 0.90, 'other': 0.50},
        ) is True

    def test_rejects_score_out_of_range(self):
        assert validate_setting_value(
            'asset_generation',
            'source_reliability_scores',
            {'endpoint security': 1.5},
        ) is False

    def test_rejects_non_dict(self):
        assert validate_setting_value(
            'asset_generation',
            'source_reliability_scores',
            "not a dict",
        ) is False
