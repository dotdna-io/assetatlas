# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from src.modules.anomaly_detection.domain import RunEvalContext
from src.modules.anomaly_detection.detectors.volume import (
    MassNewAssetsDetector,
    MassMissingDetector,
    SourceVolumeDetector,
    AttributeChurnDetector,
    IdentityChurnDetector,
    ConfidenceShiftDetector,
)

A3_PARAMS = {'z_medium': 3, 'z_high': 5, 'z_critical': 8, 'min_count': 25,
             'min_pct': 15, 'window_runs': 30, 'min_samples': 8}
A4_PARAMS = {'z_medium': 3, 'z_high': 5, 'z_critical': 8, 'min_count': 100,
             'systemic_pct': 20, 'window_runs': 30, 'min_samples': 8}

SEED_PARAMS = {'z_medium': 3, 'z_high': 5, 'z_critical': 8, 'min_count': 20,
               'window_runs': 30, 'min_samples': 8}


def _history(values, triggered='schedule', start=datetime(2026, 4, 6)):
    return [{'started_at': start + timedelta(days=i), 'triggered_by': triggered,
             'stats': {'chronicle_events': {'asset_created': v, 'attribute_updated': 50,
                                            'asset_removed': 2, 'asset_readded': 1}}}
            for i, v in enumerate(values)]


def _missing_history(values, triggered='schedule', start=datetime(2026, 4, 6)):
    return [{'started_at': start + timedelta(days=i), 'triggered_by': triggered,
             'stats': {'grace_missing_onsets': v}}
            for i, v in enumerate(values)]


def _ctx(repo, created_now, weekday, params=None):
    # 2026-06-01 is a Monday, so +weekday days lands on the requested weekday.
    run_started = datetime(2026, 6, 1) + timedelta(days=weekday)
    stats = {'stages': {'chronicle': {'event_type_counts': {
        'asset_created': created_now, 'attribute_updated': 50,
        'asset_removed': 2, 'asset_readded': 1}}}}
    p = params or SEED_PARAMS
    return RunEvalContext(
        generation_run_id='grid-1', run_started_at=run_started,
        current_stats=stats, rule={'slug': 'x', 'threshold': p},
        params=p, settings=MagicMock(), repo=repo)


# --------------------------- A1 MassNewAssets ---------------------------

def test_a1_weekend_sawtooth_does_not_fire():
    # Monday surges alternating with weekend lows, 9 weeks of history:
    # same-weekday baseline sees Mondays vs Mondays -> quiet.
    values = ([300, 80, 90, 85, 88, 20, 15] * 9)   # Mon..Sun pattern
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_history(values)))
    ctx = _ctx(repo, created_now=310, weekday=0)   # a normal Monday
    assert MassNewAssetsDetector().evaluate(ctx) == []


def test_a1_true_spike_fires_with_evidence():
    values = [200, 205, 210, 195, 202, 208, 199, 204] * 4
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_history(values)))
    ctx = _ctx(repo, created_now=1403, weekday=0)
    f = MassNewAssetsDetector().evaluate(ctx)
    assert len(f) == 1 and f[0].kind == 'event'
    ev = f[0].evidence
    assert ev['observed'] == 1403 and ev['technical']['median'] > 0 and ev['technical']['z'] > 8
    assert f[0].severity == 'critical'
    assert f[0].alert_type == 'operational'
    # Locks the evidence['technical'] contract: raw stats must not leak to the
    # top level of evidence (the UI's primary view renders top-level keys plainly).
    assert 'technical' in ev and 'z' not in ev


def test_a1_learning_mode_silent():
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = _history([200, 210])
    assert MassNewAssetsDetector().evaluate(_ctx(repo, created_now=900, weekday=0)) == []


def test_a1_below_min_count_floor_silent():
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_history([1] * 30)))
    assert MassNewAssetsDetector().evaluate(_ctx(repo, created_now=15, weekday=0)) == []


def test_a1_zero_created_silent():
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_history([200] * 30)))
    assert MassNewAssetsDetector().evaluate(_ctx(repo, created_now=0, weekday=0)) == []


