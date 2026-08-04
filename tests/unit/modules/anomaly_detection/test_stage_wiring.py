# SPDX-License-Identifier: AGPL-3.0-or-later
"""s20 anomaly wiring + service confidence histogram (Task 11)."""
from unittest.mock import MagicMock, patch

from src.modules.anomaly_detection.service import AnomalyDetectionService
from src.modules.asset_generation.pipeline.stages.s20_alert_evaluation import (
    AlertEvaluationStage,
)

_DASH = 'src.modules.dashboard.repositories.dashboard_repository.DashboardRepository'


def _stage_ctx(per_source=None):
    stage = AlertEvaluationStage(MagicMock())
    stage._get_alerts_service = lambda: _stub_alerts()
    ctx = MagicMock()
    ctx.generation_run_id = 'grid-xyz'
    ctx.pipeline_wall_start = 1_800_000_000.0   # real epoch -> real run_started_at
    ctx.stats = {'stages': {}, 'per_source': per_source or {}}
    return stage, ctx


def _stub_alerts():
    a = MagicMock()
    a.evaluate_all_rules.return_value = {}
    return a


def _patched_dashboard():
    p = patch(_DASH)
    mock = p.start()
    mock.return_value.refresh_materialized_views.return_value = {
        'refreshed_views': [], 'failed_views': {}}
    return p


def test_current_batches_built_from_per_source_with_names_and_fallback():
    _, ctx = _stage_ctx(per_source={
        'batch-1': {'unique_assets': 25000, 'excluded': 3, 'integration_name': 'SentinelOne'},
        'batch-2': {'unique_assets': 12, 'excluded': 0}})   # no name -> batch_id fallback
    batches = {b['integration_name']: b for b in AlertEvaluationStage._current_batches(ctx)}
    assert batches['SentinelOne']['unique_assets'] == 25000
    assert batches['SentinelOne']['success'] is True        # default when no outcome recorded
    assert 'batch-2' in batches       # fell back to the batch_id label


def test_current_batches_merges_real_success_error():
    _, ctx = _stage_ctx(per_source={
        'b1': {'unique_assets': 0, 'integration_name': 'CrowdStrike'}})
    ctx.stats['current_batches'] = [
        {'integration_name': 'CrowdStrike', 'success': False, 'error': 'connector timeout'}]
    batches = {b['integration_name']: b for b in AlertEvaluationStage._current_batches(ctx)}
    assert batches['CrowdStrike']['success'] is False
    assert batches['CrowdStrike']['error'] == 'connector timeout'


def test_failed_batch_absent_from_per_source_is_surfaced():
    # A batch that loaded zero records contributes no per_source row; surface it
    # so the failed routing sees it instead of it looking 'missing'.
    _, ctx = _stage_ctx(per_source={})
    ctx.stats['current_batches'] = [
        {'integration_name': 'Nessus', 'success': False, 'error': 'no records loaded from batch'}]
    batches = AlertEvaluationStage._current_batches(ctx)
    assert len(batches) == 1
    assert batches[0]['integration_name'] == 'Nessus' and batches[0]['success'] is False


def test_failed_batch_reaches_a3_failed_routing():
    # End-to-end wiring: a failed outcome flows _current_batches -> A3 _failed.
    from datetime import datetime
    from src.modules.anomaly_detection.domain import RunEvalContext
    from src.modules.anomaly_detection.detectors.volume import SourceVolumeDetector

    _, ctx = _stage_ctx(per_source={'b1': {'unique_assets': 0, 'integration_name': 'CrowdStrike'}})
    ctx.stats['current_batches'] = [
        {'integration_name': 'CrowdStrike', 'success': False, 'error': 'connector timeout'}]
    current = AlertEvaluationStage._current_batches(ctx)

    repo = MagicMock()
    repo.get_recent_run_stats.return_value = []
    rctx = RunEvalContext(
        generation_run_id='g', run_started_at=datetime(2026, 6, 2),
        current_stats={'stages': {}}, rule={'slug': 'x', 'threshold': {}},
        params={'z_medium': 3, 'min_count': 25, 'min_pct': 15,
                'window_runs': 30, 'min_samples': 8},
        settings=MagicMock(), repo=repo, current_batches=current)
    findings = SourceVolumeDetector().evaluate(rctx)
    failed = [f for f in findings if f.dimension_key == 'CrowdStrike:failed']
    assert len(failed) == 1 and failed[0].severity == 'high'
    assert failed[0].evidence['error'] == 'connector timeout'


