# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
import uuid
from unittest.mock import MagicMock
from datetime import datetime, timezone, timedelta
from src.modules.alerts.service import AlertsService


@pytest.fixture
def mock_repo():
    return MagicMock()


@pytest.fixture
def service(mock_repo):
    return AlertsService(mock_repo)


@pytest.fixture
def base_rule():
    return {
        'id': 'rule-1', 'slug': 'test-rule', 'name': 'Test',
        'description': 'd', 'type': 'security', 'severity': 'high',
        'query': None, 'threshold': None, 'source': 'user',
        'target_filters': [{'field': 'x', 'op': '=', 'value': 'y'}],
        'compliance_criteria': None, 'silence_duration': None,
    }


def test_match_result_no_matches_resolves_existing_alert(service, mock_repo, base_rule):
    mock_repo.get_active_alert_for_rule.return_value = {'id': 'alert-1'}
    result = service._evaluate_match_result(base_rule, [])
    assert result == 'resolved'
    mock_repo.resolve_alert.assert_called_once_with('alert-1')


def test_match_result_no_matches_no_alert_is_noop(service, mock_repo, base_rule):
    mock_repo.get_active_alert_for_rule.return_value = None
    result = service._evaluate_match_result(base_rule, [])
    assert result == 'unchanged'
    mock_repo.resolve_alert.assert_not_called()


def test_match_result_with_matches_creates_alert(service, mock_repo, base_rule):
    mock_repo.get_active_alert_for_rule.return_value = None
    matches = [{'id': 'a1', 'category': 'Server'}, {'id': 'a2', 'category': 'Server'}]
    result = service._evaluate_match_result(base_rule, matches)
    assert result == 'created'
    mock_repo.create_alert.assert_called_once()
    kwargs = mock_repo.create_alert.call_args.kwargs
    assert kwargs['asset_count'] == 2


def test_match_result_with_matches_updates_existing(service, mock_repo, base_rule):
    mock_repo.get_active_alert_for_rule.return_value = {
        'id': 'alert-1', 'asset_ids': ['a1'],
    }
    matches = [{'id': 'a1', 'category': 'Server'}, {'id': 'a2', 'category': 'Server'}]
    result = service._evaluate_match_result(base_rule, matches)
    assert result == 'updated'
    mock_repo.update_alert_assets.assert_called_once()


def test_match_result_unchanged_when_asset_ids_identical(service, mock_repo, base_rule):
    mock_repo.get_active_alert_for_rule.return_value = {
        'id': 'alert-1', 'asset_ids': ['a1'],
    }
    matches = [{'id': 'a1', 'category': 'Server'}]
    result = service._evaluate_match_result(base_rule, matches)
    assert result == 'unchanged'
    mock_repo.update_alert_assets.assert_not_called()


def test_unchanged_sql_rule_does_not_resolve_labels(service, mock_repo, base_rule):
    """A steady-state SQL rule re-firing every pipeline run must not pay for labels
    nobody will read.

    Label resolution is bounded to one query per CREATED alert (project owner's
    ruling). The 'unchanged' short-circuit never reaches _dispatch_delivery, the
    only consumer of the resolved labels, so resolving here would burn an indexed
    query per steadily-firing rule per pipeline run for a result nothing reads.
    """
    mock_repo.get_active_alert_for_rule.return_value = {
        'id': 'alert-1', 'asset_ids': ['a1'],
    }
    # Stored-SQL shaped: no asset_label key at all (the rule carries its own `query`,
    # same shape as test_sql_query_rule_resolves_asset_labels_for_delivery).
    matches = [{'id': 'a1', 'category': 'Server'}]
    result = service._evaluate_match_result(base_rule, matches)
    assert result == 'unchanged'
    mock_repo.get_asset_details.assert_not_called()


def test_should_fire_no_threshold_fires_on_match(service, base_rule):
    assert service._should_fire(base_rule, [{'id': 'a1'}]) is True


def test_should_fire_no_threshold_no_match_is_false(service, base_rule):
    assert service._should_fire(base_rule, []) is False


def test_should_fire_number_exceeds(service, base_rule):
    base_rule['threshold'] = {'condition': 'exceeds', 'value': 5, 'unit': 'number'}
    assert service._should_fire(base_rule, [{'id': f'a{i}'} for i in range(6)]) is True
    assert service._should_fire(base_rule, [{'id': f'a{i}'} for i in range(5)]) is False


def test_should_fire_number_falls_below(service, base_rule):
    base_rule['threshold'] = {'condition': 'falls_below', 'value': 10, 'unit': 'number'}
    assert service._should_fire(base_rule, [{'id': 'a1'}]) is True
    assert service._should_fire(base_rule, [{'id': f'a{i}'} for i in range(10)]) is False