def test_a1_mass_new_assets_attaches_capped_sample():
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(
        _history([200, 205, 210, 195, 202, 208, 199, 204] * 4)))
    repo.get_event_asset_ids.return_value = ['a1', 'a2']
    f = MassNewAssetsDetector().evaluate(_ctx(repo, created_now=1403, weekday=0))
    assert f[0].sample_asset_ids == ['a1', 'a2']
    repo.get_event_asset_ids.assert_called_once_with('grid-1', 'asset_created', 25)


# --------------------------- A2 MassMissing ---------------------------

def test_a2_reads_missing_onsets_and_fires():
    values = [18, 22, 20, 19, 21, 20, 23, 17] * 4   # quiet ~20
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_missing_history(values)))
    repo.get_missing_onset_count.return_value = 400
    ctx = _ctx(repo, created_now=0, weekday=1)
    f = MassMissingDetector().evaluate(ctx)
    assert len(f) == 1
    assert f[0].alert_type == 'security' and f[0].kind == 'event'
    assert f[0].evidence['observed'] == 400 and f[0].evidence['technical']['z'] > 8
    repo.get_missing_onset_count.assert_called_once()


def test_a2_missing_stat_in_history_stays_learning():
    # stats rows WITHOUT grace_missing_onsets => value None => learning => silent
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_history([100] * 30)))
    repo.get_missing_onset_count.return_value = 400
    assert MassMissingDetector().evaluate(_ctx(repo, created_now=0, weekday=1)) == []


def test_a2_zero_onsets_silent():
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_missing_history([20] * 30)))
    repo.get_missing_onset_count.return_value = 0
    assert MassMissingDetector().evaluate(_ctx(repo, created_now=0, weekday=1)) == []


# --------------------------- A3 SourceVolume ---------------------------

def _vol_history(per_run_batches, triggered='schedule', start=datetime(2026, 4, 6)):
    """per_run_batches: list (oldest->newest) of [(name, unique_assets), ...]."""
    rows = []
    for i, batches in enumerate(per_run_batches):
        rows.append({
            'started_at': start + timedelta(days=i), 'triggered_by': triggered,
            'stats': {'batch_results': [
                {'integration_name': n, 'unique_assets': u, 'success': True}
                for (n, u) in batches]}})
    return rows


def _a3_ctx(repo, current_batches, weekday, params=None):
    run_started = datetime(2026, 6, 1) + timedelta(days=weekday)
    p = params or A3_PARAMS
    return RunEvalContext(
        generation_run_id='grid-1', run_started_at=run_started,
        current_stats={'stages': {}}, rule={'slug': 'x', 'threshold': p},
        params=p, settings=MagicMock(), repo=repo, current_batches=current_batches)


def test_a3_volume_drop_fires_condition_per_source():
    hist = _vol_history([[('SentinelOne', 25000 + (i % 5) * 40)] for i in range(30)])
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(hist))
    ctx = _a3_ctx(repo, [{'integration_name': 'SentinelOne', 'unique_assets': 15000,
                          'success': True}], weekday=1)
    f = SourceVolumeDetector().evaluate(ctx)
    assert len(f) == 1
    assert f[0].dimension_key == 'SentinelOne:volume' and f[0].kind == 'condition'
    assert f[0].evidence['observed'] == 15000
    assert f[0].alert_type == 'operational'


def test_a3_weekend_dip_seasonal_quiet():
    per_run = []
    for i in range(63):
        wd = (datetime(2026, 4, 6) + timedelta(days=i)).weekday()
        per_run.append([('Fortigate', 500 if wd >= 5 else 5000)])
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_vol_history(per_run)))
    # current run is a Saturday, Fortigate low as usual -> same-weekday baseline quiet
    ctx = _a3_ctx(repo, [{'integration_name': 'Fortigate', 'unique_assets': 520,
                          'success': True}], weekday=5)
    assert SourceVolumeDetector().evaluate(ctx) == []


