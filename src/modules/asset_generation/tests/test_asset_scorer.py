# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for AssetScorer V2

Tests the confidence scoring using gates and gradients approach.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from src.modules.asset_generation.domain.asset_scorer import AssetScorer


class TestAssetScorerInitialization:
    """Test AssetScorer V2 initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default parameters."""
        scorer = AssetScorer()

        assert scorer is not None
        assert hasattr(scorer, 'source_reliability_scores')
        assert scorer.enable_recency is True
        assert scorer.enable_identifier_cap is True
        assert scorer.enable_consistency is True

    def test_init_with_custom_source_scores(self):
        """Test initialization with custom source reliability scores."""
        custom_scores = {
            'mdm': 0.99,
            'virtualization': 0.80,
        }

        scorer = AssetScorer(source_reliability_scores=custom_scores)

        assert scorer.source_reliability_scores['mdm'] == 0.99
        assert scorer.source_reliability_scores['virtualization'] == 0.80

    def test_default_includes_network_configuration_management(self):
        """NCM category exists in defaults at 0.80 (between MDM 0.85 and IAM 0.75)."""
        scorer = AssetScorer()

        assert scorer.source_reliability_scores['network configuration management'] == 0.80
        assert (
            scorer.source_reliability_scores['mobile device management']
            > scorer.source_reliability_scores['network configuration management']
            > scorer.source_reliability_scores['identity and access management']
        )

    def test_default_includes_telemetry_derived(self):
        """Telemetry-derived sits at observability-platform fidelity (0.70).

        SIEM/log-scraped inventory is deliberately mid-trust: the same tier as
        an observability platform, not an authoritative system-of-record.
        """
        scorer = AssetScorer()

        assert scorer.source_reliability_scores['telemetry derived'] == 0.70
        assert (
            scorer.source_reliability_scores['telemetry derived']
            == scorer.source_reliability_scores['observability platform']
        )

    def test_init_with_gates_disabled(self):
        """Test initialization with gates disabled."""
        scorer = AssetScorer(
            enable_recency_penalty=False,
            enable_identifier_cap=False,
            enable_consistency_penalty=False
        )

        assert scorer.enable_recency is False
        assert scorer.enable_identifier_cap is False
        assert scorer.enable_consistency is False


class TestConfidenceScoring:
    """Test confidence scoring."""

    def test_calculate_confidence_basic(self):
        """Test basic confidence calculation with minimal data."""
        scorer = AssetScorer(
            enable_recency_penalty=False,
            enable_identifier_cap=False,
            enable_consistency_penalty=False
        )

        assets_df = pd.DataFrame([{
            'id': 'asset-001',
            'hostname': ['server01'],
            'sn': ['SN123'],
            'data_source': ['source1'],
            'data_source_category': ['endpoint security'],
        }])

        providers_df = pd.DataFrame([{
            'id': 'asset-001',
            'hostname': 'server01',
            'data_source': 'source1',
        }])

        result = scorer.calculate_confidence(assets_df, providers_df)

        assert 'confidence' in result.columns
        assert result['confidence'].iloc[0] > 0.0
        assert result['confidence'].iloc[0] <= 1.0


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_dataframe_raises_on_confidence_access(self):
        """Test that empty DataFrame raises KeyError because confidence column is never created.

        The calculate_confidence method iterates over rows to set the confidence column.
        With an empty DataFrame, the loop body never executes, so the column is not created.
        The subsequent access to assets_df['confidence'] for statistics then raises KeyError.
        """
        scorer = AssetScorer()

        assets_df = pd.DataFrame()
        providers_df = pd.DataFrame()

        result = scorer.calculate_confidence(assets_df, providers_df)
        assert len(result) == 0
        assert 'confidence' in result.columns

    def test_get_scoring_stats(self):
        """Test that get_scoring_stats returns expected structure."""
        scorer = AssetScorer()

        stats = scorer.get_scoring_stats()
        assert stats['version'] == 2
        assert 'source_reliability_scores' in stats
        assert 'weights' in stats
        assert stats['weights']['standard']['source_reliability'] == 0.40
        assert stats['weights']['standard']['source_count'] == 0.30
        assert stats['weights']['standard']['data_completeness'] == 0.30


class TestAvailableSourcesP75:
    """Test that available_sources uses p75 per category."""

    def test_p75_differentiates_categories(self):
        """p75 should produce different available_sources for categories with different source distributions."""
        scorer = AssetScorer()
        rows = []
        # Workstations: mix of 1-source and multi-source
        for i in range(10):
            rows.append({
                'id': f'ws-{i}',
                'data_source': ['Src1'] if i < 5 else ['Src1', 'Src2', 'Src3', 'Src4', 'Src5', 'Src6'],
                'category': 'workstation',
            })
        # OT: mostly single-source
        for i in range(10):
            rows.append({
                'id': f'ot-{i}',
                'data_source': ['Armis'] if i < 8 else ['Armis', 'ServiceNow'],
                'category': 'ot',
            })

        assets_df = pd.DataFrame(rows)
        stats = scorer._compute_environment_stats(assets_df)

        ws_available = stats['p75_sources_per_category'].get('workstation', 2)
        ot_available = stats['p75_sources_per_category'].get('ot', 2)

        # Workstation p75 should be higher than OT p75
        assert ws_available > ot_available, f"workstation={ws_available} should be > ot={ot_available}"
        # Workstation p75 should reflect multi-source coverage
        assert ws_available >= 4, f"workstation p75={ws_available} should be >= 4"

    def test_source_count_score_differentiates_with_higher_available(self):
        """With higher available_sources, 2-source and 8-source should have different scores."""
        scorer = AssetScorer()
        # With available_sources=6 (a realistic p75 for workstations)
        row_2src = pd.Series({'data_source': ['A', 'B']})
        row_8src = pd.Series({'data_source': ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']})
        score_2 = scorer._calculate_source_count_score(row_2src, available_sources=6)
        score_8 = scorer._calculate_source_count_score(row_8src, available_sources=6)
        assert score_8 > score_2, f"8-source ({score_8}) should score higher than 2-source ({score_2})"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
