# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from unittest.mock import MagicMock
from src.modules.assets.services.reclassify_service import ReclassifyService


# Real UUIDs, spelled the way psycopg2 renders a `uuid` column. The service
# canonicalises every id it is handed and rejects anything that is not a UUID
# (#1275), and an `a1`-style stand-in could never have survived the `::uuid`
# casts these ids are bound into anyway.
A1 = '11111111-1111-4111-8111-111111111111'
A2 = '22222222-2222-4222-8222-222222222222'


def _svc():
    svc = ReclassifyService.__new__(ReclassifyService)   # skip catalog load
    svc.db = MagicMock()
    svc.override_repo = MagicMock()
    svc._catalog = [{"name": "network", "subcategories": ["switch"]}]
    svc.custom_subcategory_repo = MagicMock()
    svc.custom_subcategory_repo.get_for_category.return_value = []
    # current categories for chronicle old-values; A1 carries an active override
    # over a full pipeline prediction snapshot (workstation/desktop)
    svc.db.execute_query.return_value = [
        {'id': A1, 'category': 'server', 'subcategory': 'linux',
         'category_predicted': 'workstation', 'subcategory_predicted': 'desktop'},
    ]
    # Which assets carry an override comes from category_overrides, not from
    # assets.category_is_overridden — the flag is reset by every pipeline run
    # and was never set by the single-asset path (#1270).
    svc.override_repo.get_active_override_ids.return_value = {A1}
    return svc


def test_reclassify_bulk_validates_then_writes_in_scope():
    svc = _svc()
    out = svc.reclassify_bulk([A1], 'network', 'switch', 'admin')
    assert out == {"updated": 1}
    svc.override_repo.save_overrides_bulk.assert_called_once()
    # Every scope opened was closed cleanly (no error): _bulk_scope's, and the
    # refresh hook's outside_request_scope pair after it (#1428).
    assert svc.db.conn_manager.begin_scope.call_count == \
        svc.db.conn_manager.end_scope.call_count
    for c in svc.db.conn_manager.end_scope.call_args_list:
        assert c.kwargs.get('error') is None
    assert svc.db.bulk_write_chronicle_events.called


def test_reclassify_bulk_rejects_invalid_category_before_any_write():
    svc = _svc()
    with pytest.raises(ValueError):
        svc.reclassify_bulk([A1], 'bogus', 'switch', 'admin')
    svc.override_repo.save_overrides_bulk.assert_not_called()
    svc.db.conn_manager.begin_scope.assert_not_called()


def test_reclassify_bulk_rolls_back_scope_on_failure():
    svc = _svc()
    svc.override_repo.save_overrides_bulk.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        svc.reclassify_bulk([A1], 'network', 'switch', 'admin')
    # end_scope called WITH the error so the scope rolls back (all-or-nothing)
    err = svc.db.conn_manager.end_scope.call_args.kwargs.get('error')
    assert isinstance(err, RuntimeError)


def test_reclassify_bulk_empty_is_noop():
    svc = _svc()
    assert svc.reclassify_bulk([], 'network', 'switch', 'admin') == {"updated": 0}
    svc.db.conn_manager.begin_scope.assert_not_called()


def test_clear_overrides_bulk_deactivates_and_resets():
    svc = _svc()
    out = svc.clear_overrides_bulk([A1], 'admin')
    assert out == {"cleared": 1}
    svc.override_repo.deactivate_overrides_bulk.assert_called_once_with([A1])
    # Balanced and clean — see test_reclassify_bulk_validates_then_writes_in_scope.
    assert svc.db.conn_manager.begin_scope.call_count == \
        svc.db.conn_manager.end_scope.call_count
    for c in svc.db.conn_manager.end_scope.call_args_list:
        assert c.kwargs.get('error') is None
    assert svc.db.bulk_write_chronicle_events.called


def test_clear_overrides_bulk_chronicle_records_predicted_category():
    # The audit trail must reflect what the live row is actually reset to — the
    # predicted pair — not None, otherwise the chronicle lies. Subcategory used
    # to be recorded (and written) as NULL because no snapshot column existed;
    # migration 143 added it, so both fields come back now (#1154).
    svc = _svc()
    svc.clear_overrides_bulk([A1], 'admin')
    events = svc.db.bulk_write_chronicle_events.call_args.args[0]
    changes = events[0]["attribute_changes"]
    assert changes["category"]["new"] == 'workstation'  # the mocked predicted value
    assert changes["category"]["old"] == 'server'
    assert changes["subcategory"]["new"] == 'desktop'
    assert changes["subcategory"]["old"] == 'linux'


def test_clear_overrides_bulk_rolls_back_scope_on_failure():
    svc = _svc()
    svc.override_repo.deactivate_overrides_bulk.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        svc.clear_overrides_bulk([A1], 'admin')
    # end_scope called WITH the error so the scope rolls back (all-or-nothing)
    err = svc.db.conn_manager.end_scope.call_args.kwargs.get('error')
    assert isinstance(err, RuntimeError)


def test_clear_overrides_bulk_empty_is_noop():
    svc = _svc()
    assert svc.clear_overrides_bulk([], 'admin') == {"cleared": 0}
    svc.db.conn_manager.begin_scope.assert_not_called()


def test_clear_overrides_bulk_skips_non_overridden_assets():
    # A filter set can include assets that were never overridden. Clearing must
    # NOT touch them — resetting would NULL their pipeline-derived subcategory.
    svc = _svc()
    svc.db.execute_query.return_value = [
        {'id': A1, 'category': 'network', 'subcategory': 'switch',
         'category_predicted': 'server'},
        {'id': A2, 'category': 'server', 'subcategory': 'linux',
         'category_predicted': 'server'},
    ]
    svc.override_repo.get_active_override_ids.return_value = {A1}
    svc._reset_assets_bulk = MagicMock()

    out = svc.clear_overrides_bulk([A1, A2], 'admin')

    # Only the actually-overridden asset is reported, deactivated, and reset.
    assert out == {"cleared": 1}
    svc.override_repo.deactivate_overrides_bulk.assert_called_once_with([A1])
    svc._reset_assets_bulk.assert_called_once()
    assert svc._reset_assets_bulk.call_args.args[0] == [A1]
    events = svc.db.bulk_write_chronicle_events.call_args.args[0]
    assert [e['asset_id'] for e in events] == [A1]


def test_clear_overrides_bulk_no_overridden_assets_clears_nothing():
    # All matching assets are non-overridden → nothing deactivated/reset.
    svc = _svc()
    svc.db.execute_query.return_value = [
        {'id': A1, 'category': 'server', 'subcategory': 'linux',
         'category_predicted': 'server'},
    ]
    svc.override_repo.get_active_override_ids.return_value = set()
    out = svc.clear_overrides_bulk([A1], 'admin')
    assert out == {"cleared": 0}
    svc.override_repo.deactivate_overrides_bulk.assert_not_called()
    svc.db.bulk_write_chronicle_events.assert_not_called()