def test_should_fire_percent_exceeds(service, mock_repo, base_rule):
    base_rule['threshold'] = {'condition': 'exceeds', 'value': 50, 'unit': 'percent'}
    mock_repo.count_target_universe.return_value = 10
    assert service._should_fire(base_rule, [{'id': f'a{i}'} for i in range(6)]) is True
    assert service._should_fire(base_rule, [{'id': f'a{i}'} for i in range(5)]) is False


def test_should_fire_percent_with_zero_universe(service, mock_repo, base_rule):
    base_rule['threshold'] = {'condition': 'exceeds', 'value': 50, 'unit': 'percent'}
    mock_repo.count_target_universe.return_value = 0
    assert service._should_fire(base_rule, []) is False


def test_is_silenced_no_duration(service, base_rule):
    assert service._is_silenced(base_rule) is False


def test_is_silenced_within_window(service, mock_repo, base_rule):
    base_rule['silence_duration'] = 60  # minutes
    mock_repo.get_last_resolved_alert_for_rule.return_value = {
        'resolved_at': datetime.now(tz=timezone.utc) - timedelta(minutes=30),
    }
    assert service._is_silenced(base_rule) is True


def test_is_silenced_outside_window(service, mock_repo, base_rule):
    base_rule['silence_duration'] = 60
    mock_repo.get_last_resolved_alert_for_rule.return_value = {
        'resolved_at': datetime.now(tz=timezone.utc) - timedelta(minutes=90),
    }
    assert service._is_silenced(base_rule) is False


def test_is_silenced_no_recent_alert(service, mock_repo, base_rule):
    base_rule['silence_duration'] = 60
    mock_repo.get_last_resolved_alert_for_rule.return_value = None
    assert service._is_silenced(base_rule) is False


# =========================================================================
# Evaluation cadence (pipeline vs interval)
# =========================================================================


def test_evaluate_all_rules_uses_pipeline_cadence(service, mock_repo):
    """s20's pipeline path evaluates only pipeline-cadence rules."""
    mock_repo.get_enabled_rules.return_value = []
    service.evaluate_all_rules()
    mock_repo.get_enabled_rules.assert_called_once_with(cadence='pipeline')


def test_evaluate_interval_rules_uses_interval_cadence(service, mock_repo):
    """The scheduler's 5-min job evaluates only interval-cadence rules."""
    mock_repo.get_enabled_rules.return_value = []
    service.evaluate_interval_rules()
    mock_repo.get_enabled_rules.assert_called_once_with(cadence='interval')


def test_interval_loop_isolates_failing_rule(service, mock_repo):
    """One rule raising must not stop the others — the interval job keeps
    evaluating so a single bad rule can't silence the whole cadence."""
    def _rule(slug, query):
        return {
            'id': f'id-{slug}', 'slug': slug, 'name': slug, 'description': 'd',
            'type': 'operational', 'severity': 'high', 'query': query,
            'threshold': None, 'source': 'system',
            'target_filters': None, 'compliance_criteria': None,
            'silence_duration': None,
        }
    mock_repo.get_enabled_rules.return_value = [
        _rule('bad', 'SELECT bad'), _rule('ok', 'SELECT ok'),
    ]

    def _exec(query):
        if query == 'SELECT bad':
            raise RuntimeError('boom')
        return []
    mock_repo.execute_rule_query.side_effect = _exec
    mock_repo.get_active_alert_for_rule.return_value = None

    stats = service.evaluate_interval_rules()

    assert stats['rules_evaluated'] == 2
    assert stats['errors'] == 1
    assert mock_repo.execute_rule_query.call_count == 2  # good rule still ran


# =========================================================================
# CRUD facade
# =========================================================================

from src.modules.alerts.repository import SystemRuleImmutableError
from src.modules.alerts.service import SystemRuleImmutableError as ServiceSystemErr


def test_create_rule_returns_created_row(service, mock_repo):
    mock_repo.create_rule.return_value = {'id': 'new-id', 'name': 'X'}
    payload = {
        'name': 'X', 'type': 'security', 'severity': 'high',
        'target_filters': [{'field': 'x', 'op': '=', 'value': 'y'}],
    }
    result = service.create_rule(payload, created_by='user-uuid')
    assert result['id'] == 'new-id'


def test_update_rule_propagates_system_error(service, mock_repo):
    mock_repo.update_rule.side_effect = SystemRuleImmutableError('nope')
    with pytest.raises(ServiceSystemErr):
        service.update_rule('abc', {'name': 'X'})


def test_update_rule_returns_none_when_missing(service, mock_repo):
    mock_repo.update_rule.return_value = None
    assert service.update_rule('missing', {'name': 'X'}) is None