def test_a3_failed_batch_routes_distinctly():
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(
        reversed(_vol_history([[('CrowdStrike', 8000)] for _ in range(30)])))
    ctx = _a3_ctx(repo, [{'integration_name': 'CrowdStrike', 'unique_assets': 0,
                          'success': False, 'error': 'connector timeout'}], weekday=1)
    f = SourceVolumeDetector().evaluate(ctx)
    assert len(f) == 1
    assert f[0].dimension_key == 'CrowdStrike:failed' and f[0].severity == 'high'
    assert 'fail' in f[0].title.lower() or 'connector' in f[0].title.lower()


def test_a3_missing_batch_detected_by_history_presence():
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(
        _vol_history([[('SentinelOne', 25000), ('Nessus', 3000)] for _ in range(30)])))
    ctx = _a3_ctx(repo, [{'integration_name': 'SentinelOne', 'unique_assets': 25000,
                          'success': True}], weekday=1)
    dims = {x.dimension_key for x in SourceVolumeDetector().evaluate(ctx)}
    assert 'Nessus:missing' in dims


def test_a3_small_source_min_floor_quiet():
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(
        reversed(_vol_history([[('TinySource', 12)] for _ in range(30)])))
    ctx = _a3_ctx(repo, [{'integration_name': 'TinySource', 'unique_assets': 6,
                          'success': True}], weekday=1)
    assert SourceVolumeDetector().evaluate(ctx) == []


def test_a3_missing_flood_collapses_to_one_aggregate():
    # A partial/manual run: 15 sources in history-majority, only 1 in the current
    # batches -> 14 missing > threshold (default 10) -> ONE aggregate condition,
    # not 14 'high' cards (observed for real on the dev DB, 2026-07-02).
    sources = [(f'Src{i:02d}', 1000) for i in range(15)]
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(
        reversed(_vol_history([sources for _ in range(30)])))
    ctx = _a3_ctx(repo, [{'integration_name': 'Src00', 'unique_assets': 1000,
                          'success': True}], weekday=1)
    f = SourceVolumeDetector().evaluate(ctx)
    assert len(f) == 1
    agg = f[0]
    assert agg.dimension_key == 'missing:aggregate' and agg.kind == 'condition'
    assert agg.evidence['aggregated'] is True and agg.evidence['count'] == 14
    assert 'Src01' in agg.evidence['sources']


def test_a3_missing_collapse_respects_configured_threshold():
    sources = [(f'Src{i}', 500) for i in range(5)]
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(
        reversed(_vol_history([sources for _ in range(30)])))
    settings = MagicMock()
    settings.aggregation_threshold = 3
    ctx = _a3_ctx(repo, [{'integration_name': 'Src0', 'unique_assets': 500,
                          'success': True}], weekday=1)
    ctx.settings = settings
    f = SourceVolumeDetector().evaluate(ctx)   # 4 missing > 3 -> aggregate
    assert len(f) == 1 and f[0].dimension_key == 'missing:aggregate'
    assert f[0].evidence['count'] == 4


# --------------------------- A4 AttributeChurn ---------------------------

def _history_attr(values, triggered='schedule', start=datetime(2026, 4, 6)):
    return [{'started_at': start + timedelta(days=i), 'triggered_by': triggered,
             'stats': {'chronicle_events': {'attribute_updated': v}}}
            for i, v in enumerate(values)]


def _a4_ctx(repo, attr_updated, total_assets, weekday, params=None):
    run_started = datetime(2026, 6, 1) + timedelta(days=weekday)
    p = params or A4_PARAMS
    stats = {'stages': {'chronicle': {'event_type_counts': {'attribute_updated': attr_updated}}},
             'total_assets': total_assets}
    return RunEvalContext(
        generation_run_id='grid-1', run_started_at=run_started,
        current_stats=stats, rule={'slug': 'x', 'threshold': p},
        params=p, settings=MagicMock(), repo=repo, current_batches=[])


