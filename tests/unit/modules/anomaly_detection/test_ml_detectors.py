# SPDX-License-Identifier: AGPL-3.0-or-later
"""ML anomaly detectors — MlNewcomerDetector (fit fleet, score this run's created
assets) and MlBehaviorDetector (fit == score over all assets' recent windows).

These pin the behaviour the ML rollout depends on: a planted weird device /
thrasher is flagged with an explainable payload, a fleet-typical / quiet asset is
not, the learning gate holds below min_fit_rows, and an over-budget fit is skipped.
The service turns their findings into alerts through the normal path (see
test_service.py)."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.modules.anomaly_detection.detectors import DETECTOR_CLASSES
from src.modules.anomaly_detection.detectors.ml import (
    MlBehaviorDetector,
    MlNewcomerDetector,
)
from src.modules.anomaly_detection.domain import RunEvalContext

NEWCOMER_PARAMS = {'pct_medium': 0.5, 'pct_high': 0.1, 'min_fit_rows': 10,
                   'fit_budget_seconds': 10, 'top_k_categories': 15, 'max_fit_rows': 25000}
BEHAVIOR_PARAMS = {'pct_medium': 0.5, 'pct_high': 0.1, 'min_fit_rows': 10,
                   'fit_budget_seconds': 10, 'window_runs': 30}


def _ctx(repo, params):
    return RunEvalContext(
        generation_run_id='grid-1', run_started_at=datetime(2026, 6, 1),
        current_stats={}, rule={'id': 'r', 'slug': 'x', 'threshold': params},
        params=params, settings=MagicMock(), repo=repo)


# --------------------------- newcomer fixtures ------------------------------

def _asset(cat, os, mfr, src, dsc, conf):
    return {'category': cat, 'os_normalized': os,
            'manufacturer': [mfr] if mfr else [],
            'data_source': [src] if src else [],
            'data_source_category': [dsc] if dsc else [], 'confidence': conf}


def _fleet():
    rows = []
    rows += [_asset('Workstation', 'Windows 10', 'Dell', 'AD', 'directory', 0.9)] * 90
    rows += [_asset('Server', 'Linux', 'HP', 'SentinelOne', 'endpoint security', 0.8)] * 8
    rows += [_asset('Printer', 'embedded', 'HP', 'SNMP', 'network', 0.5)] * 2
    return rows


def _created(asset_id, cat, os, mfr, src, dsc, conf):
    row = _asset(cat, os, mfr, src, dsc, conf)
    row['asset_id'] = asset_id
    row['ip'] = []
    return row


def test_ml_newcomer_flags_planted_weird_asset_with_contributions():
    repo = MagicMock()
    repo.get_ml_newcomer_fit_rows.return_value = _fleet()
    repo.get_created_assets.return_value = [
        _created('normal-1', 'Workstation', 'Windows 10', 'Dell', 'AD', 'directory', 0.9),
        _created('weird-1', 'HVAC Controller', 'VxWorks', 'Siemens', 'Modbus', 'ot', 0.2)]

    findings = MlNewcomerDetector().evaluate(_ctx(repo, NEWCOMER_PARAMS))

    ids = {f.dimension_key for f in findings}
    assert 'weird-1' in ids                      # a never-seen combo is an outlier
    assert 'normal-1' not in ids                 # the fleet-typical newcomer is not
    wf = next(f for f in findings if f.dimension_key == 'weird-1')
    assert wf.kind == 'event' and wf.alert_type == 'security'
    assert wf.asset_ids == ['weird-1']
    assert wf.severity == 'high'                  # above the whole fleet -> top 0.1%
    assert wf.evidence['technical']['self_percentile'] >= 99.9
    named = {c['feature'] for c in wf.evidence['contributions']}
    assert named & {'category', 'os', 'manufacturer', 'source_categories'}
    repo.get_ml_newcomer_fit_rows.assert_called_once_with(datetime(2026, 6, 1), 25000)


def test_ml_newcomer_quiet_when_newcomers_match_fleet():
    repo = MagicMock()
    repo.get_ml_newcomer_fit_rows.return_value = _fleet()
    repo.get_created_assets.return_value = [
        _created(f'n{i}', 'Workstation', 'Windows 10', 'Dell', 'AD', 'directory', 0.9)
        for i in range(5)]
    assert MlNewcomerDetector().evaluate(_ctx(repo, NEWCOMER_PARAMS)) == []


def test_ml_newcomer_learning_gate_skips_before_scoring():
    repo = MagicMock()
    repo.get_ml_newcomer_fit_rows.return_value = _fleet()[:5]     # 5 < min_fit_rows 10
    assert MlNewcomerDetector().evaluate(_ctx(repo, NEWCOMER_PARAMS)) == []
    repo.get_created_assets.assert_not_called()                  # short-circuits


def test_ml_newcomer_skips_when_fit_exceeds_budget(monkeypatch):
    # Deterministic budget test: simulate a 100 s fit against a real 5 s budget by
    # driving the detector's clock, rather than relying on a 0 s wall-clock race.
    import src.modules.anomaly_detection.detectors.ml as ml_mod
    ticks = iter([0.0, 100.0])
    monkeypatch.setattr(ml_mod.time, 'perf_counter', lambda: next(ticks))
    repo = MagicMock()
    repo.get_ml_newcomer_fit_rows.return_value = _fleet()
    repo.get_created_assets.return_value = [
        _created('weird-1', 'HVAC Controller', 'VxWorks', 'Siemens', 'Modbus', 'ot', 0.2)]
    params = {**NEWCOMER_PARAMS, 'fit_budget_seconds': 5}        # 100 s fit > 5 s budget
    assert MlNewcomerDetector().evaluate(_ctx(repo, params)) == []


def test_newcomer_features_dedups_source_categories_and_normalizes_nan_confidence():
    from src.modules.anomaly_detection.detectors.ml import newcomer_features
    # Duplicate source categories (24% of the real fleet) must collapse to ONE token
    # — otherwise a normal asset with two same-category sources reads as a rare mix.
    dup = newcomer_features({
        'category': 'Server', 'os_normalized': 'Linux', 'manufacturer': ['HP'],
        'data_source': ['a', 'b'],
        'data_source_category': ['endpoint security', 'endpoint security'],
        'confidence': 0.8})
    assert dup['source_categories'] == 'endpoint security'
    # NaN confidence is normalized to missing (None), never echoed as a raw NaN that
    # would break the jsonb stats write.
    nan_conf = newcomer_features({
        'category': 'X', 'os_normalized': 'Y', 'manufacturer': ['Z'],
        'data_source': ['a'], 'data_source_category': ['net'],
        'confidence': float('nan')})
    assert nan_conf['confidence'] is None


# --------------------------- behavior fixtures ------------------------------

def _behavior_rows():
    """995 quiet assets + 5 planted thrashers (0.5% of the fleet, so they clear the
    top-0.5% pct_medium gate) — the shape B-family thrash detection also targets."""
    rows = [{'asset_id': f'quiet{i}', 'runs_active': 1, 'distinct_attrs': 1, 'event_count': 1}
            for i in range(995)]
    rows += [{'asset_id': f'thrash{i}', 'runs_active': 30, 'distinct_attrs': 12,
              'event_count': 200} for i in range(5)]
    return rows


def test_ml_behavior_flags_planted_thrashers():
    repo = MagicMock()
    repo.get_ml_behavior_rows.return_value = _behavior_rows()

    findings = MlBehaviorDetector().evaluate(_ctx(repo, BEHAVIOR_PARAMS))

    ids = {f.dimension_key for f in findings}
    assert 'thrash0' in ids
    assert not any(i.startswith('quiet') for i in ids)   # the quiet mode is not flagged
    tf = next(f for f in findings if f.dimension_key == 'thrash0')
    assert tf.kind == 'condition' and tf.alert_type == 'data_quality'
    assert tf.evidence['technical']['self_percentile'] >= 99.5
    assert tf.evidence['runs_active'] == 30
    repo.get_ml_behavior_rows.assert_called_once_with(30)


def test_ml_behavior_finding_title_uses_label_token_not_raw_asset_id():
    """Task 4: the detector must never bake the raw asset id into alert copy —
    it emits ASSET_LABEL_TOKEN for the service's label pass to substitute."""
    from src.modules.anomaly_detection.domain import ASSET_LABEL_TOKEN
    repo = MagicMock()
    repo.get_ml_behavior_rows.return_value = _behavior_rows()

    findings = MlBehaviorDetector().evaluate(_ctx(repo, BEHAVIOR_PARAMS))
    tf = next(f for f in findings if f.dimension_key == 'thrash0')

    assert tf.title == f"Unusual change behavior — {ASSET_LABEL_TOKEN}"
    assert 'thrash0' not in tf.title                          # no raw id in the title
    assert tf.asset_ids == ['thrash0']
    assert tf.evidence['asset_id'] == 'thrash0'                # real id kept as the map key
    assert ASSET_LABEL_TOKEN in tf.evidence['narrative']
    assert 'thrash0' not in tf.evidence['narrative']           # no raw id in the narrative either
    assert ASSET_LABEL_TOKEN in tf.description
    # Task 5b: percentile / "top X%" jargon stripped from the narrative
    assert 'percentile' not in tf.evidence['narrative']
    assert 'top ' not in tf.evidence['narrative']


