# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.modules.anomaly_detection.domain import ASSET_LABEL_TOKEN, AnomalyFinding
from src.modules.anomaly_detection.service import AnomalyDetectionService

RUN = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)


def _detector(key, findings=None, raises=False):
    fs = list(findings or [])

    class _D:
        pass

    _D.key = key

    def evaluate(self, ctx):
        if raises:
            raise RuntimeError('boom')
        return list(fs)

    _D.evaluate = evaluate
    return _D


def _rule(rid, detector_key, silence=None, threshold=None):
    return {'id': rid, 'slug': f'rule-{rid}', 'detector_key': detector_key,
            'threshold': threshold or {}, 'silence_duration': silence}


def _service(detector_classes, rules, alerts_repo=None):
    db = MagicMock()
    db.execute_query.return_value = []            # load_settings -> code defaults
    alerts_repo = alerts_repo or MagicMock()
    if alerts_repo.create_alert.return_value is None or isinstance(
            alerts_repo.create_alert.return_value, MagicMock):
        alerts_repo.create_alert.return_value = {
            'id': 'new-alert',
            'created_at': datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc),
        }
    svc = AnomalyDetectionService(db, alerts_repo, detector_classes=detector_classes)
    svc.repo = MagicMock()
    svc.repo.get_enabled_anomaly_rules.return_value = rules
    svc.repo.get_recent_alert_fingerprints.return_value = set()
    svc.repo.get_active_anomaly_alerts.return_value = []
    return svc, alerts_repo


def test_dispatch_only_enabled_rules_and_isolated_failures():
    fa = AnomalyFinding('det_a', '', 'event', 'high', 'A', 'descA', {'x': 1})
    svc, alerts_repo = _service(
        {'det_a': _detector('det_a', [fa]), 'det_b': _detector('det_b', raises=True)},
        [_rule('ra', 'det_a'), _rule('rb', 'det_b')])
    summary = svc.evaluate_run('grid-1', RUN, {})
    assert alerts_repo.create_alert.call_count == 1       # A created; B failed but isolated
    assert 'rule-rb' in summary['errors'] and summary['alerts_created'] == 1
    assert summary['rules_evaluated'] == 1


def test_unknown_detector_key_recorded():
    svc, alerts_repo = _service({}, [_rule('ra', 'nope')])
    summary = svc.evaluate_run('g', RUN, {})
    assert summary['errors']['rule-ra'] == 'unknown detector_key'
    alerts_repo.create_alert.assert_not_called()


def test_event_fingerprint_includes_date_and_dedups_within_silence():
    f = AnomalyFinding('det_a', '', 'event', 'high', 'A', 'd', {'x': 1})
    svc, alerts_repo = _service({'det_a': _detector('det_a', [f])}, [_rule('ra', 'det_a', silence=60)])
    svc.repo.get_recent_alert_fingerprints.return_value = {'anomaly:det_a::2026-06-01'}
    summary = svc.evaluate_run('g', RUN, {})
    alerts_repo.create_alert.assert_not_called()
    assert summary['alerts_created'] == 0


def test_event_fingerprint_and_evidence_passed_to_create_alert():
    f = AnomalyFinding('det_a', '', 'event', 'high', 'T', 'D',
                       {'observed': 1403, 'score': {'z': 29.8}})
    svc, alerts_repo = _service({'det_a': _detector('det_a', [f])}, [_rule('ra', 'det_a')])
    svc.evaluate_run('g', RUN, {})
    kwargs = alerts_repo.create_alert.call_args.kwargs
    assert kwargs['evidence'] == {'observed': 1403, 'score': {'z': 29.8}}
    assert kwargs['fingerprint'] == 'anomaly:det_a::2026-06-01'
    assert kwargs['source'] == 'system'