def test_a4_systemic_rename_fires_systemic_not_scattered():
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_history_attr([200] * 30)))
    repo.get_attribute_breakdown.return_value = [
        {'attr': 'data_source', 'n': 46800}, {'attr': 'last_seen', 'n': 200}]
    repo.get_scattered_event_count.return_value = 200   # ~ baseline -> quiet
    ctx = _a4_ctx(repo, attr_updated=47000, total_assets=73000, weekday=1)
    f = AttributeChurnDetector().evaluate(ctx)
    systemic = [x for x in f if x.dimension_key.startswith('systemic:')]
    scattered = [x for x in f if x.dimension_key == 'scattered']
    assert len(systemic) == 1 and systemic[0].dimension_key == 'systemic:data_source'
    assert systemic[0].severity == 'low' and systemic[0].kind == 'event'
    assert systemic[0].alert_type == 'data_quality'
    assert scattered == []


def test_a4_scattered_spike_fires():
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_history_attr([200] * 30)))
    repo.get_attribute_breakdown.return_value = [
        {'attr': 'os', 'n': 900}, {'attr': 'ip', 'n': 850}, {'attr': 'model', 'n': 800}]
    repo.get_scattered_event_count.return_value = 5000
    ctx = _a4_ctx(repo, attr_updated=5000, total_assets=73000, weekday=1)
    f = AttributeChurnDetector().evaluate(ctx)
    assert [x for x in f if x.dimension_key.startswith('systemic:')] == []
    scattered = [x for x in f if x.dimension_key == 'scattered']
    assert len(scattered) == 1 and scattered[0].kind == 'event'
    assert scattered[0].evidence['observed'] == 5000


def test_a4_scattered_uses_exact_event_count_not_subtraction():
    # A systemic rename where 300 events ALSO touched a non-systemic attribute.
    # The exact per-event count (300) is used verbatim; the old
    # total_events - systemic_sum arithmetic would have under-counted these
    # overlapping events. The detector passes the systemic set to the query.
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_history_attr([50] * 30)))
    repo.get_attribute_breakdown.return_value = [
        {'attr': 'data_source', 'n': 46800}, {'attr': 'hostname', 'n': 300}]
    repo.get_scattered_event_count.return_value = 300
    ctx = _a4_ctx(repo, attr_updated=47000, total_assets=73000, weekday=1)
    f = AttributeChurnDetector().evaluate(ctx)
    scattered = [x for x in f if x.dimension_key == 'scattered']
    assert len(scattered) == 1 and scattered[0].evidence['observed'] == 300
    passed_systemic = repo.get_scattered_event_count.call_args.args[1]
    assert 'data_source' in passed_systemic and 'hostname' not in passed_systemic


def test_a4_total_assets_falls_back_to_repo_when_absent_from_stats():
    # In-pipeline gen-stats has no total_assets at s20, so A4 must read the
    # committed fleet size from the repo — else systemic detection is dead.
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_history_attr([200] * 30)))
    repo.get_attribute_breakdown.return_value = [{'attr': 'data_source', 'n': 46800}]
    repo.get_total_assets.return_value = 73000
    repo.get_scattered_event_count.return_value = 0
    ctx = _a4_ctx(repo, attr_updated=47000, total_assets=0, weekday=1)  # 0 -> fall back
    f = AttributeChurnDetector().evaluate(ctx)
    repo.get_total_assets.assert_called_once()
    assert [x.dimension_key for x in f if x.dimension_key.startswith('systemic:')] \
        == ['systemic:data_source']


def test_a4_learning_scattered_silent_but_systemic_still_fires():
    # too little history => scattered baseline learning => scattered silent;
    # systemic is proportion-based (no baseline) => still fires.
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_history_attr([200] * 3)))
    repo.get_attribute_breakdown.return_value = [{'attr': 'data_source', 'n': 46800}]
    repo.get_scattered_event_count.return_value = 47000
    ctx = _a4_ctx(repo, attr_updated=47000, total_assets=73000, weekday=1)
    f = AttributeChurnDetector().evaluate(ctx)
    assert [x.dimension_key for x in f] == ['systemic:data_source']


# --------------------------- A5 IdentityChurn ---------------------------

A5_PARAMS = {'z_medium': 3, 'z_high': 5, 'z_critical': 8, 'min_count': 50,
             'window_runs': 30, 'min_samples': 8}


