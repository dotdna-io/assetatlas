# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for IntegrationService.get_insights and get_timeline methods.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from src.modules.integrations.services.integration_service import IntegrationService


# ── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def svc():
    """Create service with mocked dependencies."""
    with patch.object(IntegrationService, '__init__', lambda self, **kw: None):
        s = IntegrationService()
        s.repository = MagicMock()
    return s


@pytest.fixture
def integration_id():
    return uuid.uuid4()


@pytest.fixture
def fake_integration():
    obj = MagicMock()
    obj.name = 'Jamf Pro'
    return obj


# ── get_insights ───────────────────────────────────────────────────────

class TestGetInsights:

    def test_returns_not_found(self, svc, integration_id):
        svc.repository.get_by_id.return_value = None
        result = svc.get_insights(integration_id)
        assert result['success'] is False
        assert result['error'] == 'NOT_FOUND'

    def test_returns_all_indicators(self, svc, integration_id, fake_integration):
        svc.repository.get_by_id.return_value = fake_integration
        svc.repository.get_stale_assets_count.return_value = {
            'stale_count': 5, 'total_count': 100
        }
        svc.repository.get_attribute_coverage.return_value = {
            'os': {'source_coverage': 80, 'global_coverage': 90, 'gap': 10},
            'ip_address': {'source_coverage': 60, 'global_coverage': 95, 'gap': 35},
        }
        svc.repository.get_attribute_conflicts.return_value = {
            'total_conflicts': 3, 'by_field': {'hostname': 2, 'os': 1}
        }
        svc.repository.get_category_coverage.return_value = [
            {'category': 'Server', 'source_count': 40, 'total_count': 50,
             'coverage_pct': 80, 'estimated_missing': 10},
            {'category': 'Desktop', 'source_count': 20, 'total_count': 30,
             'coverage_pct': 66.7, 'estimated_missing': 10},
        ]

        result = svc.get_insights(integration_id)

        assert result['integration_id'] == str(integration_id)
        assert result['integration_name'] == 'Jamf Pro'

        ind = result['indicators']
        assert ind['stale_assets']['count'] == 5
        assert ind['stale_assets']['total'] == 100
        assert ind['stale_assets']['percentage'] == 5.0
        assert ind['stale_assets']['threshold_days'] == 30

        assert ind['missing_attributes']['worst_gap_field'] == 'ip_address'
        assert ind['missing_attributes']['worst_gap_pct'] == 35
        assert 'os' in ind['missing_attributes']['fields']

        assert ind['conflicting_attributes']['total_conflicts'] == 3

        assert ind['missing_expected_assets']['total_estimated_missing'] == 20
        assert len(ind['missing_expected_assets']['categories']) == 2

    def test_handles_zero_total_count(self, svc, integration_id, fake_integration):
        svc.repository.get_by_id.return_value = fake_integration
        svc.repository.get_stale_assets_count.return_value = {
            'stale_count': 0, 'total_count': 0
        }
        svc.repository.get_attribute_coverage.return_value = {}
        svc.repository.get_attribute_conflicts.return_value = {
            'total_conflicts': 0, 'by_field': {}
        }
        svc.repository.get_category_coverage.return_value = []

        result = svc.get_insights(integration_id)

        assert result['indicators']['stale_assets']['percentage'] == 0.0

    def test_worst_gap_field_computed(self, svc, integration_id, fake_integration):
        svc.repository.get_by_id.return_value = fake_integration
        svc.repository.get_stale_assets_count.return_value = {
            'stale_count': 0, 'total_count': 10
        }
        svc.repository.get_attribute_coverage.return_value = {
            'hostname': {'source_coverage': 95, 'global_coverage': 98, 'gap': 3},
            'serial_number': {'source_coverage': 10, 'global_coverage': 90, 'gap': 80},
            'os': {'source_coverage': 70, 'global_coverage': 85, 'gap': 15},
        }
        svc.repository.get_attribute_conflicts.return_value = {
            'total_conflicts': 0, 'by_field': {}
        }
        svc.repository.get_category_coverage.return_value = []

        result = svc.get_insights(integration_id)
        assert result['indicators']['missing_attributes']['worst_gap_field'] == 'serial_number'
        assert result['indicators']['missing_attributes']['worst_gap_pct'] == 80

    def test_empty_coverage_gives_none_worst_gap(self, svc, integration_id, fake_integration):
        svc.repository.get_by_id.return_value = fake_integration
        svc.repository.get_stale_assets_count.return_value = {
            'stale_count': 0, 'total_count': 0
        }
        svc.repository.get_attribute_coverage.return_value = {}
        svc.repository.get_attribute_conflicts.return_value = {
            'total_conflicts': 0, 'by_field': {}
        }
        svc.repository.get_category_coverage.return_value = []

        result = svc.get_insights(integration_id)
        assert result['indicators']['missing_attributes']['worst_gap_field'] is None
        assert result['indicators']['missing_attributes']['worst_gap_pct'] == 0.0