def test_condition_creates_then_updates_then_resolves():
    cond = AnomalyFinding('source_volume', 'X:volume', 'condition', 'high', 't', 'd',
                          {'e': 1}, asset_ids=['a1'])
    fp = 'anomaly-cond:source_volume:X:volume'
    rule = _rule('ra', 'source_volume')

    # run1: nothing active -> create
    svc, alerts_repo = _service({'source_volume': _detector('source_volume', [cond])}, [rule])
    s1 = svc.evaluate_run('g', RUN, {})
    alerts_repo.create_alert.assert_called_once()
    assert s1['alerts_created'] == 1

    # run2: active with same fp -> update in place, no new row
    alerts_repo.reset_mock()
    svc.repo.get_active_anomaly_alerts.return_value = [
        {'id': 'c1', 'fingerprint': fp, 'created_at': None}]
    s2 = svc.evaluate_run('g', RUN, {})
    alerts_repo.create_alert.assert_not_called()
    alerts_repo.update_alert_assets.assert_called_once()
    alerts_repo.update_alert_evidence.assert_called_once()
    assert s2['alerts_updated'] == 1

    # run3: detector silent -> resolve the stale condition
    svc2, alerts_repo2 = _service({'source_volume': _detector('source_volume', [])}, [rule])
    svc2.repo.get_active_anomaly_alerts.return_value = [
        {'id': 'c1', 'fingerprint': fp, 'created_at': None}]
    s3 = svc2.evaluate_run('g', RUN, {})
    alerts_repo2.resolve_alert.assert_called_once_with('c1')
    assert s3['alerts_resolved'] == 1


def test_condition_resolve_only_touches_anomaly_cond_fingerprints():
    svc, alerts_repo = _service(
        {'source_volume': _detector('source_volume', [])}, [_rule('ra', 'source_volume')])
    svc.repo.get_active_anomaly_alerts.return_value = [
        {'id': 'p1', 'fingerprint': 'abc123', 'created_at': None}]   # prometheus-style
    svc.evaluate_run('g', RUN, {})
    alerts_repo.resolve_alert.assert_not_called()


def test_aggregation_collapses_per_entity_flood():
    findings = [AnomalyFinding('segment_outlier', f'a{i}', 'event', 'medium', 't', 'd',
                               {'i': i}, asset_ids=[f'a{i}']) for i in range(34)]
    svc, alerts_repo = _service(
        {'segment_outlier': _detector('segment_outlier', findings)},
        [_rule('ra', 'segment_outlier')])
    svc.evaluate_run('g', RUN, {})
    assert alerts_repo.create_alert.call_count == 1
    kwargs = alerts_repo.create_alert.call_args.kwargs
    assert '34' in kwargs['title']
    # Task 5b: friendly detector phrase, never the raw detector_key
    assert 'out-of-profile devices' in kwargs['title']
    assert 'segment_outlier' not in kwargs['title']
    assert kwargs['evidence']['aggregated'] is True and kwargs['evidence']['count'] == 34
    assert len(kwargs['asset_ids']) <= 25


def test_aggregation_not_triggered_below_threshold():
    findings = [AnomalyFinding('segment_outlier', f'a{i}', 'event', 'medium', 't', 'd',
                               {'i': i}, asset_ids=[f'a{i}']) for i in range(3)]
    svc, alerts_repo = _service(
        {'segment_outlier': _detector('segment_outlier', findings)},
        [_rule('ra', 'segment_outlier')])
    svc.evaluate_run('g', RUN, {})
    assert alerts_repo.create_alert.call_count == 3    # each fires individually


def test_aggregate_title_uses_friendly_name_and_hides_detector_key():
    findings = [AnomalyFinding('identity_churn', f'a{i}', 'event', 'medium', 't', 'd',
                               {'i': i}, asset_ids=[f'a{i}']) for i in range(12)]
    svc, alerts_repo = _service(
        {'identity_churn': _detector('identity_churn', findings)},
        [_rule('ra', 'identity_churn')])
    svc.evaluate_run('g', RUN, {})
    kwargs = alerts_repo.create_alert.call_args.kwargs
    assert kwargs['title'] == '12 identity churn events this run'
    assert 'identity_churn' not in kwargs['title']