def _a5_history(n=32, merged=20, readded=10, created=30, removed=5,
                triggered='schedule', start=datetime(2026, 4, 6)):
    """Quiet baseline for all four A5 series, with mild jitter (MAD > 0)."""
    rows = []
    for i in range(n):
        j = (i % 5) - 2                       # -2..+2
        rows.append({
            'started_at': start + timedelta(days=i), 'triggered_by': triggered,
            'stats': {'merged_assets': merged + j,
                      'chronicle_events': {'asset_created': created + j,
                                           'asset_removed': max(removed + j, 1),
                                           'asset_readded': readded + j}}})
    return rows


def _a5_ctx(repo, merged=20, created=30, removed=5, readded=10, weekday=1, params=None):
    run_started = datetime(2026, 6, 1) + timedelta(days=weekday)
    p = params or A5_PARAMS
    stats = {'merged_assets': merged,
             'stages': {'chronicle': {'event_type_counts': {
                 'asset_created': created, 'asset_removed': removed,
                 'asset_readded': readded}}}}
    return RunEvalContext(
        generation_run_id='grid-1', run_started_at=run_started,
        current_stats=stats, rule={'slug': 'x', 'threshold': p},
        params=p, settings=MagicMock(), repo=repo)


def test_a5_quiet_baseline_silent():
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_a5_history()))
    assert IdentityChurnDetector().evaluate(_a5_ctx(repo)) == []


def test_a5_merged_spike_fires_only_merged():
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_a5_history()))
    f = IdentityChurnDetector().evaluate(_a5_ctx(repo, merged=500))
    assert [x.dimension_key for x in f] == ['merged']
    assert f[0].kind == 'event' and f[0].alert_type == 'data_quality'
    assert f[0].evidence['observed'] == 500 and f[0].evidence['sub_signal'] == 'merged'


def test_a5_readded_spike_fires_only_readded():
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_a5_history()))
    f = IdentityChurnDetector().evaluate(_a5_ctx(repo, readded=800))
    assert [x.dimension_key for x in f] == ['readded']
    assert f[0].evidence['observed'] == 800


def test_a5_remint_requires_both_created_and_removed_spike():
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_a5_history()))
    f = IdentityChurnDetector().evaluate(_a5_ctx(repo, created=600, removed=550))
    assert [x.dimension_key for x in f] == ['remint']
    ev = f[0].evidence
    assert ev['created']['observed'] == 600 and ev['removed']['observed'] == 550
    # internal incident refs are stripped from user-facing copy (Task 5b)
    assert '#802' not in f[0].description and 'Proxmox' not in f[0].description
    assert 'over-merge' not in f[0].description
    # Locks the evidence['technical'] contract: created/removed stay plain facts
    # (no stat keys); median/z live only under technical (review fix for the leak
    # commit 2c88c94a left behind).
    assert ev['created'] == {'observed': 600} and ev['removed'] == {'observed': 550}
    assert 'created_z' in ev['technical'] and 'removed_z' in ev['technical']
    assert 'created_median' in ev['technical'] and 'removed_median' in ev['technical']


def test_a5_created_only_spike_does_not_fire_remint():
    # A created-only spike is A1's job — without a matching removed spike the
    # re-mint signature must stay silent.
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_a5_history()))
    assert IdentityChurnDetector().evaluate(_a5_ctx(repo, created=600, removed=5)) == []


def test_a5_merged_below_min_count_silent():
    # 40 merged with a huge z is still below the min_count floor (50).
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_a5_history()))
    assert IdentityChurnDetector().evaluate(_a5_ctx(repo, merged=40)) == []


def test_a5_learning_mode_silent():
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_a5_history(n=2)))
    assert IdentityChurnDetector().evaluate(_a5_ctx(repo, merged=500)) == []


def test_a5_readded_spike_attaches_capped_sample():
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_a5_history()))
    repo.get_event_asset_ids.return_value = ['a3', 'a4']
    f = IdentityChurnDetector().evaluate(_a5_ctx(repo, readded=800))
    assert [x.dimension_key for x in f] == ['readded']
    assert f[0].sample_asset_ids == ['a3', 'a4']
    repo.get_event_asset_ids.assert_called_once_with('grid-1', 'asset_readded', 25)