def test_delete_rule_propagates_system_error(service, mock_repo):
    mock_repo.delete_rule.side_effect = SystemRuleImmutableError('nope')
    with pytest.raises(ServiceSystemErr):
        service.delete_rule('abc')


def test_preview_rule_returns_count(service, mock_repo):
    mock_repo.execute_filter_rule.return_value = [{'id': 'a1'}, {'id': 'a2'}]
    result = service.preview_rule(
        target_filters=[{'field': 'x', 'op': '=', 'value': 'y'}],
        compliance_criteria=None,
    )
    assert result == {'total_matching': 2}


def test_create_alert_inherits_rule_source(service, mock_repo, base_rule):
    base_rule['source'] = 'user'
    mock_repo.get_active_alert_for_rule.return_value = None
    service._evaluate_match_result(base_rule, [{'id': 'a1', 'category': 'Server'}])
    kwargs = mock_repo.create_alert.call_args.kwargs
    assert kwargs['source'] == 'user'


def test_create_alert_defaults_source_to_system_when_missing(service, mock_repo, base_rule):
    base_rule.pop('source', None)
    mock_repo.get_active_alert_for_rule.return_value = None
    service._evaluate_match_result(base_rule, [{'id': 'a1', 'category': 'Server'}])
    kwargs = mock_repo.create_alert.call_args.kwargs
    assert kwargs['source'] == 'system'


# =========================================================================
# Reason virtual field + response shaping
# =========================================================================


class TestComputeReason:
    def test_falls_below_percent(self):
        alert = {'threshold': {'condition': 'falls_below', 'value': 90, 'unit': 'percent', 'current_value': 82}}
        assert AlertsService._compute_reason(alert) == "Current value 82% is below the 90% threshold"

    def test_exceeds_percent(self):
        alert = {'threshold': {'condition': 'exceeds', 'value': 5, 'unit': 'percent', 'current_value': 12}}
        assert AlertsService._compute_reason(alert) == "Current value 12% is above the 5% threshold"

    def test_exceeds_number(self):
        alert = {'threshold': {'condition': 'exceeds', 'value': 10, 'unit': 'number', 'current_value': 15}}
        assert AlertsService._compute_reason(alert) == "15 assets — above the threshold of 10"

    def test_falls_below_number(self):
        alert = {'threshold': {'condition': 'falls_below', 'value': 100, 'unit': 'number', 'current_value': 42}}
        assert AlertsService._compute_reason(alert) == "42 assets — below the threshold of 100"

    def test_falls_back_to_asset_count_for_current(self):
        alert = {'threshold': {'condition': 'exceeds', 'value': 5, 'unit': 'number'}, 'asset_count': 7}
        assert AlertsService._compute_reason(alert) == "7 assets — above the threshold of 5"

    def test_no_threshold_plural(self):
        assert AlertsService._compute_reason({'asset_count': 4}) == "4 assets matched this rule"

    def test_no_threshold_singular(self):
        assert AlertsService._compute_reason({'asset_count': 1}) == "1 asset matched this rule"

    def test_no_threshold_zero(self):
        assert AlertsService._compute_reason({}) == "0 assets matched this rule"

    def test_malformed_threshold_falls_back(self):
        alert = {'threshold': {'value': 10}, 'asset_count': 3}
        assert AlertsService._compute_reason(alert) == "3 assets matched this rule"

    def test_narrative_evidence_wins_over_everything(self):
        alert = {
            'subject_type': 'asset',
            'evidence': {'narrative': 'Some humanized story.'},
            'description': 'X',
        }
        assert AlertsService._compute_reason(alert) == 'Some humanized story.'

    def test_assets_disappeared_operator_shape_mentions_limit(self):
        alert = {
            'subject_type': 'pipeline_run',
            'threshold': {'unit': 'percent', 'value': 10, 'metric': 'assets_disappeared_pct', 'operator': '>'},
            'description': 'Detects when more than 10% of assets disappear in a single pipeline run.',
        }
        reason = AlertsService._compute_reason(alert)
        assert '10%' in reason
        assert 'limit' in reason
        assert reason != alert['description']

    def test_metric_operator_shape_differs_from_description(self):
        alert = {
            'subject_type': 'metric',
            'threshold': {'metric': 'secured_to_monitored_pct', 'operator': '>', 'value': 5, 'unit': 'percent'},
            'description': 'Tracks drops in Secured-tier coverage.',
        }
        reason = AlertsService._compute_reason(alert)
        assert reason != alert['description']
        assert '5%' in reason

    def test_chronicle_silence_shape_falls_back_to_generic(self):
        alert = {'subject_type': 'pipeline_run', 'threshold': {'min': 1}, 'description': 'D'}
        assert AlertsService._compute_reason(alert) != 'D'

    def test_asset_operator_shape_falls_through_to_noun_branch(self):
        # stale-assets-retirement: subject_type=asset with an operator-shape threshold
        # must NOT be routed to the summary-sentence template (branch 3) — that loses
        # the asset entity phrasing that drives the alert's asset grid.
        alert = {
            'subject_type': 'asset', 'asset_count': 7,
            'threshold': {'metric': 'days_since_seen', 'operator': '>=', 'value': 30, 'unit': 'assets'},
        }
        assert AlertsService._compute_reason(alert) == "7 assets matched this rule"

    def test_integration_operator_shape_falls_through_to_noun_branch(self):
        # sync-failures / data-source-stale: subject_type=integration with an
        # operator-shape threshold must NOT be routed to branch 3 either — it needs
        # the "N data sources" phrasing that points at the subjects list below.
        alert = {
            'subject_type': 'integration', 'asset_count': 2,
            'threshold': {'metric': 'sync_failures', 'operator': '>', 'value': 0, 'unit': 'integrations'},
        }
        assert AlertsService._compute_reason(alert) == "2 data sources — see below"


