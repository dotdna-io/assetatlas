# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for AlertsService threshold value computation (mocked repo)."""
from unittest.mock import MagicMock

import pytest

from src.modules.alerts.service import AlertsService


@pytest.fixture
def repo():
    return MagicMock()


@pytest.fixture
def service(repo):
    return AlertsService(repository=repo)


class TestCurrentValue:
    def test_percent_unit_reports_share_of_target_universe(self, service, repo):
        # 734 EDR servers out of an 8,161-server universe → the readout must be
        # 9.0% (the share), NOT the raw match count 734 with a stray % sign.
        repo.count_target_universe.return_value = 8161
        rule = {
            'target_filters': [{'field': 'static:Category', 'op': 'equals', 'value': 'server'}],
            'threshold': {'condition': 'falls_below', 'value': 85, 'unit': 'percent'},
        }
        assert service._current_value(rule, 734) == 9.0

    def test_number_unit_reports_raw_count(self, service, repo):
        rule = {
            'target_filters': [],
            'threshold': {'condition': 'exceeds', 'value': 100, 'unit': 'number'},
        }
        assert service._current_value(rule, 734) == 734
        # Number-unit thresholds never need the universe query.
        repo.count_target_universe.assert_not_called()

    def test_percent_unit_with_empty_universe_is_zero(self, service, repo):
        repo.count_target_universe.return_value = 0
        rule = {
            'target_filters': [],
            'threshold': {'condition': 'falls_below', 'value': 85, 'unit': 'percent'},
        }
        assert service._current_value(rule, 0) == 0
