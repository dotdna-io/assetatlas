# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from src.modules.anomaly_detection.domain import RunEvalContext
from src.modules.anomaly_detection.detectors.behavior import (
    AssetChangeBurstDetector,
    AssetThrashDetector,
    IdentitySwapDetector,
    PresenceFlapDetector,
)

B1A_PARAMS = {'min_attrs': 8, 'systemic_pct': 20}
B1B_PARAMS = {'window_runs': 10, 'min_runs': 8, 'systemic_pct': 20}
B2_PARAMS = {'min_flaps': 4, 'window_days': 30}


def _ctx(repo, params, total_assets=73000):
    return RunEvalContext(
        generation_run_id='grid-1', run_started_at=datetime(2026, 6, 1),
        current_stats={'total_assets': total_assets},
        rule={'slug': 'x', 'threshold': params}, params=params,
        settings=MagicMock(), repo=repo)


# --------------------------- B1a AssetChangeBurst ---------------------------

_TEN_ATTRS = ['os', 'ip', 'mac', 'model', 'hostname', 'vendor', 'serial',
              'category', 'owner', 'location']
_EIGHT_ATTRS = ['data_source', 'last_seen', 'os', 'ip', 'mac', 'model',
                'hostname', 'category']


def test_b1a_burst_fires():
    repo = MagicMock()
    repo.get_asset_change_bursts.return_value = [
        {'asset_id': 'a1', 'attrs': list(_TEN_ATTRS), 'n': 10}]
    repo.get_attribute_breakdown.return_value = [{'attr': 'os', 'n': 3}]   # none systemic
    f = AssetChangeBurstDetector().evaluate(_ctx(repo, B1A_PARAMS))
    assert len(f) == 1 and f[0].dimension_key == 'burst' and f[0].kind == 'event'
    assert f[0].alert_type == 'data_quality'
    assert f[0].evidence['observed'] == 1
    assert f[0].evidence['assets'][0] == {
        'asset_id': 'a1', 'attr_count': 10, 'attributes': sorted(_TEN_ATTRS)}
    assert 'a1' in f[0].asset_ids
    repo.get_asset_change_bursts.assert_called_once_with('grid-1', 8)


def test_b1a_all_systemic_excluded_rename_flood():
    # Every one of the burst asset's attrs changed fleet-wide this run -> all
    # systemic -> excluded -> the 47k-rename shape must NOT read as a burst.
    repo = MagicMock()
    repo.get_asset_change_bursts.return_value = [
        {'asset_id': 'a1', 'attrs': list(_EIGHT_ATTRS), 'n': 8}]
    repo.get_attribute_breakdown.return_value = [
        {'attr': a, 'n': 50000} for a in _EIGHT_ATTRS]
    assert AssetChangeBurstDetector().evaluate(_ctx(repo, B1A_PARAMS)) == []


def test_b1a_partial_systemic_drops_below_min():
    # 3 of 8 attrs systemic -> 5 non-systemic survive, below min_attrs (8).
    repo = MagicMock()
    repo.get_asset_change_bursts.return_value = [
        {'asset_id': 'a1', 'attrs': list(_EIGHT_ATTRS), 'n': 8}]
    repo.get_attribute_breakdown.return_value = (
        [{'attr': a, 'n': 50000} for a in _EIGHT_ATTRS[:3]]
        + [{'attr': a, 'n': 10} for a in _EIGHT_ATTRS[3:]])
    assert AssetChangeBurstDetector().evaluate(_ctx(repo, B1A_PARAMS)) == []


def test_b1a_empty_quiet():
    repo = MagicMock()
    repo.get_asset_change_bursts.return_value = []
    assert AssetChangeBurstDetector().evaluate(_ctx(repo, B1A_PARAMS)) == []


# --------------------------- B1b AssetThrash ---------------------------

def _activity(asset_id, runs, attrs, start=datetime(2026, 5, 20)):
    return [{'asset_id': asset_id, 'run_id': f'run-{i}',
             'run_date': start + timedelta(days=i), 'attrs': list(attrs)}
            for i in range(runs)]