def _alert_row(**overrides):
    base = {
        'id': 'alert-1',
        'rule_id': 'rule-1',
        'type': 'security',
        'severity': 'high',
        'title': 'T',
        'description': 'D',
        'threshold': {'condition': 'exceeds', 'value': 5, 'unit': 'number', 'current_value': 9},
        'status': 'active',
        'asset_count': 9,
        'assets_by_category': {'server': 9},
        'data_sources': [],
        'asset_ids': ['a1'],
        'source': 'system',
        'created_at': datetime(2026, 4, 1, tzinfo=timezone.utc),
        'updated_at': datetime(2026, 4, 1, tzinfo=timezone.utc),
        'resolved_at': None,
    }
    base.update(overrides)
    return base


class TestGetAlertsResponse:
    @pytest.fixture(autouse=True)
    def _wire_repo(self, mock_repo):
        mock_repo.get_alerts_summary.return_value = {'total': 1}
        mock_repo.get_alerts_timeline.return_value = []
        mock_repo.get_alerts_count.return_value = 1
        mock_repo.get_asset_details.return_value = []

    def test_maps_user_source_to_custom(self, service, mock_repo):
        mock_repo.get_alerts.return_value = [_alert_row(source='user')]
        assert service.get_alerts_response()['alerts'][0]['source'] == 'custom'

    def test_preserves_system_source(self, service, mock_repo):
        mock_repo.get_alerts.return_value = [_alert_row(source='system')]
        assert service.get_alerts_response()['alerts'][0]['source'] == 'system'

    def test_reason_is_embedded(self, service, mock_repo):
        mock_repo.get_alerts.return_value = [_alert_row()]
        resp = service.get_alerts_response()
        assert resp['alerts'][0]['reason'] == "9 assets — above the threshold of 5"

    def test_asset_model_passes_through(self, service, mock_repo):
        mock_repo.get_alerts.return_value = [_alert_row()]
        mock_repo.get_asset_details.return_value = [
            {'id': 'a1', 'hostname': 'h1', 'ip': '10.0.0.1', 'model': 'Dell R740'},
        ]
        resp = service.get_alerts_response()
        assert resp['alerts'][0]['assets'][0]['model'] == 'Dell R740'

    def test_internal_fields_stripped(self, service, mock_repo):
        mock_repo.get_alerts.return_value = [_alert_row()]
        alert = service.get_alerts_response()['alerts'][0]
        assert 'asset_ids' not in alert
        assert 'rule_id' not in alert


# =========================================================================
# Per-run firing history (#906)
# =========================================================================


def test_create_records_active_event_with_run_id(service, mock_repo, base_rule):
    mock_repo.get_active_alert_for_rule.return_value = None
    mock_repo.create_alert.return_value = {
        'id': 'alert-9', 'created_at': datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc)}
    matches = [{'id': 'a1', 'category': 'Server'}, {'id': 'a2', 'category': 'Server'}]
    service._evaluate_match_result(base_rule, matches, pipeline_run_id='run-1')
    kw = mock_repo.append_alert_event.call_args.kwargs
    assert kw['alert_id'] == 'alert-9' and kw['status'] == 'active'
    assert kw['asset_count'] == 2 and kw['pipeline_run_id'] == 'run-1'
    assert kw['rule_id'] == 'rule-1' and kw['severity'] == 'high'