def test_aggregate_title_falls_back_to_spaced_key_for_unknown_detector():
    findings = [AnomalyFinding('brand_new_detector', f'a{i}', 'event', 'low', 't', 'd',
                               {'i': i}, asset_ids=[f'a{i}']) for i in range(12)]
    svc, alerts_repo = _service(
        {'brand_new_detector': _detector('brand_new_detector', findings)},
        [_rule('ra', 'brand_new_detector')])
    svc.evaluate_run('g', RUN, {})
    kwargs = alerts_repo.create_alert.call_args.kwargs
    assert kwargs['title'] == '12 brand new detector this run'   # underscores spaced
    assert 'brand_new_detector' not in kwargs['title']


def test_aggregate_evidence_items_never_carry_raw_label_token():
    # Review fix: an aggregate alert's evidence['items'] are frozen snapshots of
    # each collapsed finding's evidence, taken inside _aggregate. If labeling runs
    # AFTER aggregation, only the synthesized aggregate finding gets a label pass —
    # the frozen items never do, and their narrative ships the literal
    # {{asset_label}} token to the UI's Technical details JSON. Labeling must run
    # BEFORE aggregation so every item is already substituted when it's snapshotted.
    findings = [
        AnomalyFinding(
            'ml_newcomer', f'a{i}', 'event', 'high',
            title=f"Unusual new asset — {ASSET_LABEL_TOKEN}",
            description=f"New asset {ASSET_LABEL_TOKEN} looks unlike the fleet.",
            evidence={'narrative': f"New asset {ASSET_LABEL_TOKEN} looks unlike the fleet."},
            alert_type='security', asset_ids=[f'a{i}'])
        for i in range(12)                     # > default aggregation_threshold (10)
    ]
    svc, alerts_repo = _service(
        {'ml_newcomer': _detector('ml_newcomer', findings)},
        [_rule('ra', 'ml_newcomer')])
    svc.repo.get_asset_labels.return_value = {f'a{i}': f'host-{i}' for i in range(12)}

    svc.evaluate_run('g', RUN, {})

    kwargs = alerts_repo.create_alert.call_args.kwargs
    assert kwargs['evidence']['aggregated'] is True and kwargs['evidence']['count'] == 12
    serialized = json.dumps(kwargs['evidence'])
    assert '{{' not in serialized and ASSET_LABEL_TOKEN not in serialized
    assert all('host-' in item['narrative'] for item in kwargs['evidence']['items'])


def test_create_uses_static_rule_description_and_keeps_narrative_in_evidence():
    # Task 5b: the anomaly alert's stored description is the STATIC rule description
    # (Task 6 makes those plain); the humanized narrative stays in evidence.
    plain = '340 new assets appeared this run — far above the usual ~200.'
    f = AnomalyFinding(
        detector_key='mass_new_assets', dimension_key='', kind='event', severity='high',
        title='Mass new assets: 340 created', description=plain,
        evidence={'narrative': plain})
    svc, alerts_repo = _service({}, [])
    rule = {'id': 'r1', 'slug': 's', 'threshold': {},
            'description': 'A large batch of new assets appeared in this run.'}
    svc._create(rule, f, 'fp-1', {'alerts_created': 0})
    kwargs = alerts_repo.create_alert.call_args.kwargs
    assert kwargs['description'] == 'A large batch of new assets appeared in this run.'
    assert kwargs['evidence']['narrative'] == plain


def test_create_falls_back_to_finding_description_when_rule_has_none():
    f = AnomalyFinding('det_a', '', 'event', 'high', 'T', 'narrative-desc', {'x': 1})
    svc, alerts_repo = _service({}, [])
    svc._create({'id': 'r1', 'slug': 's', 'threshold': {}}, f, 'fp-2', {'alerts_created': 0})
    kwargs = alerts_repo.create_alert.call_args.kwargs
    assert kwargs['description'] == 'narrative-desc'


# --- ML rules fire through the normal alert path (#902: shadow mode removed) ---