def _behavior_rows_high_cardinality():
    """Mode-dominated but HIGH-cardinality: 2988 quiet + 12 distinct-valued thrashers
    (>n_bins distinct values per feature). This routes into the numeric density path,
    where quantile edges collapse onto the mode and — before the freq-fallback fix —
    every asset scored 0 and no thrasher was ever flagged."""
    rows = [{'asset_id': f'quiet{i}', 'runs_active': 1, 'distinct_attrs': 1,
             'event_count': 1} for i in range(2988)]
    rows += [{'asset_id': f'thrash{i}', 'runs_active': 20 + i, 'distinct_attrs': 20 + i,
              'event_count': 100 + i} for i in range(12)]
    return rows


def test_ml_behavior_flags_thrashers_on_high_cardinality_mode_dominated_data():
    repo = MagicMock()
    repo.get_ml_behavior_rows.return_value = _behavior_rows_high_cardinality()
    findings = MlBehaviorDetector().evaluate(_ctx(repo, BEHAVIOR_PARAMS))
    ids = {f.dimension_key for f in findings}
    assert any(i.startswith('thrash') for i in ids)          # tail is flagged (was 0 pre-fix)
    assert not any(i.startswith('quiet') for i in ids)       # the dominant mode is not
    assert len(findings) == 12