def test_b1b_two_sources_fighting_member():
    # An asset whose hostname is rewritten every run (two sources disagreeing).
    repo = MagicMock()
    repo.get_asset_run_activity.return_value = _activity('a1', 8, ['hostname'])
    repo.get_run_attr_totals.return_value = []       # nothing systemic
    f = AssetThrashDetector().evaluate(_ctx(repo, B1B_PARAMS))
    assert len(f) == 1 and f[0].dimension_key == 'membership' and f[0].kind == 'condition'
    assert f[0].evidence['observed'] == 1
    m = f[0].evidence['members'][0]
    assert m['asset_id'] == 'a1' and m['runs_active'] == 8
    assert 'hostname' in m['top_attributes']
    assert 'a1' in f[0].asset_ids
    repo.get_asset_run_activity.assert_called_once_with(10)


def test_b1b_systemic_only_runs_excluded():
    # The asset changes ONLY the run's systemic attribute each run -> every run
    # is excluded -> not a member.
    repo = MagicMock()
    repo.get_asset_run_activity.return_value = _activity('a2', 10, ['data_source'])
    repo.get_run_attr_totals.return_value = [
        {'run_id': f'run-{i}', 'attr': 'data_source', 'n': 50000} for i in range(10)]
    assert AssetThrashDetector().evaluate(_ctx(repo, B1B_PARAMS)) == []


def test_b1b_below_min_runs_not_member():
    repo = MagicMock()
    repo.get_asset_run_activity.return_value = _activity('a1', 5, ['hostname'])
    repo.get_run_attr_totals.return_value = []
    assert AssetThrashDetector().evaluate(_ctx(repo, B1B_PARAMS)) == []


def test_b1b_empty_membership_returns_empty():
    repo = MagicMock()
    repo.get_asset_run_activity.return_value = []
    assert AssetThrashDetector().evaluate(_ctx(repo, B1B_PARAMS)) == []


# --------------------------- B2 PresenceFlap ---------------------------

def test_b2_fires_when_repo_returns_flaps():
    repo = MagicMock()
    repo.get_presence_flaps.return_value = [
        {'asset_id': 'a2', 'flips': 4}, {'asset_id': 'a1', 'flips': 6}]
    f = PresenceFlapDetector().evaluate(_ctx(repo, B2_PARAMS))
    assert len(f) == 1 and f[0].dimension_key == 'membership' and f[0].kind == 'condition'
    assert f[0].severity == 'low' and f[0].alert_type == 'data_quality'
    assert f[0].evidence['observed'] == 2
    assert f[0].evidence['members'][0] == {'asset_id': 'a1', 'flips': 6}   # sorted desc
    assert set(f[0].asset_ids) == {'a1', 'a2'}
    repo.get_presence_flaps.assert_called_once_with(30, 4)


def test_b2_empty_quiet():
    repo = MagicMock()
    repo.get_presence_flaps.return_value = []
    assert PresenceFlapDetector().evaluate(_ctx(repo, B2_PARAMS)) == []


# --------------------------- B3 IdentitySwap ---------------------------

B3_PARAMS = {'stability_days': 14, 'min_seen': 3}


def _b3_ctx(repo, profiles, params=None):
    return RunEvalContext(
        generation_run_id='grid-1', run_started_at=datetime(2026, 6, 1),
        current_stats={},
        rule={'id': 'rule-b3', 'slug': 'x', 'threshold': params or B3_PARAMS},
        params=params or B3_PARAMS, settings=MagicMock(), repo=repo, profiles=profiles)


def _stable(hostname_sn=None, ip_mac=None):
    def side_effect(kind, *_):
        return {'hostname_sn': hostname_sn or {}, 'ip_mac': ip_mac or {}}[kind]
    return side_effect


