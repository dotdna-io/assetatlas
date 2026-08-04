# SPDX-License-Identifier: AGPL-3.0-or-later
import re

import pytest
from unittest.mock import MagicMock
from src.modules.alerts.repository import AlertsRepository, SystemRuleImmutableError


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def mock_compiler():
    return MagicMock()


@pytest.fixture
def repo(mock_db, mock_compiler):
    return AlertsRepository(mock_db, query_compiler=mock_compiler)


def test_get_all_rules_selects_new_columns(repo, mock_db):
    mock_db.execute_query.return_value = []
    repo.get_all_rules()
    sql = mock_db.execute_query.call_args.args[0]
    for col in ('target_filters', 'compliance_criteria', 'silence_duration', 'created_by'):
        assert col in sql


def test_get_enabled_rules_selects_new_columns(repo, mock_db):
    mock_db.execute_query.return_value = []
    repo.get_enabled_rules()
    sql = mock_db.execute_query.call_args.args[0]
    assert 'target_filters' in sql
    assert 'enabled = true' in sql


def test_get_enabled_rules_defaults_to_pipeline_cadence(repo, mock_db):
    """The pipeline evaluator (s20) must never pick up interval-cadence rules:
    the query filters on evaluation_cadence and defaults to 'pipeline'."""
    mock_db.execute_query.return_value = []
    repo.get_enabled_rules()
    sql, params = mock_db.execute_query.call_args.args
    assert 'evaluation_cadence = %(cadence)s' in sql
    assert params == {'cadence': 'pipeline'}


def test_get_enabled_rules_interval_cadence_passes_through(repo, mock_db):
    mock_db.execute_query.return_value = []
    repo.get_enabled_rules(cadence='interval')
    _, params = mock_db.execute_query.call_args.args
    assert params == {'cadence': 'interval'}


def test_create_rule_inserts_user_rule(repo, mock_db):
    mock_db.execute_query.return_value = [{'id': 'new-uuid'}]
    payload = {
        'name': 'Test',
        'slug': 'test',
        'description': '',
        'type': 'security',
        'severity': 'high',
        'target_filters': [{'field': 'x', 'op': '=', 'value': 'y'}],
        'compliance_criteria': None,
        'threshold': None,
        'silence_duration': None,
        'enabled': True,
    }
    result = repo.create_rule(payload, created_by='user-uuid')
    sql = mock_db.execute_query.call_args.args[0]
    assert 'INSERT INTO alert_rules' in sql
    assert result == {'id': 'new-uuid'}


def test_get_rule_by_id(repo, mock_db):
    mock_db.execute_query.return_value = [{'id': 'abc', 'name': 'X'}]
    result = repo.get_rule('abc')
    assert result == {'id': 'abc', 'name': 'X'}


def test_get_rule_returns_none_when_missing(repo, mock_db):
    mock_db.execute_query.return_value = []
    result = repo.get_rule('missing')
    assert result is None


def test_update_rule_user_rule_succeeds(repo, mock_db):
    mock_db.execute_query.side_effect = [
        [{'source': 'user'}],                                   # source check
        [{'id': 'abc', 'name': 'New Name', 'source': 'user'}],  # UPDATE returning
    ]
    result = repo.update_rule('abc', {'name': 'New Name'})
    assert result['name'] == 'New Name'


def test_update_rule_system_rule_raises(repo, mock_db):
    mock_db.execute_query.return_value = [{'source': 'system'}]
    with pytest.raises(SystemRuleImmutableError):
        repo.update_rule('abc', {'name': 'New Name'})


def test_update_rule_missing_returns_none(repo, mock_db):
    mock_db.execute_query.return_value = []
    result = repo.update_rule('missing', {'name': 'X'})
    assert result is None


def test_update_rule_anomaly_system_rejects_threshold_edit(repo, mock_db):
    # Threshold tuning moved to merge_anomaly_threshold (single writer, always merges);
    # update_rule's anomaly allowance is now enabled-only, so a threshold edit is rejected here.
    mock_db.execute_query.return_value = [{'source': 'system', 'evaluation_type': 'anomaly'}]
    with pytest.raises(SystemRuleImmutableError):
        repo.update_rule('a', {'threshold': {'z_medium': 4}})


