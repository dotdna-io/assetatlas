# SPDX-License-Identifier: AGPL-3.0-or-later
"""Blueprint tests for the anomaly-threshold endpoint (#878).
Mirrors test_alerts_bp.py (fixtures `client`, `auth_headers` callable, `blueprint_app`
with per-test-reset MagicMock services from tests/conftest.py) and the api_keys/batches
admin-403 pattern (`_prime_regular_user`). Validation failures return 400 (APIValidationError)."""


def test_patch_anomaly_threshold_merges_and_returns_rule(client, auth_headers, blueprint_app):
    blueprint_app.alerts_service.update_anomaly_threshold.return_value = {
        'id': 'r1', 'threshold': {'pct_medium': 0.25}}
    resp = client.patch('/api/alert-rules/r1/anomaly-threshold',
                        json={'pct_medium': 0.25}, headers=auth_headers("admin"))
    assert resp.status_code == 200
    assert resp.get_json()['data']['threshold'] == {'pct_medium': 0.25}
    blueprint_app.alerts_service.update_anomaly_threshold.assert_called_once_with('r1', {'pct_medium': 0.25})


def test_patch_anomaly_threshold_404_when_not_anomaly(client, auth_headers, blueprint_app):
    blueprint_app.alerts_service.update_anomaly_threshold.return_value = None
    resp = client.patch('/api/alert-rules/r1/anomaly-threshold',
                        json={'pct_medium': 0.25}, headers=auth_headers("admin"))
    assert resp.status_code == 404


def test_patch_anomaly_threshold_400_on_out_of_range_value(client, auth_headers, blueprint_app):
    # pct_medium is a percentage (0..100); a value above range is rejected by the schema.
    resp = client.patch('/api/alert-rules/r1/anomaly-threshold',
                        json={'pct_medium': 150}, headers=auth_headers("admin"))
    assert resp.status_code == 400


def test_patch_anomaly_threshold_400_on_removed_mode_field(client, auth_headers, blueprint_app):
    # `mode` was removed (#902); extra='forbid' now rejects it instead of merging a dead key.
    resp = client.patch('/api/alert-rules/r1/anomaly-threshold',
                        json={'mode': 'active'}, headers=auth_headers("admin"))
    assert resp.status_code == 400


def test_patch_anomaly_threshold_forbidden_for_regular_user(client, auth_headers, _prime_regular_user):
    resp = client.patch('/api/alert-rules/r1/anomaly-threshold',
                        json={'pct_medium': 0.25}, headers=auth_headers("user"))
    assert resp.status_code == 403


def test_patch_anomaly_threshold_ignores_explicit_null_params(client, auth_headers, blueprint_app):
    """Explicit JSON nulls must be dropped, not merged — a null value must never
    jsonb-merge over and clobber the stored param."""
    blueprint_app.alerts_service.update_anomaly_threshold.return_value = {'id': 'r1', 'threshold': {'pct_medium': 0.5}}
    resp = client.patch('/api/alert-rules/r1/anomaly-threshold',
                        json={'pct_high': None, 'pct_medium': 0.5}, headers=auth_headers("admin"))
    assert resp.status_code == 200
    blueprint_app.alerts_service.update_anomaly_threshold.assert_called_once_with('r1', {'pct_medium': 0.5})