def test_update_records_active_event(service, mock_repo, base_rule):
    mock_repo.get_active_alert_for_rule.return_value = {'id': 'alert-1', 'asset_ids': ['a1']}
    matches = [{'id': 'a1', 'category': 'Server'}, {'id': 'a2', 'category': 'Server'}]
    service._evaluate_match_result(base_rule, matches, pipeline_run_id='run-1')
    kw = mock_repo.append_alert_event.call_args.kwargs
    assert kw['alert_id'] == 'alert-1' and kw['status'] == 'active' and kw['asset_count'] == 2


def test_unchanged_active_still_records_event(service, mock_repo, base_rule):
    """Dense series (#905): an active alert with an unchanged asset set still gets
    a per-run snapshot, even though update_alert_assets is skipped."""
    mock_repo.get_active_alert_for_rule.return_value = {'id': 'alert-1', 'asset_ids': ['a1']}
    result = service._evaluate_match_result(
        base_rule, [{'id': 'a1', 'category': 'Server'}], pipeline_run_id='run-1')
    assert result == 'unchanged'
    mock_repo.update_alert_assets.assert_not_called()
    kw = mock_repo.append_alert_event.call_args.kwargs
    assert kw['alert_id'] == 'alert-1' and kw['status'] == 'active'


def test_resolve_records_resolved_event(service, mock_repo, base_rule):
    # Below-threshold matches: the rule resolves even though match_count > 0, and
    # the event must record asset_count=0 ("not firing"), not the live match count
    # (matches the anomaly path's resolve convention — #906 cross-path consistency).
    base_rule['threshold'] = {'condition': 'exceeds', 'value': 5, 'unit': 'number'}
    mock_repo.get_active_alert_for_rule.return_value = {'id': 'alert-1', 'asset_ids': ['a1']}
    matches = [{'id': f'a{i}', 'category': 'Server'} for i in range(3)]  # 3 <= 5 -> resolves
    result = service._evaluate_match_result(base_rule, matches, pipeline_run_id='run-1')
    assert result == 'resolved'
    kw = mock_repo.append_alert_event.call_args.kwargs
    assert kw['alert_id'] == 'alert-1' and kw['status'] == 'resolved'
    assert kw['asset_count'] == 0


def test_no_run_id_records_no_event(service, mock_repo, base_rule):
    """Interval/watchdog path (no pipeline_run_id) writes no history."""
    mock_repo.get_active_alert_for_rule.return_value = None
    mock_repo.create_alert.return_value = {
        'id': 'alert-9', 'created_at': datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc)}
    service._evaluate_match_result(base_rule, [{'id': 'a1', 'category': 'Server'}])
    mock_repo.append_alert_event.assert_not_called()


def test_unchanged_no_alert_records_no_event(service, mock_repo, base_rule):
    mock_repo.get_active_alert_for_rule.return_value = None
    result = service._evaluate_match_result(base_rule, [], pipeline_run_id='run-1')
    assert result == 'unchanged'
    mock_repo.append_alert_event.assert_not_called()


def test_get_alert_history_shape(service, mock_repo):
    run_uuid = uuid.uuid4()
    mock_repo.get_alert_history.return_value = [
        {'pipeline_run_id': run_uuid, 'status': 'active', 'asset_count': 3,
         'severity': 'high', 'created_at': None, 'started_at': None, 'completed_at': None},
    ]
    out = service.get_alert_history('al-1', days=30)
    assert out['alert_id'] == 'al-1'
    assert out['history'][0]['asset_count'] == 3
    assert out['history'][0]['pipeline_run_id'] == str(run_uuid)
    assert isinstance(out['history'][0]['pipeline_run_id'], str)
    mock_repo.get_alert_history.assert_called_once_with('al-1', days=30)


def test_get_alert_history_clamps_days(service, mock_repo):
    """The window is bounded: negative -> 1, absurdly large -> 730, so the SQL
    never builds an empty/future window or an overflowing Postgres interval."""
    mock_repo.get_alert_history.return_value = []
    service.get_alert_history('al-1', days=-5)
    assert mock_repo.get_alert_history.call_args.kwargs['days'] == 1
    service.get_alert_history('al-1', days=10 ** 9)
    assert mock_repo.get_alert_history.call_args.kwargs['days'] == 730


def test_list_response_exposes_latest_run_and_pointer(service, mock_repo):
    run_uuid = uuid.uuid4()
    mock_repo.get_alerts_summary.return_value = {'total': 1}
    mock_repo.get_alerts_timeline.return_value = []
    mock_repo.get_alerts_count.return_value = 1
    mock_repo.get_latest_pipeline_run_id.return_value = str(run_uuid)
    mock_repo.get_alerts.return_value = [{
        'id': 'al-1', 'rule_id': 'ru-1', 'type': 'security', 'severity': 'high',
        'title': 't', 'description': 'd', 'threshold': {}, 'status': 'active', 'asset_count': 0,
        'assets_by_category': {}, 'data_sources': [], 'asset_ids': [],
        'source': 'system', 'created_at': None, 'updated_at': None,
        'resolved_at': None, 'evidence': None, 'last_pipeline_run_id': run_uuid,
    }]
    resp = service.get_alerts_response()
    assert resp['latest_pipeline_run_id'] == str(run_uuid)
    assert resp['alerts'][0]['last_pipeline_run_id'] == str(run_uuid)
    assert isinstance(resp['alerts'][0]['last_pipeline_run_id'], str)