def test_ml_rule_fires_through_normal_alert_lifecycle():
    # An ML rule whose params no longer carry a `mode` key now flows through the
    # same aggregate→apply path as every other detector: a finding creates an
    # alert. (Before #902 a mode='shadow' rule recorded findings and created none.)
    f = AnomalyFinding('ml_newcomer', 'a1', 'event', 'high', 'T', 'D', {'x': 1},
                       asset_ids=['a1'])
    svc, alerts_repo = _service(
        {'ml_newcomer': _detector('ml_newcomer', [f])},
        [_rule('ra', 'ml_newcomer', threshold={'pct_medium': 0.5, 'pct_high': 0.1})])
    summary = svc.evaluate_run('g', RUN, {})
    alerts_repo.create_alert.assert_called_once()
    assert summary['alerts_created'] == 1
    assert summary['rules_evaluated'] == 1
    assert 'shadow' not in summary                          # shadow machinery is gone


def test_event_create_records_alert_event_with_run_id():
    f = AnomalyFinding('det_a', '', 'event', 'high', 'T', 'D', {'x': 1})
    svc, alerts_repo = _service({'det_a': _detector('det_a', [f])}, [_rule('ra', 'det_a')])
    svc.evaluate_run('grid-1', RUN, {}, pipeline_run_id='run-1')
    kw = alerts_repo.append_alert_event.call_args.kwargs
    assert kw['alert_id'] == 'new-alert' and kw['pipeline_run_id'] == 'run-1'
    assert kw['status'] == 'active' and kw['rule_id'] == 'ra'


def test_no_run_id_writes_no_alert_event():
    f = AnomalyFinding('det_a', '', 'event', 'high', 'T', 'D', {'x': 1})
    svc, alerts_repo = _service({'det_a': _detector('det_a', [f])}, [_rule('ra', 'det_a')])
    svc.evaluate_run('grid-1', RUN, {})  # no pipeline_run_id
    alerts_repo.append_alert_event.assert_not_called()


# --- Delivery enqueue hook (#917 Task 7) ---

def test_anomaly_create_enqueues_delivery():
    from src.modules.anomaly_detection.service import AnomalyDetectionService
    from src.modules.anomaly_detection.domain import AnomalyFinding
    alerts_repo = MagicMock()
    alerts_repo.create_alert.return_value = {
        'id': 'alert-9', 'created_at': datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc)}
    delivery = MagicMock()
    svc = AnomalyDetectionService(MagicMock(), alerts_repo, delivery_service=delivery)
    finding = AnomalyFinding(
        detector_key='volume', dimension_key='d', kind='condition', severity='high',
        alert_type='anomaly', title='t', description='desc', evidence={}, asset_ids=['a1'])
    rule = {'id': 'r1', 'slug': 's', 'name': 'n', 'threshold': {}, 'severity': 'high'}
    svc._create(rule, finding, 'fp-1', {'alerts_created': 0})
    delivery.enqueue_for_alert.assert_called_once()
    alert_arg = delivery.enqueue_for_alert.call_args.args[0]
    assert alert_arg['id'] == 'alert-9' and alert_arg['severity'] == 'high'


def test_delivery_payload_matches_stored_description_and_carries_narrative_reason():
    # Review fix: outbound delivery must not diverge from the stored/UI alert —
    # 'description' is the static rule text (what _create stores), 'reason' is
    # the humanized narrative (what the UI shows as Reason), never None when a
    # narrative exists.
    narrative = 'Some humanized story.'
    alerts_repo = MagicMock()
    alerts_repo.create_alert.return_value = {
        'id': 'alert-1', 'created_at': datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc)}
    delivery = MagicMock()
    svc = AnomalyDetectionService(MagicMock(), alerts_repo, delivery_service=delivery)
    finding = AnomalyFinding(
        detector_key='mass_new_assets', dimension_key='', kind='event', severity='high',
        title='t', description=narrative, evidence={'narrative': narrative})
    rule = {'id': 'r1', 'slug': 's', 'threshold': {}, 'description': 'Static rule description.'}
    svc._create(rule, finding, 'fp-1', {'alerts_created': 0})
    alert_arg = delivery.enqueue_for_alert.call_args.args[0]
    assert alert_arg['description'] == 'Static rule description.'
    assert alert_arg['reason'] == narrative


