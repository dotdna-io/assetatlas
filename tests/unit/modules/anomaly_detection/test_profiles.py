# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.modules.anomaly_detection.profiles import ProfileStore


def test_bootstrap_seeds_manufacturer_and_os_when_empty():
    db = MagicMock()
    db.execute_query.return_value = [{'n': 0}]
    store = ProfileStore(db)
    assert store.bootstrap_if_empty() is True
    calls = [c.args[0] for c in db.execute_query.call_args_list]
    assert any('INSERT INTO anomaly_profiles' in s for s in calls)
    assert any('fleet_manufacturer' in s for s in calls)
    assert any('fleet_os' in s for s in calls)
    # seeds from both the live table and the history table
    assert any('FROM assets,' in s or 'FROM assets ' in s for s in calls)
    assert any('assets_history' in s for s in calls)


def test_bootstrap_skips_when_already_populated():
    db = MagicMock()
    db.execute_query.return_value = [{'n': 1234}]
    store = ProfileStore(db)
    assert store.bootstrap_if_empty() is False
    calls = [c.args[0] for c in db.execute_query.call_args_list]
    assert not any('INSERT' in s for s in calls)


def test_known_values_lowercases_and_returns_set():
    db = MagicMock()
    db.execute_query.return_value = [{'value': 'dell'}, {'value': 'HP'}, {'value': 'dell'}]
    store = ProfileStore(db)
    vals = store.known_values('fleet_manufacturer')
    assert vals == {'dell', 'hp'}
    assert db.execute_query.call_args.args[1]['kind'] == 'fleet_manufacturer'


def test_upsert_values_uses_on_conflict_and_normalizes():
    db = MagicMock()
    db.execute_query.return_value = []
    store = ProfileStore(db)
    seen = datetime(2026, 6, 1, tzinfo=timezone.utc)
    store.upsert_values('fleet_manufacturer', ['Dell ', 'DELL', 'weirdcorp'], seen)
    sql, params = db.execute_query.call_args.args
    assert 'ON CONFLICT (profile_kind, key, value) DO UPDATE' in sql
    assert params['kind'] == 'fleet_manufacturer'
    assert params['seen'] == seen
    # 'Dell ' and 'DELL' collapse to a single normalized 'dell'
    assert params['values'] == ['dell', 'weirdcorp']


def test_upsert_values_noop_on_empty():
    db = MagicMock()
    store = ProfileStore(db)
    store.upsert_values('fleet_os', [], datetime(2026, 6, 1, tzinfo=timezone.utc))
    db.execute_query.assert_not_called()


# --------------------------- B3 stable bindings ---------------------------

def test_get_stable_bindings_gates_and_shapes():
    db = MagicMock()
    db.execute_query.return_value = [
        {'key': 'host1', 'value': 'SN-A', 'seen_count': 20, 'age_days': 43}]
    store = ProfileStore(db)
    out = store.get_stable_bindings('hostname_sn', 14, 3)
    assert out == {'host1': {'value': 'SN-A', 'seen_count': 20, 'age_days': 43}}
    sql, params = db.execute_query.call_args.args
    assert 'DISTINCT ON (key)' in sql
    assert 'seen_count >= %(min_seen)s' in sql
    assert "last_seen - first_seen >= (%(days)s || ' days')::interval" in sql
    assert params == {'kind': 'hostname_sn', 'min_seen': 3, 'days': 14}


def test_upsert_bindings_zips_arrays_and_dedups_pairs():
    db = MagicMock()
    store = ProfileStore(db)
    seen = datetime(2026, 6, 1, tzinfo=timezone.utc)
    store.upsert_bindings(
        'ip_mac', [('10.1.2.3', 'aa'), ('10.1.2.3', 'aa'), ('10.1.2.4', 'bb')], seen)
    sql, params = db.execute_query.call_args.args
    assert 'unnest(%(keys)s::text[], %(values)s::text[])' in sql
    assert 'ON CONFLICT (profile_kind, key, value) DO UPDATE' in sql
    assert params['keys'] == ['10.1.2.3', '10.1.2.4']   # duplicate pair collapsed
    assert params['values'] == ['aa', 'bb']
    assert params['kind'] == 'ip_mac' and params['seen'] == seen


def test_upsert_bindings_noop_on_empty():
    db = MagicMock()
    store = ProfileStore(db)
    store.upsert_bindings('ip_mac', [], datetime(2026, 6, 1, tzinfo=timezone.utc))
    db.execute_query.assert_not_called()


def test_upsert_bindings_ignores_trailing_asset_id():
    # B3 identity_swap now threads (key, value, asset_id) triples through here;
    # this store only tracks key/value stability, so the asset_id is dropped.
    db = MagicMock()
    store = ProfileStore(db)
    seen = datetime(2026, 6, 1, tzinfo=timezone.utc)
    store.upsert_bindings(
        'hostname_sn', [('server01', 'SN-NEW', 'asset-swapped')], seen)
    sql, params = db.execute_query.call_args.args
    assert params['keys'] == ['server01']
    assert params['values'] == ['SN-NEW']
