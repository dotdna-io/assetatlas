# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.modules.anomaly_detection.repository import AnomalyRepository


def _repo(rows=None):
    db = MagicMock()
    db.execute_query.return_value = rows if rows is not None else []
    return AnomalyRepository(db), db


def test_get_enabled_anomaly_rules_filters_type_and_enabled():
    repo, db = _repo([])
    repo.get_enabled_anomaly_rules()
    sql = db.execute_query.call_args.args[0]
    assert "evaluation_type = 'anomaly'" in sql
    assert 'enabled' in sql


def test_get_recent_run_stats_params_and_completed_filter():
    repo, db = _repo([])
    before = datetime(2026, 6, 1, tzinfo=timezone.utc)
    repo.get_recent_run_stats(before, limit=50)
    sql, params = db.execute_query.call_args.args
    assert "status = 'completed'" in sql and 'stats IS NOT NULL' in sql
    assert params['before'] == before and params['limit'] == 50


def test_get_created_assets_casts_grid_and_excludes_merge():
    repo, db = _repo([])
    repo.get_created_assets('the-grid')
    sql, params = db.execute_query.call_args.args
    assert '%(grid)s::uuid' in sql and "event_type = 'asset_created'" in sql
    assert "!= 'pipeline_merge'" in sql
    assert params['grid'] == 'the-grid'


def test_get_datasource_loss_events_filters_removed_members():
    repo, db = _repo([])
    repo.get_datasource_loss_events('grid-1')
    sql, params = db.execute_query.call_args.args
    assert "'data_source' = ANY(c.changed_attributes)" in sql
    assert 'removed' in sql
    assert params['grid'] == 'grid-1'


def test_get_datasource_loss_history_window_param():
    repo, db = _repo([])
    repo.get_datasource_loss_history(90)
    sql, params = db.execute_query.call_args.args
    assert params['days'] == 90
    assert 'loss_count' in sql


def test_get_datasource_loss_history_excludes_current_run():
    repo, db = _repo([])
    repo.get_datasource_loss_history(90, exclude_grid='the-grid')
    sql, params = db.execute_query.call_args.args
    assert 'IS DISTINCT FROM %(exclude_grid)s::uuid' in sql
    assert params['exclude_grid'] == 'the-grid'


def test_get_total_assets_returns_int():
    repo, db = _repo([{'n': 73739}])
    assert repo.get_total_assets() == 73739


def test_get_segment_profiles_passes_segments_list():
    repo, db = _repo([])
    started = datetime(2026, 6, 1, tzinfo=timezone.utc)
    repo.get_segment_profiles(['10.1.2.0/24'], started)
    sql, params = db.execute_query.call_args.args
    assert params['segments'] == ['10.1.2.0/24']
    assert params['run_started'] == started


def test_get_source_category_map_picks_majority_per_source():
    rows = [
        {'source': 'SentinelOne', 'category': 'endpoint security', 'n': 100},
        {'source': 'SentinelOne', 'category': 'cmdb', 'n': 5},
        {'source': 'ServiceNow', 'category': 'cmdb', 'n': 40},
    ]
    repo, db = _repo(rows)
    m = repo.get_source_category_map()
    assert m == {'SentinelOne': 'endpoint security', 'ServiceNow': 'cmdb'}


def test_get_missing_onset_count_returns_int():
    repo, db = _repo([{'n': 42}])
    assert repo.get_missing_onset_count(datetime(2026, 6, 1, tzinfo=timezone.utc)) == 42


def test_get_missing_onset_count_zero_when_empty():
    repo, db = _repo([])
    assert repo.get_missing_onset_count(datetime(2026, 6, 1, tzinfo=timezone.utc)) == 0


def test_get_active_anomaly_alerts_filters_active_with_fingerprint():
    repo, db = _repo([])
    repo.get_active_anomaly_alerts('rule-1')
    sql, params = db.execute_query.call_args.args
    assert "status = 'active'" in sql and 'fingerprint IS NOT NULL' in sql
    # severity must be selected so condition-resolve events record the real
    # severity, not a hardcoded 'low' fallback (#906).
    assert 'severity' in sql
    assert params['rule_id'] == 'rule-1'


def test_get_recent_alert_fingerprints_returns_set():
    rows = [{'fingerprint': 'a'}, {'fingerprint': 'b'}, {'fingerprint': 'a'}]
    repo, db = _repo(rows)
    since = datetime(2026, 6, 1, tzinfo=timezone.utc)
    result = repo.get_recent_alert_fingerprints('rule-1', since)
    assert result == {'a', 'b'}
    params = db.execute_query.call_args.args[1]
    assert params['since'] == since