def test_anomaly_condition_update_does_not_enqueue():
    from src.modules.anomaly_detection.service import AnomalyDetectionService
    from src.modules.anomaly_detection.domain import AnomalyFinding
    alerts_repo = MagicMock()
    delivery = MagicMock()
    svc = AnomalyDetectionService(MagicMock(), alerts_repo, delivery_service=delivery)
    fp = 'anomaly-cond:source_volume:X:volume'
    svc.repo = MagicMock()
    svc.repo.get_active_anomaly_alerts.return_value = [
        {'id': 'c1', 'fingerprint': fp, 'severity': 'high'}]
    finding = AnomalyFinding(
        detector_key='source_volume', dimension_key='X:volume', kind='condition',
        severity='high', alert_type='anomaly', title='t', description='desc', evidence={},
        asset_ids=['a1'])
    rule = {'id': 'r1', 'slug': 's', 'name': 'n', 'threshold': {}, 'severity': 'high'}
    summary = {'alerts_created': 0, 'alerts_updated': 0, 'alerts_resolved': 0}
    svc._apply_conditions(rule, [finding], summary)
    alerts_repo.update_alert_assets.assert_called_once()
    delivery.enqueue_for_alert.assert_not_called()


# --- Asset-label resolution (Task 4): no raw UUIDs in anomaly alert copy ------

def test_label_findings_substitutes_token_and_fills_evidence_when_labeled():
    f = AnomalyFinding(
        detector_key='ml_behavior', dimension_key='X', kind='condition', severity='high',
        title=f"Unusual change behavior — {ASSET_LABEL_TOKEN}", description='',
        evidence={'narrative': f"{ASSET_LABEL_TOKEN}'s recent change behavior is unusual "
                                "for this fleet."},
        asset_ids=['X'])
    svc, _ = _service({}, [])
    svc.repo.get_asset_labels.return_value = {'X': 'web-01'}

    out = svc._label_findings([f])[0]

    assert out.title == "Unusual change behavior — web-01"
    assert out.evidence['labels'] == {'X': 'web-01'}
    assert out.evidence['label'] == 'web-01'
    assert ASSET_LABEL_TOKEN not in out.evidence['narrative']
    assert 'web-01' in out.evidence['narrative']
    svc.repo.get_asset_labels.assert_called_once_with(['X'])


def test_label_findings_falls_back_to_short_label_when_unresolved():
    full_id = 'yyyyyyyy-1111-2222-3333-444444444444'
    f = AnomalyFinding(
        detector_key='ml_behavior', dimension_key=full_id, kind='condition', severity='high',
        title=f"Unusual change behavior — {ASSET_LABEL_TOKEN}", description='', evidence={},
        asset_ids=[full_id])
    svc, _ = _service({}, [])
    svc.repo.get_asset_labels.return_value = {}       # 'Y' unresolved

    out = svc._label_findings([f])[0]

    short = f"asset {full_id[:8]}"
    assert out.title == f"Unusual change behavior — {short}"
    assert out.evidence['labels'] == {full_id: short}
    assert out.evidence['label'] == short
    assert full_id not in out.title                   # no raw UUID leak


# --- subject_type plumbing (Task 1): _create forwards it, _aggregate inherits it ---

def test_create_forwards_subject_type_metric():
    f = AnomalyFinding(
        detector_key='mass_new_assets', dimension_key='', kind='event', severity='high',
        title='t', description='d', evidence={'detector': 'mass_new_assets'},
        subject_type='metric')
    svc, alerts_repo = _service({}, [])
    svc._create({'id': 'r1', 'slug': 's', 'threshold': {}}, f, 'fp-st', {'alerts_created': 0})
    kwargs = alerts_repo.create_alert.call_args.kwargs
    assert kwargs['subject_type'] == 'metric'


