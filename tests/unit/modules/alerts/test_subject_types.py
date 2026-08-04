# SPDX-License-Identifier: AGPL-3.0-or-later
from unittest.mock import MagicMock
from src.modules.alerts.repository import AlertsRepository
from src.modules.alerts.service import AlertsService


def test_create_alert_persists_subject_type_and_subjects():
    db = MagicMock()
    db.execute_query.return_value = [{'id': 'a1'}]
    repo = AlertsRepository(db)
    repo.create_alert(
        rule_id='r1', alert_type='operational', severity='high',
        title='t', description='d', threshold={}, asset_count=2,
        assets_by_category={}, data_sources=[], asset_ids=[],
        subject_type='integration',
        subjects=[{'id': 'i1', 'label': 'CrowdStrike', 'sublabel': 'EDR · sync failed'}],
    )
    sql, params = db.execute_query.call_args[0][0], db.execute_query.call_args[0][1]
    assert 'subject_type' in sql and 'subjects' in sql
    assert params['subject_type'] == 'integration'
    assert '"CrowdStrike"' in params['subjects']  # json-encoded


def _svc():
    return AlertsService(MagicMock())


def _rule(subject_type, slug='r'):
    return {'id': 'r1', 'slug': slug, 'name': 'n', 'description': 'd', 'type': 'operational',
            'severity': 'high', 'query': 'SELECT 1', 'threshold': {'min': 1}, 'source': 'system',
            'target_filters': None, 'compliance_criteria': None, 'silence_duration': None,
            'subject_type': subject_type}


def test_integration_rule_builds_subjects_not_assets():
    svc = _svc(); repo = svc.repo
    repo.get_active_alert_for_rule.return_value = None
    matches = [{'subject_id': 'i1', 'subject_label': 'CrowdStrike', 'subject_sublabel': 'EDR · sync failed'}]
    assert svc._evaluate_match_result(_rule('integration'), matches) == 'created'
    _, kwargs = repo.create_alert.call_args
    assert kwargs['subject_type'] == 'integration'
    assert kwargs['subjects'] == [{'id': 'i1', 'label': 'CrowdStrike', 'sublabel': 'EDR · sync failed'}]
    assert kwargs['asset_ids'] == [] and kwargs['asset_count'] == 1


def test_summary_only_rule_stores_no_entities():
    svc = _svc(); repo = svc.repo
    repo.get_active_alert_for_rule.return_value = None
    matches = [{'id': 'run-1'}]
    assert svc._evaluate_match_result(_rule('pipeline_run'), matches) == 'created'
    _, kwargs = repo.create_alert.call_args
    assert kwargs['subject_type'] == 'pipeline_run'
    assert kwargs['subjects'] is None and kwargs['asset_ids'] == [] and kwargs['asset_count'] == 0


def test_reason_is_subject_aware():
    svc = _svc()
    assert 'data source' in svc._compute_reason(
        {'subject_type': 'integration', 'asset_count': 2, 'threshold': {}})
    assert 'asset' in svc._compute_reason(
        {'subject_type': 'asset', 'asset_count': 3, 'threshold': {}})


def test_integration_rule_unchanged_when_subjects_match():
    """The typed-list unchanged-short-circuit: an integration alert whose existing
    subjects match the fresh matches must return 'unchanged' and skip the rewrite.
    Requires get_active_alert_for_rule to SELECT subjects; psycopg2 hands the jsonb
    back as a parsed list of dicts, so _subject_identity's s['id'] resolves directly."""
    svc = _svc(); repo = svc.repo
    matches = [{'subject_id': 'i1', 'subject_label': 'CrowdStrike', 'subject_sublabel': 'EDR · sync failed'}]
    repo.get_active_alert_for_rule.return_value = {
        'id': 'a1', 'asset_ids': [],
        'subjects': [{'id': 'i1', 'label': 'CrowdStrike', 'sublabel': 'EDR · sync failed'}],
    }
    assert svc._evaluate_match_result(_rule('integration'), matches) == 'unchanged'
    repo.update_alert_assets.assert_not_called()


def test_get_active_alert_for_rule_selects_subjects():
    """Guards the fix that feeds the short-circuit: without subjects in this SELECT
    the service's _subject_identity always sees existing subjects=None and rewrites
    every typed-list alert each run. The service test above mocks the repo, so this
    is what would catch a re-drop of the column."""
    db = MagicMock()
    db.execute_query.return_value = []
    AlertsRepository(db).get_active_alert_for_rule('r1')
    sql = db.execute_query.call_args.args[0]
    assert 'subject_type' in sql and 'subjects' in sql


def test_get_enabled_rules_selects_subject_type():
    """Guards the fix: without subject_type in this SELECT, every rule dict reaches
    the evaluator with no subject_type key, so rule.get('subject_type') defaults to
    'asset' — typed-list rules (integration/audit_event) crash KeyError 'id' in the
    asset branch, and summary-only rules (pipeline_run/metric/system) mis-render as
    asset. The service tests above hand-build the rule dict via the _rule() helper
    (which already includes subject_type), bypassing get_enabled_rules entirely, so
    this is what would catch a re-drop of the column from the real SELECT."""
    db = MagicMock()
    db.execute_query.return_value = []
    AlertsRepository(db).get_enabled_rules('pipeline')
    sql = db.execute_query.call_args.args[0]
    assert 'subject_type' in sql
