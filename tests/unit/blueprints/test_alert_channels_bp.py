# SPDX-License-Identifier: AGPL-3.0-or-later
"""Blueprint tests for the alert delivery channel + per-rule routing admin API (#917).

Mirrors test_alert_rules_bp.py / test_batches_bp.py: fixtures `client`, `auth_headers`
callable, `blueprint_app` with per-test-reset MagicMock services from tests/conftest.py,
and the `_prime_regular_user` override for 403 checks (the autouse `_prime_user_cache`
fixture always seeds an admin user in the JWT cache, so asserting 403 for a non-admin
requires overriding that cache entry, not just passing a different role to auth_headers).
Response envelope is {success, data}; a Pydantic validation failure returns 400.
"""

import pytest

from src.modules.alert_delivery.channels.base import DeliveryResult


# =============================================================================
# Admin gate
# =============================================================================

class TestAlertChannelsAuth:
    """All channel + routing endpoints are admin-only."""

    @pytest.mark.parametrize("method,path", [
        ("GET", "/api/alerts/channels"),
        ("POST", "/api/alerts/channels"),
        ("GET", "/api/alerts/channels/c1"),
        ("PUT", "/api/alerts/channels/c1"),
        ("DELETE", "/api/alerts/channels/c1"),
        ("POST", "/api/alerts/channels/c1/test"),
        ("GET", "/api/alerts/routing"),
        ("PUT", "/api/alerts/routing/security"),
        ("GET", "/api/alerts/channels/types"),
    ])
    def test_no_auth_returns_401(self, client, method, path):
        resp = getattr(client, method.lower())(path)
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path", [
        ("GET", "/api/alerts/channels"),
        ("POST", "/api/alerts/channels"),
        ("GET", "/api/alerts/channels/c1"),
        ("PUT", "/api/alerts/channels/c1"),
        ("DELETE", "/api/alerts/channels/c1"),
        ("POST", "/api/alerts/channels/c1/test"),
        ("GET", "/api/alerts/routing"),
        ("PUT", "/api/alerts/routing/security"),
        ("GET", "/api/alerts/channels/types"),
    ])
    def test_non_admin_returns_403(self, client, auth_headers, _prime_regular_user, method, path):
        resp = getattr(client, method.lower())(path, headers=auth_headers("user"))
        assert resp.status_code == 403


def test_list_channels_requires_admin(client, auth_headers, _prime_regular_user):
    resp = client.get('/api/alerts/channels', headers=auth_headers('user'))
    assert resp.status_code == 403


def test_list_channels_returns_masked(client, auth_headers, blueprint_app):
    blueprint_app.alert_delivery_service.list_channels.return_value = [
        {'id': 'c1', 'type': 'log', 'name': 'ops', 'enabled': True, 'config': {}}]
    resp = client.get('/api/alerts/channels', headers=auth_headers('admin'))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['success'] is True
    assert body['data'][0]['id'] == 'c1'


def test_get_channel_ok(client, auth_headers, blueprint_app):
    blueprint_app.alert_delivery_service.get_channel.return_value = {
        'id': 'c1', 'type': 'log', 'name': 'ops', 'enabled': True, 'config': {}}
    resp = client.get('/api/alerts/channels/c1', headers=auth_headers('admin'))
    assert resp.status_code == 200
    assert resp.get_json()['data']['id'] == 'c1'


def test_get_channel_not_found(client, auth_headers, blueprint_app):
    blueprint_app.alert_delivery_service.get_channel.return_value = None
    resp = client.get('/api/alerts/channels/missing', headers=auth_headers('admin'))
    # Resource identified by the URL doesn't exist → 404 (REST-correct, matches
    # the sibling alert_rules blueprint), distinct from a 400 validation failure.
    assert resp.status_code == 404


def test_create_channel_validation_400(client, auth_headers):
    resp = client.post('/api/alerts/channels', json={'name': 'x'},   # missing type
                        headers=auth_headers('admin'))
    assert resp.status_code == 400


def test_create_channel_ok(client, auth_headers, blueprint_app):
    blueprint_app.alert_delivery_service.create_channel.return_value = {
        'id': 'c9', 'type': 'log', 'name': 'ops', 'enabled': True, 'config': {}}
    resp = client.post('/api/alerts/channels',
                        json={'type': 'log', 'name': 'ops', 'config': {}},
                        headers=auth_headers('admin'))
    assert resp.status_code in (200, 201)
    assert resp.get_json()['data']['id'] == 'c9'


def test_create_channel_unknown_type_400(client, auth_headers, blueprint_app):
    from src.modules.alert_delivery.channels import registry
    blueprint_app.alert_delivery_service.create_channel.side_effect = registry.UnknownChannelType('bogus')
    resp = client.post('/api/alerts/channels',
                        json={'type': 'bogus', 'name': 'ops', 'config': {}},
                        headers=auth_headers('admin'))
    assert resp.status_code == 400


def test_create_channel_invalid_config_400(client, auth_headers, blueprint_app):
    blueprint_app.alert_delivery_service.create_channel.side_effect = ValueError('bad config')
    resp = client.post('/api/alerts/channels',
                        json={'type': 'log', 'name': 'ops', 'config': {}},
                        headers=auth_headers('admin'))
    assert resp.status_code == 400


