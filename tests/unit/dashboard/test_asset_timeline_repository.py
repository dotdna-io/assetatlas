# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for DashboardRepository.get_asset_timeline (mocked db)."""
from datetime import date
from unittest.mock import MagicMock

from src.modules.dashboard.repositories.dashboard_repository import DashboardRepository


def _repo():
    return DashboardRepository(MagicMock())


def test_timeline_splits_new_returned_removed_and_zero_fills():
    repo = _repo()
    # First call: day series (label + day). Second call: per-(asset,day) state rows.
    repo.db.execute_query.side_effect = [
        [
            {'label': 'Jun 10', 'day': date(2026, 6, 10)},
            {'label': 'Jun 11', 'day': date(2026, 6, 11)},
        ],
        [
            {'asset_id': 'a', 'day': date(2026, 6, 10), 'end_enter': True, 'last_evt': 'asset_created'},
            {'asset_id': 'b', 'day': date(2026, 6, 10), 'end_enter': True, 'last_evt': 'asset_readded'},
            {'asset_id': 'c', 'day': date(2026, 6, 9),  'end_enter': True, 'last_evt': 'asset_created'},
            {'asset_id': 'c', 'day': date(2026, 6, 11), 'end_enter': False, 'last_evt': 'asset_removed'},
        ],
    ]
    out = repo.get_asset_timeline(days=2)
    assert out == [
        {'label': 'Jun 10', 'added': 1, 'returned': 1, 'removed': 0},
        {'label': 'Jun 11', 'added': 0, 'returned': 0, 'removed': 1},
    ]
    # The state query must exclude pipeline-merge removals (NULL-safe, so a
    # removal with NULL causation is still kept as a real departure) and use
    # named params.
    state_sql = repo.db.execute_query.call_args_list[1][0][0]
    assert "IS DISTINCT FROM 'pipeline_merge'" in state_sql
    assert "%(days)s" in state_sql


def test_timeline_returns_empty_on_error():
    repo = _repo()
    repo.db.execute_query.side_effect = RuntimeError('boom')
    assert repo.get_asset_timeline(days=30) == []