def test_ml_behavior_uniform_fleet_yields_no_outliers():
    # Every asset behaves identically -> nobody is an outlier (strictly-less
    # percentile keeps the whole tied population off the alert surface).
    repo = MagicMock()
    repo.get_ml_behavior_rows.return_value = [
        {'asset_id': f'a{i}', 'runs_active': 1, 'distinct_attrs': 1, 'event_count': 1}
        for i in range(50)]
    assert MlBehaviorDetector().evaluate(_ctx(repo, BEHAVIOR_PARAMS)) == []


def test_ml_behavior_learning_gate():
    repo = MagicMock()
    repo.get_ml_behavior_rows.return_value = [
        {'asset_id': f'a{i}', 'runs_active': 1, 'distinct_attrs': 1, 'event_count': 1}
        for i in range(5)]                                       # 5 < min_fit_rows 10
    assert MlBehaviorDetector().evaluate(_ctx(repo, BEHAVIOR_PARAMS)) == []


def test_both_ml_detectors_registered():
    assert DETECTOR_CLASSES['ml_newcomer'] is MlNewcomerDetector
    assert DETECTOR_CLASSES['ml_behavior'] is MlBehaviorDetector


def test_service_runs_real_ml_newcomer_and_fires_alerts():
    """End-to-end guarantee (#902): the REAL ml_newcomer detector, dispatched by
    the service under a normal rule (no shadow mode), flows through aggregate→apply
    and CREATES an alert for the planted outlier — proven with the real detector +
    registry, not a stub. (Before #902 this recorded shadow stats and fired none.)"""
    from src.modules.anomaly_detection.service import AnomalyDetectionService
    db = MagicMock()
    db.execute_query.return_value = []
    alerts_repo = MagicMock()
    alerts_repo.create_alert.return_value = {
        'id': 'alert-1', 'created_at': datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc)}
    svc = AnomalyDetectionService(db, alerts_repo)      # real DETECTOR_CLASSES registry
    svc.repo = MagicMock()
    svc.repo.get_enabled_anomaly_rules.return_value = [{
        'id': 'r1', 'slug': 'anomaly-ml-newcomer', 'detector_key': 'ml_newcomer',
        'threshold': NEWCOMER_PARAMS, 'silence_duration': None}]
    svc.repo.get_ml_newcomer_fit_rows.return_value = _fleet()
    svc.repo.get_created_assets.return_value = [
        _created('weird-1', 'HVAC Controller', 'VxWorks', 'Siemens', 'Modbus', 'ot', 0.2)]
    svc.repo.get_recent_alert_fingerprints.return_value = set()
    svc.repo.get_active_anomaly_alerts.return_value = []
    # ml_newcomer now emits ASSET_LABEL_TOKEN (Task 5b); the service's label pass
    # runs on every finding, so get_asset_labels must return a real dict.
    svc.repo.get_asset_labels.return_value = {}

    summary = svc.evaluate_run('grid-1', datetime(2026, 6, 1), {})

    alerts_repo.create_alert.assert_called_once()       # the outlier fires an alert
    assert summary['alerts_created'] >= 1
    assert 'shadow' not in summary                       # shadow machinery is gone
    kwargs = alerts_repo.create_alert.call_args.kwargs
    assert kwargs['fingerprint'].startswith('anomaly:ml_newcomer:weird-1:')