def test_update_rule_anomaly_system_allows_enabled_toggle(repo, mock_db):
    mock_db.execute_query.side_effect = [
        [{'source': 'system', 'evaluation_type': 'anomaly'}],
        [{'id': 'a', 'enabled': False}],
    ]
    assert repo.update_rule('a', {'enabled': False}) is not None


def test_update_rule_anomaly_system_rejects_name_edit(repo, mock_db):
    mock_db.execute_query.return_value = [{'source': 'system', 'evaluation_type': 'anomaly'}]
    with pytest.raises(SystemRuleImmutableError):
        repo.update_rule('a', {'name': 'X'})


def test_update_rule_anomaly_system_rejects_threshold_plus_name(repo, mock_db):
    mock_db.execute_query.return_value = [{'source': 'system', 'evaluation_type': 'anomaly'}]
    with pytest.raises(SystemRuleImmutableError):
        repo.update_rule('a', {'threshold': {}, 'name': 'X'})


def test_update_rule_non_anomaly_system_stays_fully_immutable(repo, mock_db):
    mock_db.execute_query.return_value = [{'source': 'system', 'evaluation_type': 'sql'}]
    with pytest.raises(SystemRuleImmutableError):
        repo.update_rule('a', {'threshold': {}})


def test_get_alerts_selects_evidence(repo, mock_db):
    mock_db.execute_query.return_value = []
    repo.get_alerts()
    assert 'evidence' in mock_db.execute_query.call_args.args[0]


def test_delete_rule_user_rule_resolves_alerts(repo, mock_db):
    mock_db.execute_query.side_effect = [
        [{'source': 'user'}],  # source check
        [{'id': 'abc'}],        # combined resolve-then-delete CTE
    ]
    result = repo.delete_rule('abc')
    assert result is True
    # The CTE must both resolve active alerts and delete the rule
    delete_call = mock_db.execute_query.call_args_list[1]
    assert 'UPDATE alerts' in delete_call.args[0]
    assert "SET status = 'resolved'" in delete_call.args[0]
    assert 'DELETE FROM alert_rules' in delete_call.args[0]


def test_delete_rule_system_rule_raises(repo, mock_db):
    mock_db.execute_query.return_value = [{'source': 'system'}]
    with pytest.raises(SystemRuleImmutableError):
        repo.delete_rule('abc')


def test_delete_rule_missing_returns_false(repo, mock_db):
    mock_db.execute_query.return_value = []
    result = repo.delete_rule('missing')
    assert result is False


def test_execute_filter_rule_uses_compiler(repo, mock_db, mock_compiler):
    target_filters = [{'field': 'category', 'op': '=', 'value': 'Server'}]
    mock_compiler.build_asset_query.return_value = ('SELECT id FROM assets WHERE 1=1', {})
    mock_db.execute_query.return_value = [{'id': 'asset-1'}]
    result = repo.execute_filter_rule(target_filters, None)
    assert result == [{'id': 'asset-1'}]
    mock_compiler.build_asset_query.assert_called_once()
    # Verify the shape translation happened (field/op -> field_id/operator) and
    # that the operator spelling is normalized to its canonical registry code
    # (the '=' alias resolves to 'eq') so a label/code mix can't reach the
    # compiler as a phantom (spec §8 invariant 1).
    args = mock_compiler.build_asset_query.call_args
    passed_filters = args.args[0]
    assert passed_filters[0] == {'field_id': 'category', 'operator': 'eq', 'value': 'Server'}


def test_count_target_universe_uses_compiler(repo, mock_db, mock_compiler):
    target_filters = [{'field': 'category', 'op': '=', 'value': 'Server'}]
    mock_compiler.build_count_query.return_value = ('SELECT COUNT(*) FROM assets', {})
    mock_db.execute_query.return_value = [{'count': 42}]
    result = repo.count_target_universe(target_filters)
    assert result == 42


def test_execute_filter_rule_without_compiler_raises():
    repo_no_compiler = AlertsRepository(MagicMock(), query_compiler=None)
    with pytest.raises(RuntimeError, match='AssetQueryCompiler'):
        repo_no_compiler.execute_filter_rule([{'field': 'x', 'op': '=', 'value': 'y'}], None)


