# SPDX-License-Identifier: AGPL-3.0-or-later
from unittest.mock import MagicMock
from src.modules.assets.repositories.asset_repository import AssetRepository


def _repo():
    db = MagicMock()
    resolver = MagicMock()
    return AssetRepository(db, resolver), db


def test_get_matching_asset_ids_returns_str_ids_and_applies_limit():
    repo, db = _repo()
    db.execute_query.return_value = [{'id': 'a1'}, {'id': 'a2'}]

    ids = repo.get_matching_asset_ids(search='printer', limit=1001)

    assert ids == ['a1', 'a2']
    sql, params = db.execute_query.call_args[0][0], db.execute_query.call_args[0][1]
    assert 'SELECT id' in sql
    assert 'LIMIT %(limit)s' in sql
    assert params['limit'] == 1001


def test_get_matching_asset_ids_no_filters_selects_all():
    repo, db = _repo()
    db.execute_query.return_value = []
    repo.get_matching_asset_ids(limit=5)
    sql = db.execute_query.call_args[0][0]
    assert 'WHERE' not in sql  # empty where_sql when nothing constrains


def test_get_matching_asset_ids_alert_scope_narrows_not_selects_all():
    # The scoped counterpart to the test above (#1011): with no filters but an
    # alert scope, the resolver must NOT bottom out at "SELECT id FROM assets".
    # An alert scope constrains the set, so the WHERE clause carries the alert
    # membership test — otherwise a bulk action from an alert drilldown resolves
    # every asset while the count beside it reads the alert's size.
    repo, db = _repo()
    db.execute_query.return_value = []
    repo.get_matching_asset_ids(alert_id='948e3f69-818b-4649-8f77-362048686475', limit=5)
    sql, params = db.execute_query.call_args[0][0], db.execute_query.call_args[0][1]
    assert 'WHERE' in sql
    assert 'assets.id = ANY(al.asset_ids)' in sql
    assert '%(alert_id)s::uuid' in sql
    assert params['alert_id'] == '948e3f69-818b-4649-8f77-362048686475'


from unittest.mock import patch
from src.modules.assets.services.asset_service import AssetService


def test_service_forwards_confidence_thresholds():
    repo = MagicMock()
    repo.get_matching_asset_ids.return_value = ['a1']
    svc = AssetService.__new__(AssetService)   # bypass __init__ wiring
    svc.repo = repo
    with patch.object(svc, '_get_confidence_thresholds', return_value=(0.5, 0.8)):
        ids = svc.get_matching_asset_ids(filters=[{'field_id': 'x'}], limit=1001)
    assert ids == ['a1']
    kwargs = repo.get_matching_asset_ids.call_args.kwargs
    assert kwargs['confidence_thresholds'] == (0.5, 0.8)
    assert kwargs['limit'] == 1001


def test_service_forwards_alert_id():
    # The bulk path (_resolve_bulk_ids) passes alert_id through this service seam
    # (#1011). Dropping it here silently un-scopes the target set.
    repo = MagicMock()
    repo.get_matching_asset_ids.return_value = ['a1']
    svc = AssetService.__new__(AssetService)   # bypass __init__ wiring
    svc.repo = repo
    with patch.object(svc, '_get_confidence_thresholds', return_value=(0.5, 0.8)):
        svc.get_matching_asset_ids(
            filters=[], alert_id='948e3f69-818b-4649-8f77-362048686475', limit=1001,
        )
    kwargs = repo.get_matching_asset_ids.call_args.kwargs
    assert kwargs['alert_id'] == '948e3f69-818b-4649-8f77-362048686475'