# --------------------------- phase-2 detector reads ---------------------------

def test_get_recent_fingerprints_like_escapes_and_appends_wildcard():
    rows = [{'fingerprint': 'anomaly:identity_swap:ip_mac:10.1.2.3:aa>bb:2026-07-01'}]
    repo, db = _repo(rows)
    since = datetime(2026, 6, 20, tzinfo=timezone.utc)
    out = repo.get_recent_fingerprints_like(
        'rule-1', 'anomaly:identity_swap:ip_mac:10.1.2.3:aa>bb', since)
    assert out == {'anomaly:identity_swap:ip_mac:10.1.2.3:aa>bb:2026-07-01'}
    sql, params = db.execute_query.call_args.args
    assert 'LIKE %(prefix)s ESCAPE' in sql
    # 'identity_swap'/'ip_mac' underscores escaped so they don't act as wildcards
    assert params['prefix'] == r'anomaly:identity\_swap:ip\_mac:10.1.2.3:aa>bb%'
    assert params['rule_id'] == 'rule-1' and params['since'] == since


def test_get_asset_change_bursts_min_attrs_and_merge_exclusion():
    repo, db = _repo([])
    repo.get_asset_change_bursts('the-grid', 8)
    sql, params = db.execute_query.call_args.args
    assert '%(grid)s::uuid' in sql and "!= 'pipeline_merge'" in sql
    assert 'HAVING count(DISTINCT attr) >= %(min_attrs)s' in sql
    assert 'array_agg(DISTINCT attr)' in sql
    assert params['grid'] == 'the-grid' and params['min_attrs'] == 8


def test_get_scattered_event_count_uses_subset_check():
    repo, db = _repo([{'n': 300}])
    n = repo.get_scattered_event_count('the-grid', {'data_source', 'last_seen'})
    assert n == 300
    sql, params = db.execute_query.call_args.args
    assert '%(grid)s::uuid' in sql and "!= 'pipeline_merge'" in sql
    assert '<@ %(systemic)s::text[]' in sql and 'NOT (' in sql
    assert params['grid'] == 'the-grid'
    assert params['systemic'] == ['data_source', 'last_seen']    # sorted set -> list


def test_get_asset_run_activity_uses_last_n_runs_not_days():
    repo, db = _repo([])
    repo.get_asset_run_activity(10)
    sql, params = db.execute_query.call_args.args
    assert 'generation_run_id AS run_id' in sql
    assert 'recent_runs' in sql and 'LIMIT %(runs)s' in sql   # run-count window
    assert "|| ' days')::interval" not in sql                # NOT a day window
    assert "!= 'pipeline_merge'" in sql
    assert 'GROUP BY c.asset_id, c.generation_run_id' in sql
    assert params == {'runs': 10}


def test_get_run_attr_totals_uses_last_n_runs():
    repo, db = _repo([])
    repo.get_run_attr_totals(10)
    sql, params = db.execute_query.call_args.args
    assert 'GROUP BY c.generation_run_id, attr' in sql
    assert 'recent_runs' in sql and 'LIMIT %(runs)s' in sql
    assert "|| ' days')::interval" not in sql
    assert params == {'runs': 10}


def test_get_presence_flaps_uses_lag_and_min_flaps():
    repo, db = _repo([])
    repo.get_presence_flaps(30, 4)
    sql, params = db.execute_query.call_args.args
    assert 'LAG(missing_since IS NOT NULL)' in sql
    assert 'assets_history' in sql
    assert 'HAVING count(*) >= %(min_flaps)s' in sql
    assert params['days'] == 30 and params['min_flaps'] == 4


def test_unambiguous_pairs_carry_asset_id():
    from src.modules.anomaly_detection.repository import _unambiguous_pairs
    rows = [{'key': 'srv-legacy-77', 'value': 'DELL-SN-4QW8Z1', 'asset_id': 'a-1'}]
    assert _unambiguous_pairs(rows) == [('srv-legacy-77', 'DELL-SN-4QW8Z1', 'a-1')]


def test_unambiguous_pairs_drops_collisions():
    from src.modules.anomaly_detection.repository import _unambiguous_pairs
    rows = [{'key': 'host', 'value': 'SN1', 'asset_id': 'a'},
            {'key': 'host', 'value': 'SN2', 'asset_id': 'b'}]
    assert _unambiguous_pairs(rows) == []