def _real_compiler():
    """A real AssetQueryCompiler over a DB-free catalog. Static fields
    ("Category", "Data Source") resolve without DB rows, so execute_filter_rule
    compiles actual SQL and we can assert an encoded-string field produced a
    predicate rather than a silently dropped clause."""
    from src.modules.shared.asset_query_compiler import AssetQueryCompiler
    from src.modules.fields import FieldCatalog, FieldResolver
    catalog_db = MagicMock()
    catalog_db.execute_query.return_value = []
    return AssetQueryCompiler(resolver=FieldResolver(catalog=FieldCatalog(db_adapter=catalog_db)))


def test_execute_filter_rule_resolves_encoded_string_fields():
    """#822: stored filter rules keep target_filters[].field as the encoded
    string the wizard writes via encodeFieldId ("static:Category"), and the same
    for compliance_criteria conditions. execute_filter_rule must compile those
    into WHERE predicates, not silently drop them — a dropped clause yields a
    WHERE-less query that matches every asset (73,739 vs 62,442 on dev).

    Regression tripwire: on pre-fix code the encoded strings resolve to nothing,
    build_asset_query emits no WHERE, and every assertion below fails.
    """
    db = MagicMock()
    db.execute_query.return_value = []
    repo = AlertsRepository(db, query_compiler=_real_compiler())

    target_filters = [
        {'field': 'static:Category', 'op': 'any', 'value': ['Server', 'Workstation']},
    ]
    compliance_criteria = {
        'conditions': [
            {'field': 'static:Data Source', 'op': 'none', 'value': ['crowdstrike']},
        ],
    }

    repo.execute_filter_rule(target_filters, compliance_criteria)

    sql, params = db.execute_query.call_args.args
    # The encoded-string fields compiled into real predicates, not a dropped
    # (WHERE-less) query.
    assert 'WHERE' in sql
    assert 'LOWER(category) IN' in sql            # target filter field resolved
    # `unnest(data_source)` is the compliance criterion's WHERE predicate — assert
    # on it, NOT the bare `data_source` column (which is always in select_columns,
    # so it would pass even if the criterion were dropped — the #822 failure mode).
    assert 'unnest(data_source)' in sql           # compliance criterion resolved into a predicate
    assert {'Server', 'Workstation'} <= set(params.values())
    assert 'crowdstrike' in params.values()


def test_count_target_universe_resolves_encoded_string_fields():
    """#822: the percent-threshold denominator (count_target_universe) must also
    resolve encoded-string fields. If it drops them, the target universe becomes
    the whole fleet and every compliance ratio is silently understated."""
    db = MagicMock()
    db.execute_query.return_value = [{'count': 3}]
    repo = AlertsRepository(db, query_compiler=_real_compiler())

    repo.count_target_universe(
        [{'field': 'static:Category', 'op': 'any', 'value': ['Server']}])

    sql, params = db.execute_query.call_args.args
    assert 'WHERE' in sql
    assert 'LOWER(category) IN' in sql
    assert 'Server' in params.values()


class TestCreateRuleEvaluationType:
    """Regression for #584 §1 (#550): wizard rules were inserted without
    evaluation_type, defaulting to 'sql' and violating alert_rules_path_check."""

    def test_wizard_rule_inserts_filter_evaluation_type(self):
        db = MagicMock()
        db.execute_query.return_value = [{'id': 'r1'}]
        repo = AlertsRepository(db)

        payload = {
            'name': 'Unmanaged servers',
            'type': 'security',
            'severity': 'high',
            'target_filters': [{'field': 'category', 'op': 'equals', 'value': 'Server'}],
        }
        repo.create_rule(payload, created_by='11111111-1111-1111-1111-111111111111')

        sql, params = db.execute_query.call_args[0]
        assert 'evaluation_type' in sql, 'INSERT must set evaluation_type explicitly'
        assert "'filter'" in sql, "user/wizard rules are always the filter path"