def test_b3_dhcp_pool_churn_silent():
    # A DHCP address never accumulates a stable binding -> get_stable_bindings
    # returns nothing -> no flip fires, but the observation is still recorded.
    repo = MagicMock()
    repo.get_current_bindings.return_value = {
        'hostname_sn': [], 'ip_mac': [('10.0.0.50', 'aa:bb:cc:dd:ee:01', 'a1')]}
    repo.get_recent_fingerprints_like.return_value = set()
    profiles = MagicMock()
    profiles.get_stable_bindings.side_effect = _stable()      # nothing stable
    ctx = _b3_ctx(repo, profiles)
    assert IdentitySwapDetector().evaluate(ctx) == []
    profiles.upsert_bindings.assert_any_call(
        'ip_mac', [('10.0.0.50', 'aa:bb:cc:dd:ee:01', 'a1')], ctx.run_started_at)


def test_b3_stable_hostname_flip_fires_once_high():
    repo = MagicMock()
    repo.get_current_bindings.return_value = {
        'hostname_sn': [('server01', 'SN-NEW', 'a1')], 'ip_mac': []}
    repo.get_recent_fingerprints_like.return_value = set()    # not fired before
    profiles = MagicMock()
    profiles.get_stable_bindings.side_effect = _stable(
        hostname_sn={'server01': {'value': 'SN-OLD', 'seen_count': 20, 'age_days': 43}})
    f = IdentitySwapDetector().evaluate(_b3_ctx(repo, profiles))
    assert len(f) == 1
    assert f[0].dimension_key == 'hostname_sn:server01:SN-OLD>SN-NEW'
    assert f[0].kind == 'event' and f[0].severity == 'high'
    assert f[0].alert_type == 'security'
    ev = f[0].evidence
    assert ev['old_value'] == 'SN-OLD' and ev['new_value'] == 'SN-NEW'
    assert ev['stability_age_days'] == 43 and ev['seen_count'] == 20
    # Task 5b: generic kind-aware title; the specifics move into the narrative.
    assert f[0].title == "Stable asset now reports different hardware"
    assert 'server01' not in f[0].title and 'SN-NEW' not in f[0].title
    assert '43 days' in f[0].description and 'SN-NEW' in f[0].description
    # dedup used the anchored dateless prefix (service appends :{date})
    assert (repo.get_recent_fingerprints_like.call_args.args[1]
            == 'anomaly:identity_swap:hostname_sn:server01:SN-OLD>SN-NEW:')


def test_identity_swap_populates_asset_ids():
    # Task 3: the finding names the affected asset so its drilldown grid isn't
    # empty; subject_type stays the default 'asset' (per-asset), from Task 1.
    repo = MagicMock()
    repo.get_current_bindings.return_value = {
        'hostname_sn': [('server01', 'SN-NEW', 'asset-swapped')], 'ip_mac': []}
    repo.get_recent_fingerprints_like.return_value = set()
    profiles = MagicMock()
    profiles.get_stable_bindings.side_effect = _stable(
        hostname_sn={'server01': {'value': 'SN-OLD', 'seen_count': 20, 'age_days': 43}})
    findings = IdentitySwapDetector().evaluate(_b3_ctx(repo, profiles))
    assert findings and findings[0].asset_ids == ['asset-swapped']
    assert findings[0].subject_type == 'asset'


def test_b3_next_day_does_not_refire_via_prefix_dedup():
    repo = MagicMock()
    repo.get_current_bindings.return_value = {
        'hostname_sn': [('server01', 'SN-NEW', 'a1')], 'ip_mac': []}
    # yesterday's alert for this exact flip is within the stability window
    repo.get_recent_fingerprints_like.return_value = {
        'anomaly:identity_swap:hostname_sn:server01:SN-OLD>SN-NEW:2026-05-31'}
    profiles = MagicMock()
    profiles.get_stable_bindings.side_effect = _stable(
        hostname_sn={'server01': {'value': 'SN-OLD', 'seen_count': 21, 'age_days': 44}})
    assert IdentitySwapDetector().evaluate(_b3_ctx(repo, profiles)) == []