def test_aggregate_inherits_subject_type():
    events = [AnomalyFinding(detector_key='identity_churn', dimension_key=str(i), kind='event',
                             severity='low', title='t', description='d', evidence={},
                             subject_type='metric') for i in range(12)]
    svc, _ = _service({}, [])
    out = svc._aggregate(events, threshold=10)
    assert out[-1].subject_type == 'metric'


# --- sample_asset_ids -> evidence['sample'] (Task 4) --------------------------

def test_label_findings_builds_sample():
    f = AnomalyFinding(detector_key='mass_new_assets', dimension_key='', kind='event',
                       severity='high', title='t', description='d',
                       evidence={'observed': 500}, subject_type='metric',
                       sample_asset_ids=['a1', 'a2'])
    svc, _ = _service({}, [])
    svc.repo.get_asset_labels.return_value = {'a1': 'web-01', 'a2': 'web-02'}

    out = svc._label_findings([f])[0]

    assert out.evidence['sample'] == [{'id': 'a1', 'label': 'web-01'},
                                      {'id': 'a2', 'label': 'web-02'}]
    svc.repo.get_asset_labels.assert_called_once_with(['a1', 'a2'])
    assert 'labels' not in out.evidence         # asset_ids empty -> no per-asset labels map


def test_label_findings_sample_falls_back_to_short_label_when_unresolved():
    full_id = 'zzzzzzzz-1111-2222-3333-444444444444'
    f = AnomalyFinding(
        detector_key='identity_churn', dimension_key='readded', kind='event',
        severity='high', title='t', description='d', evidence={},
        subject_type='metric', sample_asset_ids=[full_id])
    svc, _ = _service({}, [])
    svc.repo.get_asset_labels.return_value = {}       # unresolved

    out = svc._label_findings([f])[0]

    # 'id' intentionally carries the real asset id (Task 5 links to it); only the
    # human-readable 'label' must never be a raw UUID.
    short = f"asset {full_id[:8]}"
    assert out.evidence['sample'] == [{'id': full_id, 'label': short}]
    assert full_id not in short


def test_label_findings_dedupes_ids_across_asset_ids_and_sample():
    f = AnomalyFinding(
        detector_key='identity_swap', dimension_key='X', kind='event', severity='high',
        title='t', description='d', evidence={},
        asset_ids=['a1'], sample_asset_ids=['a1', 'a2'])
    svc, _ = _service({}, [])
    svc.repo.get_asset_labels.return_value = {'a1': 'web-01', 'a2': 'web-02'}

    svc._label_findings([f])

    svc.repo.get_asset_labels.assert_called_once_with(['a1', 'a2'])   # single batch, deduped


def test_label_findings_no_sample_key_when_sample_asset_ids_empty():
    f = AnomalyFinding(detector_key='mass_missing', dimension_key='', kind='event',
                       severity='high', title='t', description='d', evidence={})
    svc, _ = _service({}, [])
    out = svc._label_findings([f])[0]
    assert 'sample' not in out.evidence
    svc.repo.get_asset_labels.assert_not_called()


# --- Dispatch carries evidence, labelled assets, and detect-time (Task 4) -----

def _drive_anomaly_create(asset_ids, evidence, subject_type='asset'):
    """Drive AnomalyDetectionService._create far enough to capture the dispatched
    alert dict, and return it."""
    from datetime import datetime, timezone
    from src.modules.anomaly_detection.domain import AnomalyFinding

    alerts_repo = MagicMock()
    alerts_repo.create_alert.return_value = {
        'id': 'alert-9',
        'created_at': datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc),
    }
    delivery = MagicMock()
    svc = AnomalyDetectionService(MagicMock(), alerts_repo, delivery_service=delivery)
    finding = AnomalyFinding(
        detector_key='volume', dimension_key='d', kind='event', severity='high',
        title='t', description='desc', evidence=evidence,
        alert_type='data_quality', asset_ids=asset_ids, subject_type=subject_type)
    rule = {'id': 'r1', 'slug': 's', 'name': 'n', 'threshold': {}, 'severity': 'high',
            'description': 'A tracked signal moved unusually.'}
    svc._create(rule, finding, 'fp-1', {'alerts_created': 0})
    return delivery.enqueue_for_alert.call_args.args[0]