# ── posture_scores ─────────────────────────────────────────────────────

class TestPostureScores:
    """Per-source posture scores (visibility, quality, completeness)."""

    def _wire(self, svc, fake_integration, *,
              stale=None, coverage=None, conflicts=None,
              categories=None, confidence=None):
        svc.repository.get_by_id.return_value = fake_integration
        svc.repository.get_stale_assets_count.return_value = (
            stale if stale is not None
            else {'stale_count': 0, 'total_count': 0}
        )
        svc.repository.get_attribute_coverage.return_value = coverage or {}
        svc.repository.get_attribute_conflicts.return_value = (
            conflicts if conflicts is not None
            else {'total_conflicts': 0, 'by_field': {}}
        )
        svc.repository.get_category_coverage.return_value = categories or []
        svc.repository.get_confidence_distribution.return_value = (
            confidence if confidence is not None
            else {'high': 0, 'medium': 0, 'low': 0}
        )

    def test_visibility_inverts_stale_pct(
        self, svc, integration_id, fake_integration
    ):
        # 20% stale → visibility = 80
        self._wire(svc, fake_integration,
                   stale={'stale_count': 20, 'total_count': 100})
        result = svc.get_insights(integration_id)
        assert result['posture_scores']['visibility'] == 80

    def test_visibility_zero_when_all_stale(
        self, svc, integration_id, fake_integration
    ):
        self._wire(svc, fake_integration,
                   stale={'stale_count': 100, 'total_count': 100})
        result = svc.get_insights(integration_id)
        assert result['posture_scores']['visibility'] == 0

    def test_visibility_full_when_none_stale(
        self, svc, integration_id, fake_integration
    ):
        self._wire(svc, fake_integration,
                   stale={'stale_count': 0, 'total_count': 100})
        result = svc.get_insights(integration_id)
        assert result['posture_scores']['visibility'] == 100

    def test_quality_high_only_is_100(
        self, svc, integration_id, fake_integration
    ):
        self._wire(svc, fake_integration,
                   stale={'stale_count': 0, 'total_count': 10},
                   confidence={'high': 10, 'medium': 0, 'low': 0})
        result = svc.get_insights(integration_id)
        assert result['posture_scores']['quality'] == 100

    def test_quality_low_only_is_0(
        self, svc, integration_id, fake_integration
    ):
        self._wire(svc, fake_integration,
                   stale={'stale_count': 0, 'total_count': 10},
                   confidence={'high': 0, 'medium': 0, 'low': 10})
        result = svc.get_insights(integration_id)
        assert result['posture_scores']['quality'] == 0

    def test_quality_mixed_high_medium(
        self, svc, integration_id, fake_integration
    ):
        # 5 high (100) + 5 medium (50) → (500 + 250) / 10 = 75
        self._wire(svc, fake_integration,
                   stale={'stale_count': 0, 'total_count': 10},
                   confidence={'high': 5, 'medium': 5, 'low': 0})
        result = svc.get_insights(integration_id)
        assert result['posture_scores']['quality'] == 75

    def test_quality_zero_when_no_assets_with_confidence(
        self, svc, integration_id, fake_integration
    ):
        self._wire(svc, fake_integration,
                   stale={'stale_count': 0, 'total_count': 0},
                   confidence={'high': 0, 'medium': 0, 'low': 0})
        result = svc.get_insights(integration_id)
        assert result['posture_scores']['quality'] == 0

    def test_completeness_averages_reported_categories(
        self, svc, integration_id, fake_integration
    ):
        # 80% + 60% over two reported categories → mean 70
        cats = [
            {'category': 'server', 'source_count': 80,
             'total_count': 100, 'coverage_pct': 80,
             'estimated_missing': 20},
            {'category': 'workstation', 'source_count': 60,
             'total_count': 100, 'coverage_pct': 60,
             'estimated_missing': 40},
        ]
        self._wire(svc, fake_integration,
                   stale={'stale_count': 0, 'total_count': 200},
                   categories=cats)
        result = svc.get_insights(integration_id)
        assert result['posture_scores']['completeness'] == 70

    def test_completeness_ignores_unreported_categories(
        self, svc, integration_id, fake_integration
    ):
        # Source covers only 'server' at 80%; OT/IoT/etc not reported.
        # Completeness must NOT penalise the source for those.
        cats = [
            {'category': 'server', 'source_count': 80,
             'total_count': 100, 'coverage_pct': 80,
             'estimated_missing': 20},
        ]
        self._wire(svc, fake_integration,
                   stale={'stale_count': 0, 'total_count': 100},
                   categories=cats)
        result = svc.get_insights(integration_id)
        assert result['posture_scores']['completeness'] == 80

    def test_completeness_zero_when_no_categories_reported(
        self, svc, integration_id, fake_integration
    ):
        self._wire(svc, fake_integration,
                   stale={'stale_count': 0, 'total_count': 0},
                   categories=[])
        result = svc.get_insights(integration_id)
        assert result['posture_scores']['completeness'] == 0

    def test_zero_asset_source_returns_all_zero_scores(
        self, svc, integration_id, fake_integration
    ):
        # A source that reports no assets should not get a perfect
        # visibility score by accident — vacuous "no stale assets" must
        # not become "100% visible".
        self._wire(svc, fake_integration,
                   stale={'stale_count': 0, 'total_count': 0},
                   confidence={'high': 0, 'medium': 0, 'low': 0},
                   categories=[])
        result = svc.get_insights(integration_id)
        assert result['posture_scores'] == {
            'visibility': 0,
            'quality': 0,
            'completeness': 0,
        }