def test_b3_dedup_prefix_is_anchored_against_value_prefix_collision():
    # A genuine flip old>'SN1' must build a prefix that a prior alert old>'SN12'
    # cannot swallow. The trailing ':' anchors the prefix at the value boundary.
    repo = MagicMock()
    repo.get_current_bindings.return_value = {
        'hostname_sn': [('server01', 'SN1', 'a1')], 'ip_mac': []}
    repo.get_recent_fingerprints_like.return_value = set()
    profiles = MagicMock()
    profiles.get_stable_bindings.side_effect = _stable(
        hostname_sn={'server01': {'value': 'SN-OLD', 'seen_count': 20, 'age_days': 43}})
    f = IdentitySwapDetector().evaluate(_b3_ctx(repo, profiles))
    assert len(f) == 1
    prefix = repo.get_recent_fingerprints_like.call_args.args[1]
    assert prefix == 'anomaly:identity_swap:hostname_sn:server01:SN-OLD>SN1:'
    assert prefix.endswith('>SN1:')          # cannot LIKE-match a stored '>SN12:...'


def test_b3_ip_mac_flip_is_medium():
    repo = MagicMock()
    repo.get_current_bindings.return_value = {
        'hostname_sn': [], 'ip_mac': [('10.0.0.9', 'bb:bb:bb:bb:bb:bb', 'a1')]}
    repo.get_recent_fingerprints_like.return_value = set()
    profiles = MagicMock()
    profiles.get_stable_bindings.side_effect = _stable(
        ip_mac={'10.0.0.9': {'value': 'aa:aa:aa:aa:aa:aa', 'seen_count': 10, 'age_days': 30}})
    f = IdentitySwapDetector().evaluate(_b3_ctx(repo, profiles))
    assert len(f) == 1 and f[0].severity == 'medium'
    assert f[0].dimension_key == 'ip_mac:10.0.0.9:aa:aa:aa:aa:aa:aa>bb:bb:bb:bb:bb:bb'
    # Task 5b: ip_mac flavor gets its own generic title (specifics in narrative).
    assert f[0].title == "Stable address now reports a different device"
    assert '10.0.0.9' not in f[0].title
    assert '10.0.0.9' in f[0].description and 'bb:bb:bb:bb:bb:bb' in f[0].description


def test_b3_multi_mac_excluded_by_repo_contract():
    # The repo returns only single-mac assets, so a multi-mac asset never appears
    # in ip_mac pairs — the detector invents nothing even though a stable binding
    # exists for that IP.
    repo = MagicMock()
    repo.get_current_bindings.return_value = {'hostname_sn': [], 'ip_mac': []}
    repo.get_recent_fingerprints_like.return_value = set()
    profiles = MagicMock()
    profiles.get_stable_bindings.side_effect = _stable(
        ip_mac={'10.0.0.9': {'value': 'aa', 'seen_count': 9, 'age_days': 30}})
    assert IdentitySwapDetector().evaluate(_b3_ctx(repo, profiles)) == []


def test_b3_unchanged_value_does_not_fire():
    repo = MagicMock()
    repo.get_current_bindings.return_value = {
        'hostname_sn': [('server01', 'SN-OLD', 'a1')], 'ip_mac': []}
    repo.get_recent_fingerprints_like.return_value = set()
    profiles = MagicMock()
    profiles.get_stable_bindings.side_effect = _stable(
        hostname_sn={'server01': {'value': 'SN-OLD', 'seen_count': 20, 'age_days': 43}})
    assert IdentitySwapDetector().evaluate(_b3_ctx(repo, profiles)) == []


# --------------------------- de-jargon copy scan (Task 5b) ---------------------------

def test_b_family_copy_is_plain_and_generic():
    repo = MagicMock()
    repo.get_current_bindings.return_value = {
        'hostname_sn': [('server01', 'SN-NEW', 'a1')], 'ip_mac': []}
    repo.get_recent_fingerprints_like.return_value = set()
    profiles = MagicMock()
    profiles.get_stable_bindings.side_effect = _stable(
        hostname_sn={'server01': {'value': 'SN-OLD', 'seen_count': 20, 'age_days': 43}})
    f = IdentitySwapDetector().evaluate(_b3_ctx(repo, profiles))[0]
    blob = '\n'.join([f.title, f.description])
    for token in ('z=', 'MAD', 'percentile', '#802', 'Proxmox'):
        assert token not in blob
    # generic, kind-aware title carries no baked-in hostname/serial
    assert f.title == "Stable asset now reports different hardware"
    assert 'server01' not in f.title and 'SN-' not in f.title