def test_get_current_bindings_shapes_pairs_and_drops_collisions():
    db = MagicMock()
    db.execute_query.side_effect = [
        [{'key': 'host1', 'value': 'SN123', 'asset_id': 'a-1'},
         {'key': None, 'value': 'x', 'asset_id': 'a-2'},
         {'key': 'host2', 'value': None, 'asset_id': 'a-3'},
         {'key': 'shared', 'value': 'S1', 'asset_id': 'a-4'},
         {'key': 'shared', 'value': 'S2', 'asset_id': 'a-5'}],  # collision
        [{'key': '10.1.2.3', 'value': 'aa:bb:cc:dd:ee:ff', 'asset_id': 'a-6'}],  # ip_mac (colons survive)
    ]
    repo = AnomalyRepository(db)
    out = repo.get_current_bindings()
    # null key/value dropped, and 'shared' dropped (two assets, two serials -> not
    # an identity binding, so B3 can't manufacture a false swap from it)
    assert out['hostname_sn'] == [('host1', 'SN123', 'a-1')]
    assert out['ip_mac'] == [('10.1.2.3', 'aa:bb:cc:dd:ee:ff', 'a-6')]
    calls = db.execute_query.call_args_list
    assert 'array_length(sn, 1) = 1' in calls[0].args[0]
    assert 'array_length(a.mac, 1) = 1' in calls[1].args[0]
    assert calls[1].args[1]['strict_ipv4']                       # ip picker guarded


def test_get_settle_window_assets_bounds_first_seen():
    repo, db = _repo([])
    started = datetime(2026, 6, 1, tzinfo=timezone.utc)
    repo.get_settle_window_assets(started, 2, 7)
    sql, params = db.execute_query.call_args.args
    assert "(%(settle_max)s || ' days')::interval" in sql
    assert "(%(settle_min)s || ' days')::interval" in sql
    assert params['run_started'] == started
    assert params['settle_min'] == 2 and params['settle_max'] == 7


def test_get_segment_coverage_overlap_and_established_peer_filter():
    repo, db = _repo([])
    started = datetime(2026, 6, 1, tzinfo=timezone.utc)
    repo.get_segment_coverage(['10.1.2.0/24'], ['Server', 'Workstation'],
                              ['SentinelOne'], started, 7)
    sql, params = db.execute_query.call_args.args
    assert 'a.data_source && %(security)s::text[]' in sql
    # peers exclude the whole settle window, not just this run — otherwise the
    # newcomers under evaluation dilute their own coverage
    assert "a.first_seen < %(run_started)s - (%(settle_max)s || ' days')::interval" in sql
    assert 'sub.segment = ANY(%(segments)s)' in sql
    assert params['segments'] == ['10.1.2.0/24']
    assert params['eligible'] == ['Server', 'Workstation']
    assert params['security'] == ['SentinelOne']
    assert params['run_started'] == started and params['settle_max'] == 7


# ------------------------ asset-label resolution (Task 4) ---------------------

def test_get_asset_labels_builds_canonical_coalesce_and_maps_ids():
    rows = [{'id': 'a1', 'label': 'web-01'}, {'id': 'a2', 'label': None}]
    repo, db = _repo(rows)
    result = repo.get_asset_labels(['a1', 'a2'])
    sql, params = db.execute_query.call_args.args
    # canonical asset_label COALESCE (mirrors fields/catalog.py:180-193) so anomaly
    # copy shows the same label Explorer does
    assert 'asset_label_override' in sql and 'hostname[1]' in sql
    assert params['ids'] == ['a1', 'a2']
    assert result == {'a1': 'web-01'}          # null-label row omitted


def test_get_asset_labels_empty_input_returns_empty_dict():
    repo, db = _repo([])
    assert repo.get_asset_labels([]) == {}
    db.execute_query.assert_not_called()       # short-circuits, no round trip


# ------------------------ evidence sample resolution (Task 4) -----------------

def test_get_event_asset_ids_filters_event_type_and_excludes_merge():
    rows = [{'asset_id': 'a1'}, {'asset_id': 'a2'}, {'asset_id': None}]
    repo, db = _repo(rows)
    result = repo.get_event_asset_ids('the-grid', 'asset_created', limit=25)
    sql, params = db.execute_query.call_args.args
    assert '%(grid)s::uuid' in sql and 'c.event_type = %(event_type)s' in sql
    assert "!= 'pipeline_merge'" in sql and 'LIMIT %(limit)s' in sql
    assert params == {'grid': 'the-grid', 'event_type': 'asset_created', 'limit': 25}
    assert result == ['a1', 'a2']              # null asset_id dropped, cast to str


def test_get_event_asset_ids_default_limit_is_25():
    repo, db = _repo([])
    repo.get_event_asset_ids('the-grid', 'asset_readded')
    params = db.execute_query.call_args.args[1]
    assert params['limit'] == 25