def test_anomaly_dispatch_carries_evidence_and_labelled_assets():
    """The card's whole evidence block — narrative, observed vs usual, breakdown,
    labelled sample — was invisible to every channel because the payload never carried
    evidence. Labels come from evidence['labels'], already resolved by _label_findings,
    so this costs no extra query."""
    dispatched = _drive_anomaly_create(
        asset_ids=['a1', 'a2'],
        evidence={'narrative': 'Far above the usual 3.', 'observed': 12, 'typical': 3,
                  'labels': {'a1': 'web-prod-01', 'a2': 'db-prod-02'},
                  'technical': {'z': 4.2}},
    )
    assert dispatched['evidence']['narrative'] == 'Far above the usual 3.'
    assert dispatched['evidence']['observed'] == 12
    assert dispatched['assets'] == [
        {'id': 'a1', 'label': 'web-prod-01'},
        {'id': 'a2', 'label': 'db-prod-02'},
    ]
    # Anomaly reason stays the narrative — byte-identical to _compute_reason's first
    # branch, which is what the card renders.
    assert dispatched['reason'] == 'Far above the usual 3.'
    # The raw-id keys are stripped by build_alert_payload, not here — the dispatch
    # dict still carries the finding's evidence verbatim.
    assert dispatched['created_at'].year == 2026


def test_aggregate_dispatch_carries_a_reason_and_real_labels():
    """The aggregate path shipped reason=None and a label-less asset list.

    _label_findings deliberately runs BEFORE _aggregate (so no {{asset_label}} token
    leaks into the frozen evidence items), but _aggregate then builds a NEW finding
    whose evidence has no narrative and no labels. Dispatch read evidence['narrative']
    -> None, so every alert collapsing more than aggregation_threshold (10) events
    notified with no reason and 'label': None for all 25 assets, while the card beside
    it computed and showed the sentence. The bug this branch exists to kill was only
    dead on the rule path.

    Routing through _compute_reason — the same static method GET /api/alerts calls —
    is what makes the two unable to disagree. Its first branch returns the narrative
    when present, so every non-aggregate finding is byte-identical (pinned by
    test_anomaly_dispatch_carries_evidence_and_labelled_assets)."""
    from src.modules.alerts.service import AlertsService

    findings = [AnomalyFinding('segment_outlier', f'a{i}', 'event', 'medium', 't', 'd',
                               {'narrative': f'finding {i}'}, asset_ids=[f'a{i}'])
                for i in range(12)]        # > default aggregation_threshold (10)
    svc, alerts_repo = _service(
        {'segment_outlier': _detector('segment_outlier', findings)},
        [_rule('ra', 'segment_outlier')])
    svc.repo.get_asset_labels.return_value = {f'a{i}': f'host-{i}' for i in range(12)}
    delivery = MagicMock()
    svc.delivery = delivery
    svc.evaluate_run('g', RUN, {})

    dispatched = delivery.enqueue_for_alert.call_args.args[0]
    # The bug itself: the aggregate must not notify with no reason.
    assert dispatched['reason']
    # And it must be what the card computes for the same alert. Built from a
    # hand-written dict and anchored to a literal, so a fake fix that hardcodes a
    # plausible string still fails.
    expected = AlertsService._compute_reason({
        'threshold': {}, 'subject_type': 'asset', 'asset_count': 12,
        'evidence': dispatched['evidence'],
    })
    assert dispatched['reason'] == expected
    assert dispatched['reason'] == '12 assets matched this rule'
    # Labels the aggregate's own asset union, reusing what _label_findings already
    # resolved on each collapsed finding — no extra query, no reordering.
    assert dispatched['assets'] == [{'id': f'a{i}', 'label': f'host-{i}'} for i in range(12)]
    svc.repo.get_asset_labels.assert_called_once()