def test_a5_merged_spike_has_no_sample():
    # design ruling: only the re-added sub-signal samples; merged stays number-only.
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_a5_history()))
    f = IdentityChurnDetector().evaluate(_a5_ctx(repo, merged=500))
    assert [x.dimension_key for x in f] == ['merged']
    assert f[0].sample_asset_ids == []
    repo.get_event_asset_ids.assert_not_called()


def test_a5_remint_has_no_sample():
    # design ruling: remint (_remint, not _spike) stays number-only.
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_a5_history()))
    f = IdentityChurnDetector().evaluate(_a5_ctx(repo, created=600, removed=550))
    assert [x.dimension_key for x in f] == ['remint']
    assert f[0].sample_asset_ids == []
    repo.get_event_asset_ids.assert_not_called()


# --------------------------- A6 ConfidenceShift ---------------------------

A6_PARAMS = {'z_medium': 3, 'z_high': 5, 'z_critical': 8, 'min_pp': 5,
             'window_runs': 30, 'min_samples': 8}


def _a6_history(n=32, low=100, medium=400, high=500,
                triggered='schedule', start=datetime(2026, 4, 6)):
    """~10% low-confidence share baseline with mild jitter."""
    rows = []
    for i in range(n):
        j = (i % 5) - 2
        rows.append({
            'started_at': start + timedelta(days=i), 'triggered_by': triggered,
            'stats': {'confidence_histogram': {'low': low + j, 'medium': medium,
                                               'high': high}}})
    return rows


def _a6_history_no_stat(n=32, triggered='schedule', start=datetime(2026, 4, 6)):
    return [{'started_at': start + timedelta(days=i), 'triggered_by': triggered,
             'stats': {'chronicle_events': {'asset_created': 5}}} for i in range(n)]


def _a6_ctx(repo, current_hist, weekday=1, params=None):
    run_started = datetime(2026, 6, 1) + timedelta(days=weekday)
    p = params or A6_PARAMS
    repo.get_confidence_boundaries.return_value = (0.45, 0.70)
    repo.get_confidence_histogram.return_value = current_hist
    return RunEvalContext(
        generation_run_id='grid-1', run_started_at=run_started,
        current_stats={}, rule={'slug': 'x', 'threshold': p},
        params=p, settings=MagicMock(), repo=repo)


def test_a6_fires_on_degradation_as_condition():
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_a6_history()))
    # current low-share ~30% vs baseline ~10% -> +20pp, high z
    ctx = _a6_ctx(repo, {'low': 300, 'medium': 350, 'high': 350})
    f = ConfidenceShiftDetector().evaluate(ctx)
    assert len(f) == 1 and f[0].kind == 'condition'
    assert f[0].dimension_key == 'low-confidence' and f[0].alert_type == 'data_quality'
    assert f[0].evidence['delta_pp'] >= 5 and f[0].evidence['histogram']['low'] == 300


def test_a6_resolve_shape_when_recovered():
    # Back in band (~10%) -> no finding; the service resolves the active condition.
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_a6_history()))
    ctx = _a6_ctx(repo, {'low': 100, 'medium': 400, 'high': 500})
    assert ConfidenceShiftDetector().evaluate(ctx) == []


def test_a6_learning_when_history_lacks_stat():
    # confidence_histogram absent from history -> low-share series all None ->
    # learning -> silent even though the current share is high.
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_a6_history_no_stat()))
    ctx = _a6_ctx(repo, {'low': 500, 'medium': 300, 'high': 200})
    assert ConfidenceShiftDetector().evaluate(ctx) == []


# --------------------------- de-jargon copy scan (Task 5b) ---------------------------

_JARGON = ('z=', 'MAD', 'percentile', '#802', 'Proxmox', 'over-merge')


def _assert_plain(finding):
    """Title + description + narrative must carry none of the stats/internal-ref jargon."""
    blob = '\n'.join([finding.title, finding.description or '',
                      finding.evidence.get('narrative', '')])
    for token in _JARGON:
        assert token not in blob, f"jargon {token!r} leaked into copy: {blob!r}"


