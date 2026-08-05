# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ScoringStage loading custom reliability scores from settings."""

from unittest.mock import Mock, patch, MagicMock

from src.modules.asset_generation.pipeline.stages.s14_scoring import ScoringStage
from src.modules.asset_generation.domain.asset_scorer import AssetScorer


class TestScoringStageCustomScores:
    """Test that ScoringStage reads scores from settings_manager.

    s14 reaches settings through a lazy ``flask.current_app`` import guarded
    by ``has_app_context()`` (same pattern as s20/s22), so the seam to patch
    is on the ``flask`` module, not a stage-module attribute.
    """

    def test_custom_scores_applied_to_scorer(self):
        """When settings has custom scores, scorer uses them."""
        custom_scores = {'endpoint security': 0.80, 'other': 0.30}

        mock_service = Mock()
        mock_service.scorer = AssetScorer()
        stage = ScoringStage(mock_service)

        mock_app = MagicMock()
        mock_app.settings_manager.get.return_value = custom_scores

        with patch('flask.has_app_context', return_value=True), \
                patch('flask.current_app', mock_app):
            stage._load_custom_scores()

        assert mock_service.scorer.source_reliability_scores['endpoint security'] == 0.80
        assert mock_service.scorer.source_reliability_scores['other'] == 0.30

    def test_no_custom_scores_keeps_defaults(self):
        """When settings returns None, scorer keeps DEFAULT_SOURCE_SCORES."""
        mock_service = Mock()
        default_scores = dict(AssetScorer.DEFAULT_SOURCE_SCORES)
        mock_service.scorer = AssetScorer()
        stage = ScoringStage(mock_service)

        mock_app = MagicMock()
        mock_app.settings_manager.get.return_value = None

        with patch('flask.has_app_context', return_value=True), \
                patch('flask.current_app', mock_app):
            stage._load_custom_scores()

        assert mock_service.scorer.source_reliability_scores == default_scores

    def test_settings_error_keeps_defaults(self):
        """When settings_manager raises, scorer keeps defaults."""
        mock_service = Mock()
        mock_service.scorer = AssetScorer()
        stage = ScoringStage(mock_service)

        mock_app = MagicMock()
        mock_app.settings_manager.get.side_effect = Exception("DB down")

        with patch('flask.has_app_context', return_value=True), \
                patch('flask.current_app', mock_app):
            stage._load_custom_scores()

        assert mock_service.scorer.source_reliability_scores == AssetScorer.DEFAULT_SOURCE_SCORES