# --- Delivery enqueue hook (#917 Task 7) ---

def test_created_alert_enqueues_delivery():
    from src.modules.alerts.service import AlertsService
    repo = MagicMock()
    repo.execute_filter_rule.return_value = [{'id': 'asset-1', 'category': 'Server'}]
    repo.get_active_alert_for_rule.return_value = None      # -> created branch
    repo.create_alert.return_value = {
        'id': 'alert-1', 'created_at': datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc)}
    delivery = MagicMock()
    svc = AlertsService(repo, delivery_service=delivery)
    rule = {'id': 'r1', 'slug': 's', 'name': 'n', 'type': 'compliance',
            'severity': 'high', 'description': 'd', 'threshold': {}, 'source': 'system'}
    result = svc.evaluate_rule(rule)
    assert result == 'created'
    delivery.enqueue_for_alert.assert_called_once()
    alert_arg, rule_arg = delivery.enqueue_for_alert.call_args.args[:2]
    assert alert_arg['id'] == 'alert-1' and alert_arg['severity'] == 'high'
    assert rule_arg['id'] == 'r1'


def test_unchanged_and_resolved_do_not_enqueue():
    from src.modules.alerts.service import AlertsService
    repo = MagicMock()
    repo.execute_filter_rule.return_value = [{'id': 'asset-1'}]
    repo.get_active_alert_for_rule.return_value = {'id': 'alert-1', 'asset_ids': ['asset-1']}
    delivery = MagicMock()
    svc = AlertsService(repo, delivery_service=delivery)
    rule = {'id': 'r1', 'slug': 's', 'name': 'n', 'type': 'compliance',
            'severity': 'high', 'description': 'd', 'threshold': {}}
    assert svc.evaluate_rule(rule) == 'unchanged'      # same asset set
    delivery.enqueue_for_alert.assert_not_called()


def test_resolved_alert_does_not_enqueue():
    from src.modules.alerts.service import AlertsService
    repo = MagicMock()
    repo.execute_filter_rule.return_value = []      # condition no longer met
    repo.get_active_alert_for_rule.return_value = {'id': 'alert-1', 'asset_ids': ['asset-1']}
    delivery = MagicMock()
    svc = AlertsService(repo, delivery_service=delivery)
    rule = {'id': 'r1', 'slug': 's', 'name': 'n', 'type': 'compliance',
            'severity': 'high', 'description': 'd', 'threshold': {}}
    result = svc.evaluate_rule(rule)
    assert result == 'resolved'
    repo.resolve_alert.assert_called_once_with('alert-1')
    delivery.enqueue_for_alert.assert_not_called()


def test_no_delivery_service_is_noop():
    from src.modules.alerts.service import AlertsService
    repo = MagicMock()
    repo.execute_filter_rule.return_value = [{'id': 'asset-1'}]
    repo.get_active_alert_for_rule.return_value = None
    repo.create_alert.return_value = {
        'id': 'alert-1', 'created_at': datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc)}
    svc = AlertsService(repo)      # no delivery service
    assert svc.evaluate_rule({'id': 'r1', 'slug': 's', 'name': 'n', 'type': 'compliance',
                              'severity': 'high', 'description': 'd', 'threshold': {}}) == 'created'
    # No crash; nothing to assert beyond the successful create.