def test_ml_behavior_findings_are_condition_kind():
    """PR #825 review finding: ml_behavior scores a rolling-window population, so
    a persistently anomalous asset reappears every run. Event-kind fingerprints
    are date-suffixed by the service and would re-fire a new alert daily;
    condition-kind updates one alert and auto-resolves (the B1b/B2 pattern this
    detector generalizes)."""
    repo = MagicMock()
    repo.get_ml_behavior_rows.return_value = _behavior_rows()
    findings = MlBehaviorDetector().evaluate(_ctx(repo, BEHAVIOR_PARAMS))
    assert findings, 'fixture must produce at least one outlier'
    assert all(f.kind == 'condition' for f in findings)


def test_ml_behavior_same_asset_updates_one_alert_across_runs():
    """Cross-run dedup: the same behavioural outlier on two consecutive runs must
    create ONE alert then update it — never a second row. (Would fail with
    kind='event': date-suffixed fingerprints re-fire.)"""
    from src.modules.anomaly_detection.service import AnomalyDetectionService
    db = MagicMock()
    db.execute_query.return_value = []
    alerts_repo = MagicMock()
    alerts_repo.create_alert.return_value = {
        'id': 'alert-1', 'created_at': datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc)}
    svc = AnomalyDetectionService(db, alerts_repo)      # real registry
    svc.repo = MagicMock()
    svc.repo.get_enabled_anomaly_rules.return_value = [{
        'id': 'r2', 'slug': 'anomaly-ml-behavior', 'detector_key': 'ml_behavior',
        'threshold': BEHAVIOR_PARAMS, 'silence_duration': None}]
    svc.repo.get_ml_behavior_rows.return_value = _behavior_rows()
    # ml_behavior's title/narrative now carry ASSET_LABEL_TOKEN (Task 4); the
    # service's label pass calls this on every run, so it must return a real dict
    # (an unconfigured MagicMock would fail the str.replace() substitution).
    svc.repo.get_asset_labels.return_value = {}

    # run 1: no active alerts yet -> create
    svc.repo.get_active_anomaly_alerts.return_value = []
    svc.repo.get_recent_alert_fingerprints.return_value = set()
    s1 = svc.evaluate_run('grid-1', datetime(2026, 6, 1), {})
    assert s1['alerts_created'] >= 1
    fp = alerts_repo.create_alert.call_args.kwargs.get('fingerprint') or \
        alerts_repo.create_alert.call_args.args[-1]
    assert str(fp).startswith('anomaly-cond:ml_behavior:')

    # run 2 (next day): ALL of run 1's condition alerts are active -> update, no new rows
    created_before = alerts_repo.create_alert.call_count
    all_fps = [c.kwargs.get('fingerprint') for c in alerts_repo.create_alert.call_args_list]
    assert all(str(f).startswith('anomaly-cond:ml_behavior:') for f in all_fps)
    svc.repo.get_active_anomaly_alerts.return_value = [
        {'id': f'alert-{i}', 'fingerprint': f, 'created_at': datetime(2026, 6, 1)}
        for i, f in enumerate(all_fps)]
    s2 = svc.evaluate_run('grid-2', datetime(2026, 6, 2), {})
    assert alerts_repo.create_alert.call_count == created_before
    assert s2['alerts_updated'] >= 1 and s2['alerts_created'] == 0