def test_update_channel_ok(client, auth_headers, blueprint_app):
    blueprint_app.alert_delivery_service.update_channel.return_value = {
        'id': 'c1', 'type': 'log', 'name': 'ops2', 'enabled': False, 'config': {}}
    resp = client.put('/api/alerts/channels/c1',
                       json={'name': 'ops2', 'enabled': False, 'config': {}},
                       headers=auth_headers('admin'))
    assert resp.status_code == 200
    assert resp.get_json()['data']['name'] == 'ops2'


def test_update_channel_not_found(client, auth_headers, blueprint_app):
    blueprint_app.alert_delivery_service.update_channel.return_value = None
    resp = client.put('/api/alerts/channels/missing',
                       json={'name': 'ops2', 'enabled': False, 'config': {}},
                       headers=auth_headers('admin'))
    assert resp.status_code == 404


def test_update_channel_invalid_config_400(client, auth_headers, blueprint_app):
    blueprint_app.alert_delivery_service.update_channel.side_effect = ValueError('bad config')
    resp = client.put('/api/alerts/channels/c1',
                       json={'name': 'ops2', 'enabled': False, 'config': {}},
                       headers=auth_headers('admin'))
    assert resp.status_code == 400


def test_delete_channel_ok(client, auth_headers, blueprint_app):
    blueprint_app.alert_delivery_service.delete_channel.return_value = True
    resp = client.delete('/api/alerts/channels/c1', headers=auth_headers('admin'))
    assert resp.status_code == 200
    assert resp.get_json()['data']['deleted'] is True


def test_delete_channel_not_found(client, auth_headers, blueprint_app):
    blueprint_app.alert_delivery_service.delete_channel.return_value = False
    resp = client.delete('/api/alerts/channels/missing', headers=auth_headers('admin'))
    assert resp.status_code == 404


def test_send_test_returns_result(client, auth_headers, blueprint_app):
    blueprint_app.alert_delivery_service.send_test.return_value = DeliveryResult(ok=True)
    resp = client.post('/api/alerts/channels/c1/test', headers=auth_headers('admin'))
    assert resp.status_code == 200
    assert resp.get_json()['data']['ok'] is True


def test_send_test_returns_failure_result(client, auth_headers, blueprint_app):
    blueprint_app.alert_delivery_service.send_test.return_value = DeliveryResult(
        ok=False, error='channel not found', retriable=False)
    resp = client.post('/api/alerts/channels/missing/test', headers=auth_headers('admin'))
    # send_test never raises — the service already models "not found" as a failed
    # DeliveryResult, so this is still a 200 with ok=False, not a 4xx.
    assert resp.status_code == 200
    body = resp.get_json()['data']
    assert body['ok'] is False
    assert body['error'] == 'channel not found'


def test_get_routing_returns_all_categories(client, auth_headers, blueprint_app):
    blueprint_app.alert_delivery_service.get_all_routes.return_value = {
        'security': [{'channel_id': 'c1', 'name': 'ops', 'channel_type': 'webhook',
                      'enabled': True, 'min_severity': 'high'}]}
    resp = client.get('/api/alerts/routing', headers=auth_headers('admin'))
    assert resp.status_code == 200
    assert resp.get_json()['data']['security'][0]['channel_id'] == 'c1'


def test_put_routing_sets_a_category(client, auth_headers, blueprint_app):
    resp = client.put('/api/alerts/routing/security',
                      headers=auth_headers('admin'),
                      json={'channels': [{'channel_id': 'c1', 'min_severity': 'high'}]})
    assert resp.status_code == 200
    blueprint_app.alert_delivery_service.set_routes_for_category.assert_called_once()
    args = blueprint_app.alert_delivery_service.set_routes_for_category.call_args.args
    assert args[0] == 'security'


def test_put_routing_rejects_unknown_category(client, auth_headers, blueprint_app):
    resp = client.put('/api/alerts/routing/nonsense',
                      headers=auth_headers('admin'), json={'channels': []})
    assert resp.status_code == 400


def test_put_routing_invalid_min_severity_400(client, auth_headers, blueprint_app):
    """A typo in min_severity must be rejected by the RouteItem Literal, not silently
    coerced to the lowest threshold — otherwise a 'critical-only' route would fire on
    every severity. Covers the body-validation path the per-rule endpoint's deleted
    test used to guard."""
    resp = client.put('/api/alerts/routing/security',
                      headers=auth_headers('admin'),
                      json={'channels': [{'channel_id': 'c1', 'min_severity': 'crticial'}]})
    assert resp.status_code == 400
    blueprint_app.alert_delivery_service.set_routes_for_category.assert_not_called()


def test_list_channel_types_includes_webhook_excludes_log(client, auth_headers):
    resp = client.get('/api/alerts/channels/types', headers=auth_headers('admin'))
    assert resp.status_code == 200
    types = {t['type'] for t in resp.get_json()['data']}
    assert 'webhook' in types
    assert 'email' in types  # #919 email channel is user-creatable
    assert 'syslog' in types  # #920 syslog channel is user-creatable
    assert 'log' not in types  # internal dummy/test channel, not user-creatable