def test_s20_runs_anomaly_and_stores_summary():
    stage, ctx = _stage_ctx(per_source={'b1': {'unique_assets': 5, 'integration_name': 'X'}})
    anomaly = MagicMock()
    anomaly.evaluate_run.return_value = {'rules_evaluated': 3, 'alerts_created': 1, 'errors': {}}
    anomaly.fleet_confidence_histogram.return_value = {'low': 1, 'medium': 2, 'high': 3}
    stage._get_anomaly_service = lambda: anomaly

    p = _patched_dashboard()
    try:
        stage._execute(ctx)
    finally:
        p.stop()

    kwargs = anomaly.evaluate_run.call_args.kwargs
    assert kwargs['generation_run_id'] == 'grid-xyz'
    assert kwargs['current_stats'] is ctx.stats
    assert any(b['integration_name'] == 'X' for b in kwargs['current_batches'])
    stored = ctx.stats['stages']['anomaly_detection']
    assert stored['rules_evaluated'] == 3
    assert stored['confidence_histogram'] == {'low': 1, 'medium': 2, 'high': 3}


def test_s20_anomaly_failure_is_non_fatal():
    stage, ctx = _stage_ctx()
    anomaly = MagicMock()
    anomaly.evaluate_run.side_effect = RuntimeError('boom')
    stage._get_anomaly_service = lambda: anomaly

    p = _patched_dashboard()
    try:
        stage._execute(ctx)   # must not raise
    finally:
        p.stop()
    assert 'error' in ctx.stats['stages']['anomaly_detection']


def test_fleet_confidence_histogram_uses_settings_boundaries():
    def exec_q(sql, params=None):
        if 'system_settings' in sql:
            return [{'key': 'confidence_threshold_medium', 'value': '0.6'},
                    {'key': 'confidence_threshold_high', 'value': '0.85'}]
        return [{'low': 10, 'medium': 20, 'high': 70}]

    db = MagicMock()
    db.execute_query.side_effect = exec_q
    svc = AnomalyDetectionService(db, MagicMock())
    assert svc.fleet_confidence_histogram() == {'low': 10, 'medium': 20, 'high': 70}
    hist_call = next(c for c in db.execute_query.call_args_list if 'FILTER' in c.args[0])
    assert hist_call.args[1]['med'] == 0.6 and hist_call.args[1]['high'] == 0.85


def test_fleet_confidence_histogram_defaults_when_no_settings():
    def exec_q(sql, params=None):
        if 'system_settings' in sql:
            return []
        return [{'low': 1, 'medium': 2, 'high': 3}]

    db = MagicMock()
    db.execute_query.side_effect = exec_q
    svc = AnomalyDetectionService(db, MagicMock())
    svc.fleet_confidence_histogram()
    hist_call = next(c for c in db.execute_query.call_args_list if 'FILTER' in c.args[0])
    # defaults come from the shared DEFAULT_CONFIDENCE_THRESHOLDS (0.45/0.70)
    assert hist_call.args[1]['med'] == 0.45 and hist_call.args[1]['high'] == 0.70


def test_context_carries_pipeline_run_id():
    import pandas as pd
    from src.modules.asset_generation.pipeline.context import PipelineContext
    ctx = PipelineContext(raw_data_df=pd.DataFrame(), pipeline_run_id='run-1')
    assert ctx.pipeline_run_id == 'run-1'


def test_s20_forwards_pipeline_run_id_to_both_paths():
    stage, ctx = _stage_ctx(per_source={'b1': {'unique_assets': 5, 'integration_name': 'X'}})
    ctx.pipeline_run_id = 'run-1'
    alerts = _stub_alerts()
    stage._get_alerts_service = lambda: alerts
    anomaly = MagicMock()
    anomaly.evaluate_run.return_value = {'rules_evaluated': 0, 'errors': {}}
    anomaly.fleet_confidence_histogram.return_value = {}
    stage._get_anomaly_service = lambda: anomaly

    p = _patched_dashboard()
    try:
        stage._execute(ctx)
    finally:
        p.stop()

    assert alerts.evaluate_all_rules.call_args.kwargs['pipeline_run_id'] == 'run-1'
    assert anomaly.evaluate_run.call_args.kwargs['pipeline_run_id'] == 'run-1'