def test_service_labels_ml_behavior_title_end_to_end():
    """Brief's Step 1, proven through the real pipeline: the REAL MlBehaviorDetector
    dispatched by the REAL service resolves a planted asset's label and substitutes
    it into the alert title actually sent to create_alert — never the raw id."""
    from src.modules.anomaly_detection.service import AnomalyDetectionService
    rows = [{'asset_id': f'quiet{i}', 'runs_active': 1, 'distinct_attrs': 1, 'event_count': 1}
            for i in range(995)]
    rows.append({'asset_id': 'thrash0', 'runs_active': 30, 'distinct_attrs': 12,
                 'event_count': 200})
    db = MagicMock()
    db.execute_query.return_value = []
    alerts_repo = MagicMock()
    alerts_repo.create_alert.return_value = {
        'id': 'alert-1', 'created_at': datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc)}
    svc = AnomalyDetectionService(db, alerts_repo)      # real registry
    svc.repo = MagicMock()
    svc.repo.get_enabled_anomaly_rules.return_value = [{
        'id': 'r2', 'slug': 'anomaly-ml-behavior', 'detector_key': 'ml_behavior',
        'threshold': BEHAVIOR_PARAMS, 'silence_duration': None}]
    svc.repo.get_ml_behavior_rows.return_value = rows
    svc.repo.get_active_anomaly_alerts.return_value = []
    svc.repo.get_recent_alert_fingerprints.return_value = set()
    svc.repo.get_asset_labels.return_value = {'thrash0': 'web-thrash-01'}

    svc.evaluate_run('grid-1', datetime(2026, 6, 1), {})

    alerts_repo.create_alert.assert_called_once()
    kwargs = alerts_repo.create_alert.call_args.kwargs
    assert kwargs['title'] == 'Unusual change behavior — web-thrash-01'
    assert 'thrash0' not in kwargs['title']
    assert kwargs['evidence']['label'] == 'web-thrash-01'
    assert kwargs['evidence']['labels'] == {'thrash0': 'web-thrash-01'}


def test_service_labels_ml_newcomer_title_end_to_end():
    """Task 5b: ml_newcomer now emits ASSET_LABEL_TOKEN + de-jargoned copy. The REAL
    service label pass resolves the token into the alert title/narrative — never the
    raw id — and no z/percentile/'top X%' jargon survives."""
    from src.modules.anomaly_detection.service import AnomalyDetectionService
    db = MagicMock()
    db.execute_query.return_value = []
    alerts_repo = MagicMock()
    alerts_repo.create_alert.return_value = {
        'id': 'alert-1', 'created_at': datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc)}
    svc = AnomalyDetectionService(db, alerts_repo)      # real registry
    svc.repo = MagicMock()
    svc.repo.get_enabled_anomaly_rules.return_value = [{
        'id': 'r1', 'slug': 'anomaly-ml-newcomer', 'detector_key': 'ml_newcomer',
        'threshold': NEWCOMER_PARAMS, 'silence_duration': None}]
    svc.repo.get_ml_newcomer_fit_rows.return_value = _fleet()
    svc.repo.get_created_assets.return_value = [
        _created('weird-1', 'HVAC Controller', 'VxWorks', 'Siemens', 'Modbus', 'ot', 0.2)]
    svc.repo.get_recent_alert_fingerprints.return_value = set()
    svc.repo.get_active_anomaly_alerts.return_value = []
    svc.repo.get_asset_labels.return_value = {'weird-1': 'hvac-ctrl-01'}

    svc.evaluate_run('grid-1', datetime(2026, 6, 1), {})

    alerts_repo.create_alert.assert_called_once()
    kwargs = alerts_repo.create_alert.call_args.kwargs
    assert kwargs['title'] == 'Unusual new asset — hvac-ctrl-01'
    assert 'weird-1' not in kwargs['title']
    narrative = kwargs['evidence']['narrative']
    assert 'hvac-ctrl-01' in narrative and 'weird-1' not in narrative
    for token in ('z=', 'percentile', 'MAD', 'top '):
        assert token not in kwargs['title'] and token not in narrative
