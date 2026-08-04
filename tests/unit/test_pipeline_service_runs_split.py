# SPDX-License-Identifier: AGPL-3.0-or-later
"""get_runs must split CREATED into new (assets_created) + returned (assets_readded)."""
from unittest.mock import MagicMock

from src.modules.pipeline.pipeline_service import PipelineService


def _service_with_runs(stats):
    svc = PipelineService.__new__(PipelineService)
    db = MagicMock()
    # COUNT query, then page query
    db.execute_query.side_effect = [
        [{'total': 1}],
        [{
            'id': '00000000-0000-0000-0000-000000000001',
            'triggered_by': 'schedule', 'status': 'completed',
            'started_at': None, 'completed_at': None, 'duration_seconds': None,
            'stats': stats, 'error_message': None, 'created_at': None,
        }],
    ]
    svc._db = db
    return svc


def test_new_run_splits_created_and_readded_from_chronicle_events():
    svc = _service_with_runs({
        'total_assets': 100,
        'total_assets_created': 1403,           # combined (untouched)
        'total_assets_updated': 50,
        'removed_assets': 161,
        'chronicle_events': {'asset_created': 319, 'asset_readded': 1084,
                             'asset_removed': 161, 'attribute_updated': 50},
    })
    item = svc.get_runs(page=1, per_page=20)['runs'][0]
    assert item['assets_created'] == 319
    assert item['assets_readded'] == 1084
    assert item['assets_removed'] == 161


def test_legacy_run_without_chronicle_events_falls_back():
    svc = _service_with_runs({
        'total_assets': 100, 'total_assets_created': 7,
        'total_assets_updated': 3, 'removed_assets': 2,
    })
    item = svc.get_runs(page=1, per_page=20)['runs'][0]
    assert item['assets_created'] == 7      # combined fallback
    assert item['assets_readded'] == 0


def test_run_exposes_assets_merged_and_net_removed():
    # removed_assets is already net (genuine only); merged_assets is the
    # pipeline_merge consolidation count.
    svc = _service_with_runs({
        'total_assets': 100, 'removed_assets': 4, 'merged_assets': 1,
    })
    item = svc.get_runs(page=1, per_page=20)['runs'][0]
    assert item['assets_removed'] == 4
    assert item['assets_merged'] == 1


def test_legacy_run_without_merged_defaults_to_zero():
    svc = _service_with_runs({'total_assets': 1, 'removed_assets': 2})
    item = svc.get_runs(page=1, per_page=20)['runs'][0]
    assert item['assets_merged'] == 0