def test_volume_family_copy_is_plain_language():
    # mass_new: baseline (mode, MAD, z=) jargon -> plain "far above the usual ~N"
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(
        _history([200, 205, 210, 195, 202, 208, 199, 204] * 4)))
    mass = MassNewAssetsDetector().evaluate(_ctx(repo, created_now=1403, weekday=0))[0]
    _assert_plain(mass)
    assert 'far above the usual' in mass.evidence['narrative']
    assert '(baseline' not in mass.title              # stat parenthetical stripped
    assert '1403' in mass.title                       # observed count stays (a fact)

    # remint: the worst offender (#802 / Proxmox / over-merge signature)
    repo2 = MagicMock()
    repo2.get_recent_run_stats.return_value = list(reversed(_a5_history()))
    remint = IdentityChurnDetector().evaluate(_a5_ctx(repo2, created=600, removed=550))[0]
    _assert_plain(remint)

    # source_volume drop -> "well below the usual ~N"
    hist = _vol_history([[('SentinelOne', 25000 + (i % 5) * 40)] for i in range(30)])
    repo3 = MagicMock()
    repo3.get_recent_run_stats.return_value = list(reversed(hist))
    sv = SourceVolumeDetector().evaluate(_a3_ctx(
        repo3, [{'integration_name': 'SentinelOne', 'unique_assets': 15000,
                 'success': True}], weekday=1))[0]
    _assert_plain(sv)
    assert 'well below the usual' in sv.evidence['narrative']
    assert '(baseline' not in sv.title

    # confidence_shift
    repo4 = MagicMock()
    repo4.get_recent_run_stats.return_value = list(reversed(_a6_history()))
    conf = ConfidenceShiftDetector().evaluate(
        _a6_ctx(repo4, {'low': 300, 'medium': 350, 'high': 350}))[0]
    _assert_plain(conf)
    assert '(baseline' not in conf.title


def test_a4_systemic_title_humanizes_raw_column_name():
    # 'data_source' is a raw DB column — the systemic title/narrative must humanize it.
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_history_attr([200] * 30)))
    repo.get_attribute_breakdown.return_value = [{'attr': 'data_source', 'n': 46800}]
    repo.get_scattered_event_count.return_value = 0
    ctx = _a4_ctx(repo, attr_updated=47000, total_assets=73000, weekday=1)
    sysf = [x for x in AttributeChurnDetector().evaluate(ctx)
            if x.dimension_key.startswith('systemic:')][0]
    assert 'data_source' not in sysf.title and 'data source' in sysf.title
    assert 'data_source' not in sysf.description and 'data source' in sysf.description
    assert sysf.dimension_key == 'systemic:data_source'    # fingerprint key stays raw


# --------------------------- subject_type='metric' (Task 2) ---------------------------
# These are count/rate detectors, not per-asset ones — their alerts must render
# summary-only (no empty "Involved Assets" grid). Covers all 11 AnomalyFinding(...)
# constructor sites in this module, including every source_volume routing (design
# ruling: source_volume is metric-subject, NOT integration).

def test_a1_mass_new_assets_is_metric_subject():
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(
        _history([200, 205, 210, 195, 202, 208, 199, 204] * 4)))
    f = MassNewAssetsDetector().evaluate(_ctx(repo, created_now=1403, weekday=0))
    assert f and all(x.subject_type == 'metric' for x in f)


def test_a2_mass_missing_is_metric_subject():
    values = [18, 22, 20, 19, 21, 20, 23, 17] * 4
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_missing_history(values)))
    repo.get_missing_onset_count.return_value = 400
    f = MassMissingDetector().evaluate(_ctx(repo, created_now=0, weekday=1))
    assert f and all(x.subject_type == 'metric' for x in f)