class TestCreateAlertEvidenceFingerprint:
    """#584 §4 (anomaly): create_alert grows optional evidence/fingerprint so
    anomaly findings persist their explainable payload + dedup key — without
    disturbing existing (positional) callers."""

    def test_create_alert_persists_evidence_and_fingerprint(self, repo, mock_db):
        mock_db.execute_query.return_value = [{'id': 'a1'}]
        repo.create_alert(
            'rule-1', 'security', 'high', 'title', 'desc', {'k': 1},
            3, {'Server': 3}, ['SentinelOne'], [],
            evidence={'observed': 1403, 'score': {'z': 29.8}},
            fingerprint='anomaly:mass_new_assets::2026-06-01',
        )
        sql, params = mock_db.execute_query.call_args.args
        assert 'evidence' in sql and 'fingerprint' in sql
        assert '%(evidence)s::jsonb' in sql and '%(fingerprint)s' in sql
        assert params['fingerprint'] == 'anomaly:mass_new_assets::2026-06-01'
        # evidence is json-dumped (a string) so it lands in the jsonb column.
        assert isinstance(params['evidence'], str) and '1403' in params['evidence']

    def test_create_alert_sanitizes_nan_in_evidence(self, repo, mock_db):
        mock_db.execute_query.return_value = [{'id': 'a2'}]
        repo.create_alert(
            'rule-1', 'operational', 'medium', 't', 'd', {}, 0, {}, [], [],
            evidence={'z': float('nan'), 'observed': 5},
        )
        params = mock_db.execute_query.call_args.args[1]
        # _clean_nan turns NaN into null so psycopg2 emits valid JSON.
        assert 'NaN' not in params['evidence'] and 'null' in params['evidence']

    def test_create_alert_backward_compatible_without_new_args(self, repo, mock_db):
        mock_db.execute_query.return_value = [{'id': 'a3'}]
        result = repo.create_alert(
            'rule-1', 'operational', 'medium', 't', 'd', {}, 0, {}, [], [],
        )
        assert result == {'id': 'a3'}
        params = mock_db.execute_query.call_args.args[1]
        assert params['evidence'] is None and params['fingerprint'] is None

    def test_update_alert_evidence_sets_severity_and_evidence(self, repo, mock_db):
        repo.update_alert_evidence('alert-1', 'critical', {'observed': 9, 'z': float('nan')})
        sql, params = mock_db.execute_query.call_args.args
        assert 'evidence = %(evidence)s::jsonb' in sql and 'severity = %(severity)s' in sql
        assert params['alert_id'] == 'alert-1' and params['severity'] == 'critical'
        assert 'NaN' not in params['evidence'] and mock_db.execute_query.call_args.kwargs[
            'fetch'] is False


def test_get_enabled_rules_excludes_prometheus_source(repo, mock_db):
    """Prometheus-synced rows hold PromQL in `query` (sync never set
    evaluation_type, so they default to 'sql') and their alerts arrive via the
    Alertmanager webhook — DB-evaluating them fails the SELECT-only guard on
    every run (11 errors/run observed on the dev DB). Both cadence loops must
    exclude them."""
    mock_db.execute_query.return_value = []
    repo.get_enabled_rules()
    sql = mock_db.execute_query.call_args.args[0]
    assert "source != 'prometheus'" in sql


def test_append_alert_event_inserts_and_stamps(repo, mock_db):
    repo.append_alert_event(
        alert_id='al-1', rule_id='ru-1', pipeline_run_id='run-1',
        status='active', severity='high', asset_count=7,
    )
    # Single atomic CTE: one round trip that both inserts the event and stamps
    # alerts.last_pipeline_run_id (so the stamp can never lag the event row).
    assert mock_db.execute_query.call_count == 1
    sql, params = mock_db.execute_query.call_args.args[0:2]
    assert 'INSERT INTO alert_events' in sql
    assert 'UPDATE alerts' in sql and 'last_pipeline_run_id' in sql
    assert params['alert_id'] == 'al-1' and params['rule_id'] == 'ru-1'
    assert params['pipeline_run_id'] == 'run-1'
    assert params['status'] == 'active' and params['asset_count'] == 7


def test_get_alert_history_joins_runs_and_windows(repo, mock_db):
    mock_db.execute_query.return_value = []
    repo.get_alert_history('al-1', days=30)
    sql, params = mock_db.execute_query.call_args.args
    assert 'FROM alert_events' in sql and 'JOIN pipeline_runs' in sql
    # Most-recent `limit` rows (inner DESC), presented oldest->newest (outer ASC),
    # so a dense-series alert's chart keeps the latest runs, not the oldest.
    assert 'ORDER BY pr.started_at DESC' in sql
    assert 'ORDER BY started_at ASC' in sql
    assert params['alert_id'] == 'al-1' and params['days'] == 30