# ── get_timeline ───────────────────────────────────────────────────────

class TestGetTimeline:

    def test_returns_not_found(self, svc, integration_id):
        svc.repository.get_by_id.return_value = None
        result = svc.get_timeline(integration_id)
        assert result['success'] is False
        assert result['error'] == 'NOT_FOUND'

    def test_returns_formatted_data(self, svc, integration_id, fake_integration):
        svc.repository.get_by_id.return_value = fake_integration
        timeline_data = [
            {'label': '2026-02-28', 'added': 5, 'removed': 2},
            {'label': '2026-03-01', 'added': 3, 'removed': 0},
        ]
        svc.repository.get_asset_timeline.return_value = timeline_data

        result = svc.get_timeline(integration_id, days=7)

        assert result['integration_id'] == str(integration_id)
        assert result['range'] == '7d'
        assert result['granularity'] == 'daily'
        assert result['data'] == timeline_data
        svc.repository.get_asset_timeline.assert_called_once_with(
            'Jamf Pro', days=7
        )

    def test_default_days(self, svc, integration_id, fake_integration):
        svc.repository.get_by_id.return_value = fake_integration
        svc.repository.get_asset_timeline.return_value = []

        result = svc.get_timeline(integration_id)

        assert result['range'] == '30d'
        svc.repository.get_asset_timeline.assert_called_once_with(
            'Jamf Pro', days=30
        )
