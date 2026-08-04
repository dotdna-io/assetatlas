# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for legacy alias removal from AssetScorer."""

from src.modules.asset_generation.domain.asset_scorer import AssetScorer


class TestNoLegacyAliases:
    """Verify legacy aliases are removed from DEFAULT_SOURCE_SCORES."""

    LEGACY_KEYS = [
        'mdm', 'virtualization', 'identity management', 'manual',
        'monitoring and analytics', 'it service management',
        'network infrastructure', 'ot/iot security',
        'network discovery', 'asset management',
    ]

    def test_no_legacy_aliases_in_defaults(self):
        for key in self.LEGACY_KEYS:
            assert key not in AssetScorer.DEFAULT_SOURCE_SCORES, (
                f"Legacy alias '{key}' should be removed"
            )

    def test_unknown_category_falls_back_to_0_5(self):
        scorer = AssetScorer()
        score = scorer.source_reliability_scores.get('nonexistent_category', 0.5)
        assert score == 0.5