def test_dispatched_reason_matches_what_the_alerts_api_computes():
    """THE regression test for this whole change.

    #979 put the plain-language 'why this fired' in `reason`, but the delivery dict
    hardcoded reason=None, so every OOTB and custom alert notified with no reason for
    two releases while the card showed it. Nothing caught it because the read path and
    the dispatch path were never pinned to each other.

    This asserts the channel receives exactly what GET /api/alerts computes for the
    same alert. If the two paths ever drift again, this fails."""
    from unittest.mock import MagicMock
    from datetime import datetime, timezone
    from src.modules.alerts.service import AlertsService

    repo = MagicMock()
    repo.get_active_alert_for_rule.return_value = None
    repo.create_alert.return_value = {
        'id': 'alert-1',
        'created_at': datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc),
    }
    repo.execute_filter_rule.return_value = [
        {'id': 'a1', 'asset_label': 'web-prod-01', 'category': 'Server', 'data_source': 'S1'},
        {'id': 'a2', 'asset_label': 'db-prod-02', 'category': 'Server', 'data_source': 'S1'},
        {'id': 'a3', 'asset_label': 'vm-build-07', 'category': 'Server', 'data_source': 'S1'},
    ]
    delivery = MagicMock()
    service = AlertsService(repo, delivery)

    rule = {
        'id': 'r1', 'slug': 'disk-low', 'name': 'Disk low', 'type': 'compliance',
        'severity': 'high', 'description': 'Assets are below the configured threshold.',
        'source': 'system', 'subject_type': 'asset',
        'threshold': {'condition': 'exceeds', 'value': 2},
        'target_filters': [{'field': 'static:category', 'op': 'equals', 'value': 'Server'}],
        'compliance_criteria': None, 'silence_duration': None,
    }
    service._evaluate_match_result(rule, repo.execute_filter_rule.return_value, None)

    dispatched = delivery.enqueue_for_alert.call_args.args[0]

    # The bug itself: reason must not be None.
    assert dispatched['reason']
    # And it must be byte-identical to what the card shows for the same alert.
    expected = AlertsService._compute_reason({
        'threshold': dispatched['threshold'],
        'subject_type': 'asset',
        'asset_count': 3,
        'evidence': None,
    })
    assert dispatched['reason'] == expected
    assert dispatched['reason'] == '3 assets — above the threshold of 2'


def test_dispatched_alert_carries_labelled_assets_and_real_created_at():
    """Assets by label, never UUID (#979 copy contract), and the DB's detect-time —
    not a dispatch-time stamp, which the pipeline's long transaction would push
    minutes away from what the card shows."""
    from unittest.mock import MagicMock
    from datetime import datetime, timezone
    from src.modules.alerts.service import AlertsService

    created = datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc)
    repo = MagicMock()
    repo.get_active_alert_for_rule.return_value = None
    repo.create_alert.return_value = {'id': 'alert-1', 'created_at': created}
    matches = [
        {'id': 'a2', 'asset_label': 'db-prod-02', 'category': 'Server', 'data_source': 'S1'},
        {'id': 'a1', 'asset_label': 'web-prod-01', 'category': 'Server', 'data_source': 'S1'},
    ]
    delivery = MagicMock()
    service = AlertsService(repo, delivery)
    rule = {
        'id': 'r1', 'slug': 's', 'name': 'n', 'type': 'compliance', 'severity': 'high',
        'description': 'd', 'source': 'system', 'subject_type': 'asset',
        'threshold': {'condition': 'exceeds', 'value': 1},
        'target_filters': [], 'compliance_criteria': None, 'silence_duration': None,
    }
    service._evaluate_match_result(rule, matches, None)

    dispatched = delivery.enqueue_for_alert.call_args.args[0]
    assert dispatched['created_at'] == created
    # Sorted by id, and asset_ids stays aligned with assets by construction.
    assert dispatched['assets'] == [
        {'id': 'a1', 'label': 'web-prod-01'},
        {'id': 'a2', 'label': 'db-prod-02'},
    ]
    assert dispatched['asset_ids'] == ['a1', 'a2']


def test_sql_query_rule_resolves_asset_labels_for_delivery():
    """evaluate_rule has TWO match sources, and only one of them projects the label.

    A rule carrying its own `query` runs that stored SQL untouched, and no seeded rule
    projects asset_label (migrations 037 and 119 have zero). evaluation_type even
    DEFAULTS to 'sql' (migration 084), so this is the dominant rule shape rather than
    an edge case: without resolving the labels here, every asset in the notification
    renders as "Unknown" — worse than before this branch, which named nobody rather
    than confidently asserting a wrong name for everybody.

    Resolved through the read path's own query, so the channel names assets exactly as
    the card does."""
    a1 = uuid.UUID('11111111-1111-1111-1111-111111111111')
    a2 = uuid.UUID('22222222-2222-2222-2222-222222222222')
    repo = MagicMock()
    repo.get_active_alert_for_rule.return_value = None
    repo.create_alert.return_value = {
        'id': 'alert-1', 'created_at': datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc)}
    # Shaped as a seeded rule's SQL actually returns: no asset_label key at all, and
    # ids as UUID objects rather than the strings the payload pairs by index.
    repo.execute_rule_query.return_value = [
        {'id': a2, 'hostname': 'db-prod-02', 'category': 'Server'},
        {'id': a1, 'hostname': 'web-prod-01', 'category': 'Server'},
    ]
    # get_asset_details is the card's own query: UUID ids, label aliased `label`.
    repo.get_asset_details.return_value = [
        {'id': a1, 'label': 'FINANCE-DB-PRIMARY'},
        {'id': a2, 'label': 'db-prod-02'},
    ]
    delivery = MagicMock()
    service = AlertsService(repo, delivery)
    rule = {
        'id': 'r1', 'slug': 'stale-assets', 'name': 'n', 'type': 'compliance',
        'severity': 'high', 'description': 'd', 'source': 'system', 'subject_type': 'asset',
        'query': 'SELECT id, hostname[1] AS hostname, category FROM assets WHERE 1=1',
        'threshold': {'condition': 'exceeds', 'value': 1}, 'silence_duration': None,
    }
    assert service.evaluate_rule(rule) == 'created'

    dispatched = delivery.enqueue_for_alert.call_args.args[0]
    # Real labels — including the override the card shows. Never None, never "Unknown".
    assert dispatched['assets'] == [
        {'id': str(a1), 'label': 'FINANCE-DB-PRIMARY'},
        {'id': str(a2), 'label': 'db-prod-02'},
    ]
    # asset_ids stays index-aligned with assets: the payload pairs them by index.
    assert dispatched['asset_ids'] == [str(a1), str(a2)]
    # One batched query for the whole alert — not one per asset.
    assert repo.get_asset_details.call_count == 1