def test_get_latest_pipeline_run_id_returns_completed(repo, mock_db):
    mock_db.execute_query.return_value = [{'id': 'run-9'}]
    assert repo.get_latest_pipeline_run_id() == 'run-9'
    sql = mock_db.execute_query.call_args.args[0]
    assert "status = 'completed'" in sql and 'ORDER BY completed_at DESC' in sql


def test_get_latest_pipeline_run_id_none_when_no_runs(repo, mock_db):
    mock_db.execute_query.return_value = []
    assert repo.get_latest_pipeline_run_id() is None


def test_get_alerts_selects_last_pipeline_run_id(repo, mock_db):
    mock_db.execute_query.return_value = []
    repo.get_alerts()
    assert 'last_pipeline_run_id' in mock_db.execute_query.call_args.args[0]


def test_create_alert_returns_id_and_created_at(repo, mock_db):
    """Channels need the alert's real detect-time. alerts.created_at defaults to
    Postgres now() = transaction-start, and the pipeline evaluates alerts inside one
    long transaction, so a dispatch-time stamp would disagree with the card by
    minutes. RETURNING is the only source that matches what the UI shows."""
    from datetime import datetime, timezone
    created = datetime(2026, 7, 16, 14, 32, 5, tzinfo=timezone.utc)
    mock_db.execute_query.return_value = [{'id': 'alert-1', 'created_at': created}]
    row = repo.create_alert(
        rule_id='r1', alert_type='security', severity='high', title='t',
        description='d', threshold={}, asset_count=0, assets_by_category={},
        data_sources=[], asset_ids=[])
    assert row == {'id': 'alert-1', 'created_at': created}
    assert 'RETURNING id, created_at' in mock_db.execute_query.call_args.args[0]


def test_create_alert_returns_none_when_insert_yields_no_row(repo, mock_db):
    """Callers unpack row['id']; a None return must stay distinguishable from a row
    rather than raising a TypeError deep in the dispatch path."""
    mock_db.execute_query.return_value = []
    assert repo.create_alert(
        rule_id='r1', alert_type='security', severity='high', title='t',
        description='d', threshold={}, asset_count=0, assets_by_category={},
        data_sources=[], asset_ids=[]) is None


def test_get_asset_details_aliases_the_label_as_the_key_the_card_reads(repo, mock_db):
    """The alert card's Involved Assets grid must name an asset the same way the rest
    of the product does — including an override someone deliberately set.

    get_alerts_response passes these rows through untouched as alert['assets'], so this
    alias IS the emitted API key, and the card reads `asset.label` (AlertAsset.label,
    ui/prod/src/api/alerts.ts:15 — AlertsPage.tsx:285). Aliasing it `asset_label`
    leaves `asset.label` undefined, so the grid silently stays on its hostname fallback
    while the outbound channels announce the override — inverting the very card/channel
    parity this exists for. Asserting the alias the CONSUMER reads (rather than merely
    that some asset_label expression is present) is the whole point: the previous
    version of this test passed against exactly that bug."""
    mock_db.execute_query.return_value = []
    repo.get_asset_details(['11111111-1111-1111-1111-111111111111'])
    sql = mock_db.execute_query.call_args.args[0]
    assert re.search(r'\bAS\s+label\b', sql), 'the card reads asset.label'
    assert not re.search(r'\bAS\s+asset_label\b', sql), 'asset.asset_label is read by nobody'
    assert 'asset_label_override' in sql   # still the catalog's canonical expression


def test_execute_filter_rule_projects_canonical_asset_label(repo, mock_db, mock_compiler):
    """The rule dispatch path builds the delivery payload's labelled asset list off
    these match rows. Projecting the label here keeps build_alert_payload's 'no extra
    DB read' invariant — no second query just to name the assets."""
    mock_compiler.build_asset_query.return_value = ('SELECT 1', {})
    mock_db.execute_query.return_value = []
    repo.execute_filter_rule([{'field': 'static:category', 'op': 'equals', 'value': 'Server'}], None)
    select_columns = mock_compiler.build_asset_query.call_args.kwargs['select_columns']
    assert any('AS asset_label' in c for c in select_columns)
    assert any('asset_label_override' in c for c in select_columns)
