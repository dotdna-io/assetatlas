# SPDX-License-Identifier: AGPL-3.0-or-later
"""
End-to-end integration test for the alert rule evaluator.

Verifies the full loop:
    create user rule → call evaluate_rule directly → assert alert row is active
    → toggle rule off via PATCH → assert alert is resolved

Fixtures from tests/integration/conftest.py (session-scoped):
    live_client, live_admin_headers, live_db
"""

import pytest

pytestmark = pytest.mark.integration


def test_user_rule_fires_and_resolves(live_client, live_admin_headers, live_db):
    """Full loop: create rule → evaluate → alert exists → toggle off → alert resolved."""
    # Category 'server' (lowercase) is guaranteed to exist in the live DB
    # (1200+ rows). Use op='equals' so the compiler builds a real WHERE clause.
    resp = live_client.post(
        '/api/alert-rules',
        headers=live_admin_headers,
        json={
            'name': 'E2E Evaluator Test Rule',
            'type': 'security',
            'severity': 'high',
            'target_filters': [{'field': 'category', 'op': 'equals', 'value': 'server'}],
        },
    )
    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    rule = body.get('data') or body
    rule_id = rule['id']

    try:
        # Trigger evaluation via the service directly (skips the full pipeline).
        with live_client.application.app_context():
            from flask import current_app
            svc = current_app.alerts_service
            rule_obj = svc.repo.get_rule(rule_id)
            assert rule_obj is not None, f"Rule {rule_id} not found after creation"
            result = svc.evaluate_rule(rule_obj)

        assert result == 'created', f"expected 'created', got {result!r}"

        # Alert row must exist with status='active'
        alerts = live_db.execute_query(
            "SELECT status FROM alerts WHERE rule_id = %(rid)s::uuid",
            {'rid': rule_id},
        )
        assert len(alerts) == 1, f"expected 1 alert row, got {len(alerts)}"
        assert alerts[0]['status'] == 'active'

        # Toggle off → service resolves active alerts before disabling the rule
        resp = live_client.patch(
            f'/api/alert-rules/{rule_id}',
            headers=live_admin_headers,
            json={'enabled': False},
        )
        assert resp.status_code == 200, resp.get_json()

        alerts = live_db.execute_query(
            "SELECT status FROM alerts WHERE rule_id = %(rid)s::uuid",
            {'rid': rule_id},
        )
        assert len(alerts) == 1
        assert alerts[0]['status'] == 'resolved'

    finally:
        # Cleanup: delete the rule (repo.delete_rule resolves alerts first)
        live_client.delete(f'/api/alert-rules/{rule_id}', headers=live_admin_headers)