def test_filter_rule_label_of_none_does_not_trigger_a_second_query():
    """The discriminator is the KEY's absence, not a null value.

    execute_filter_rule always projects asset_label, so re-querying would be a wasted
    round-trip on every firing filter rule. And an asset with no override, hostname,
    fqdn, MAC, serial or IP has a genuinely null canonical label — "every label is
    None" would re-query for it on every run and still learn nothing, while the key's
    absence says precisely which source the rows came from."""
    repo = MagicMock()
    repo.get_active_alert_for_rule.return_value = None
    repo.create_alert.return_value = {
        'id': 'alert-1', 'created_at': datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc)}
    repo.execute_filter_rule.return_value = [
        {'id': 'a1', 'asset_label': None, 'category': 'Server', 'data_source': 'S1'},
    ]
    delivery = MagicMock()
    service = AlertsService(repo, delivery)
    rule = {
        'id': 'r1', 'slug': 's', 'name': 'n', 'type': 'compliance', 'severity': 'high',
        'description': 'd', 'source': 'system', 'subject_type': 'asset',
        'threshold': {'condition': 'exceeds', 'value': 0},
        'target_filters': [], 'compliance_criteria': None, 'silence_duration': None,
    }
    assert service.evaluate_rule(rule) == 'created'

    repo.get_asset_details.assert_not_called()
    dispatched = delivery.enqueue_for_alert.call_args.args[0]
    assert dispatched['assets'] == [{'id': 'a1', 'label': None}]


def test_label_resolution_failure_still_delivers_the_alert():
    """A label lookup that fails must cost labels, not the notification.

    _resolve_asset_labels runs after create_alert has already committed, and this
    created branch is the only one that ever reaches _dispatch_delivery — the next
    run finds the alert active and returns unchanged/updated, neither of which
    dispatches. So an exception escaping here doesn't delay the notification, it
    loses it permanently and silently. The channels degrade to "Unknown" instead.
    """
    from unittest.mock import MagicMock
    from datetime import datetime, timezone
    from src.modules.alerts.service import AlertsService

    repo = MagicMock()
    repo.get_active_alert_for_rule.return_value = None
    repo.create_alert.return_value = {
        'id': 'alert-1',
        'created_at': datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc),
    }
    # A stored-SQL rule's rows carry no asset_label key, so resolution is attempted.
    matches = [{'id': 'a1', 'category': 'Server'}, {'id': 'a2', 'category': 'Server'}]
    repo.get_asset_details.side_effect = Exception('connection reset by peer')

    delivery = MagicMock()
    service = AlertsService(repo, delivery)
    rule = {
        'id': 'r1', 'slug': 's', 'name': 'n', 'type': 'compliance', 'severity': 'high',
        'description': 'd', 'source': 'system', 'subject_type': 'asset',
        'threshold': {'condition': 'exceeds', 'value': 1},
        'target_filters': [], 'compliance_criteria': None, 'silence_duration': None,
    }

    assert service._evaluate_match_result(rule, matches, None) == 'created'

    delivery.enqueue_for_alert.assert_called_once()
    dispatched = delivery.enqueue_for_alert.call_args.args[0]
    # The reason — the whole point of this branch — still goes out.
    assert dispatched['reason'] == '2 assets — above the threshold of 1'
    # Labels degrade to None; the email renders "Unknown" rather than nothing at all.
    assert dispatched['assets'] == [{'id': 'a1', 'label': None},
                                    {'id': 'a2', 'label': None}]
