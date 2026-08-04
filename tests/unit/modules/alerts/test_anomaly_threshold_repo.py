# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard + wiring for merge_anomaly_threshold — the merge SQL itself (jsonb ||)
is proven against Postgres in tests/integration/test_alert_rules_api.py."""
import json
from unittest.mock import MagicMock

from src.modules.alerts.repository import AlertsRepository


def _repo():
    return AlertsRepository(MagicMock())


def test_merge_returns_none_for_missing_rule():
    repo = _repo()
    repo._get_rule_source = MagicMock(return_value=None)
    assert repo.merge_anomaly_threshold('r1', {'pct_medium': 0.5}) is None


def test_merge_returns_none_for_non_anomaly_rule():
    repo = _repo()
    repo._get_rule_source = MagicMock(return_value={'source': 'system', 'evaluation_type': 'sql'})
    assert repo.merge_anomaly_threshold('r1', {'pct_medium': 0.5}) is None


def test_merge_uses_jsonb_concat_not_replace():
    """Regression for #878 Gap 1: the write must MERGE (jsonb ||), never replace,
    or a partial edit would drop other threshold params."""
    repo = _repo()
    repo._get_rule_source = MagicMock(return_value={'source': 'system', 'evaluation_type': 'anomaly'})
    repo.db.execute_query = MagicMock(return_value=[{'id': 'r1', 'threshold': {'pct_medium': 0.5}}])
    out = repo.merge_anomaly_threshold('r1', {'pct_medium': 0.5})
    assert out == {'id': 'r1', 'threshold': {'pct_medium': 0.5}}
    sql, params = repo.db.execute_query.call_args[0]
    # Merge, not replace: the stored jsonb is concatenated with the patch via ||,
    # so unspecified keys survive. A plain `threshold = %(patch)s::jsonb` would strip them.
    assert '|| %(patch)s::jsonb' in sql
    assert params['patch'] == json.dumps({'pct_medium': 0.5})


def test_noop_patch_returns_current_rule():
    repo = _repo()
    repo._get_rule_source = MagicMock(return_value={'source': 'system', 'evaluation_type': 'anomaly'})
    repo.get_rule = MagicMock(return_value={'id': 'r1'})
    assert repo.merge_anomaly_threshold('r1', {}) == {'id': 'r1'}
    repo.get_rule.assert_called_once_with('r1')


def test_merge_returns_none_for_non_system_rule():
    """Only system-owned anomaly rules are tunable — a (hypothetical) user-owned anomaly
    rule is rejected, matching update_rule's system-immutability guard. #879 review."""
    repo = _repo()
    repo._get_rule_source = MagicMock(return_value={'source': 'user', 'evaluation_type': 'anomaly'})
    assert repo.merge_anomaly_threshold('r1', {'pct_medium': 0.5}) is None