def test_a3_source_volume_is_metric_subject_all_routings():
    # _failed
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(
        reversed(_vol_history([[('CrowdStrike', 8000)] for _ in range(30)])))
    ctx = _a3_ctx(repo, [{'integration_name': 'CrowdStrike', 'unique_assets': 0,
                          'success': False, 'error': 'connector timeout'}], weekday=1)
    f = SourceVolumeDetector().evaluate(ctx)
    assert f and all(x.subject_type == 'metric' for x in f)

    # _volume
    hist = _vol_history([[('SentinelOne', 25000 + (i % 5) * 40)] for i in range(30)])
    repo2 = MagicMock()
    repo2.get_recent_run_stats.return_value = list(reversed(hist))
    ctx2 = _a3_ctx(repo2, [{'integration_name': 'SentinelOne', 'unique_assets': 15000,
                           'success': True}], weekday=1)
    f2 = SourceVolumeDetector().evaluate(ctx2)
    assert f2 and all(x.subject_type == 'metric' for x in f2)

    # _missing (below aggregation threshold)
    repo3 = MagicMock()
    repo3.get_recent_run_stats.return_value = list(reversed(
        _vol_history([[('SentinelOne', 25000), ('Nessus', 3000)] for _ in range(30)])))
    ctx3 = _a3_ctx(repo3, [{'integration_name': 'SentinelOne', 'unique_assets': 25000,
                           'success': True}], weekday=1)
    f3 = SourceVolumeDetector().evaluate(ctx3)
    assert f3 and all(x.subject_type == 'metric' for x in f3)

    # _collapse_missing (aggregate, above threshold)
    sources = [(f'Src{i:02d}', 1000) for i in range(15)]
    repo4 = MagicMock()
    repo4.get_recent_run_stats.return_value = list(
        reversed(_vol_history([sources for _ in range(30)])))
    ctx4 = _a3_ctx(repo4, [{'integration_name': 'Src00', 'unique_assets': 1000,
                           'success': True}], weekday=1)
    f4 = SourceVolumeDetector().evaluate(ctx4)
    assert f4 and all(x.subject_type == 'metric' for x in f4)


def test_a4_attribute_churn_is_metric_subject_both_routings():
    # _systemic
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_history_attr([200] * 30)))
    repo.get_attribute_breakdown.return_value = [
        {'attr': 'data_source', 'n': 46800}, {'attr': 'last_seen', 'n': 200}]
    repo.get_scattered_event_count.return_value = 200
    ctx = _a4_ctx(repo, attr_updated=47000, total_assets=73000, weekday=1)
    f = AttributeChurnDetector().evaluate(ctx)
    assert f and all(x.subject_type == 'metric' for x in f)

    # _scattered
    repo2 = MagicMock()
    repo2.get_recent_run_stats.return_value = list(reversed(_history_attr([200] * 30)))
    repo2.get_attribute_breakdown.return_value = [
        {'attr': 'os', 'n': 900}, {'attr': 'ip', 'n': 850}, {'attr': 'model', 'n': 800}]
    repo2.get_scattered_event_count.return_value = 5000
    ctx2 = _a4_ctx(repo2, attr_updated=5000, total_assets=73000, weekday=1)
    f2 = AttributeChurnDetector().evaluate(ctx2)
    assert f2 and all(x.subject_type == 'metric' for x in f2)


def test_a5_identity_churn_is_metric_subject_all_signals():
    # _spike (merged)
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_a5_history()))
    f = IdentityChurnDetector().evaluate(_a5_ctx(repo, merged=500))
    assert f and all(x.subject_type == 'metric' for x in f)

    # _spike (readded)
    repo2 = MagicMock()
    repo2.get_recent_run_stats.return_value = list(reversed(_a5_history()))
    f2 = IdentityChurnDetector().evaluate(_a5_ctx(repo2, readded=800))
    assert f2 and all(x.subject_type == 'metric' for x in f2)

    # _remint
    repo3 = MagicMock()
    repo3.get_recent_run_stats.return_value = list(reversed(_a5_history()))
    f3 = IdentityChurnDetector().evaluate(_a5_ctx(repo3, created=600, removed=550))
    assert f3 and all(x.subject_type == 'metric' for x in f3)


def test_a6_confidence_shift_is_metric_subject():
    repo = MagicMock()
    repo.get_recent_run_stats.return_value = list(reversed(_a6_history()))
    ctx = _a6_ctx(repo, {'low': 300, 'medium': 350, 'high': 350})
    f = ConfidenceShiftDetector().evaluate(ctx)
    assert f and all(x.subject_type == 'metric' for x in f)
