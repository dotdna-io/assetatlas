# SPDX-License-Identifier: AGPL-3.0-or-later
from unittest.mock import MagicMock

from src.modules.alert_delivery.repository import AlertDeliveryRepository


def _repo(rows=None):
    db = MagicMock()
    db.execute_query.return_value = rows if rows is not None else []
    return AlertDeliveryRepository(db), db


def test_create_channel_encrypts_only_sensitive_fields():
    repo, db = _repo([{'id': 'chan-1'}])
    cid = repo.create_channel(
        'webhook', 'ops', {'url': 'https://x', 'token': 'secret123'},
        sensitive_fields=('token',), created_by='admin@x')
    assert cid == 'chan-1'
    import json
    params = db.execute_query.call_args.args[1]
    # config is serialized with json.dumps + inserted as ::jsonb (repo house style).
    stored = json.loads(params['config'])
    # url stays plaintext, token is encrypted (ENC: sentinel), original never leaks.
    assert stored['url'] == 'https://x'
    assert stored['token'].startswith('ENC:') and 'secret123' not in stored['token']


def test_get_channel_decrypts_secrets_only_when_asked():
    # type is deliberately unregistered (not 'log'/'webhook'/...) so this test exercises
    # the registry-miss fallback in get_channel (decrypt every key), independent of any
    # one channel's real sensitive_fields.
    from src.modules.integrations.shared.encryption import encrypt
    enc = encrypt('secret123')
    repo, db = _repo([{'id': 'c1', 'type': 'unregistered', 'name': 'ops',
                       'enabled': True, 'config': {'url': 'u', 'token': enc}}])
    plain = repo.get_channel('c1', decrypt_secrets=True)
    assert plain['config']['token'] == 'secret123'

    db.execute_query.return_value = [{'id': 'c1', 'type': 'unregistered', 'name': 'ops',
                                      'enabled': True, 'config': {'url': 'u', 'token': enc}}]
    masked = repo.get_channel('c1', decrypt_secrets=False)
    assert masked['config']['token'] == enc  # still ciphertext; caller masks


def test_get_routes_for_category_joins_channel():
    repo, db = _repo([{'channel_id': 'c1', 'channel_type': 'webhook',
                       'enabled': True, 'min_severity': 'high', 'name': 'ops'}])
    routes = repo.get_routes_for_category('security')
    sql, params = db.execute_query.call_args.args
    assert 'WHERE acc.category = %(category)s' in sql
    assert params['category'] == 'security'
    assert routes[0]['min_severity'] == 'high'


def test_get_all_routes_groups_by_category():
    repo, db = _repo([
        {'category': 'security', 'channel_id': 'c1', 'channel_type': 'webhook',
         'enabled': True, 'min_severity': 'high', 'name': 'ops'},
        {'category': 'custom', 'channel_id': 'c2', 'channel_type': 'webhook',
         'enabled': True, 'min_severity': 'low', 'name': 'devs'},
    ])
    routes = repo.get_all_routes()
    assert set(routes) == {'security', 'custom'}
    assert routes['security'][0]['channel_id'] == 'c1'


def test_set_routes_for_category_replaces_then_inserts():
    repo, db = _repo([])
    repo.set_routes_for_category('security', [
        {'channel_id': 'c1', 'min_severity': 'high'},
        {'channel_id': 'c2', 'min_severity': 'low'}])
    calls = db.execute_query.call_args_list
    assert 'DELETE FROM alert_category_channels' in calls[0].args[0]
    assert calls[0].args[1]['category'] == 'security'
    inserted = [c for c in calls if 'INSERT INTO alert_category_channels' in c.args[0]]
    assert len(inserted) == 2 and inserted[0].args[1]['channel_id'] == 'c1'


def test_set_routes_for_category_is_atomic_on_failure():
    import pytest
    repo, db = _repo([])
    n = {'i': 0}

    def exec_side(*args, **kwargs):
        if 'INSERT INTO alert_category_channels' in args[0]:
            n['i'] += 1
            if n['i'] == 2:
                raise RuntimeError('fk violation')
        return []

    db.execute_query.side_effect = exec_side
    with pytest.raises(RuntimeError):
        repo.set_routes_for_category('security', [
            {'channel_id': 'c1', 'min_severity': 'low'},
            {'channel_id': 'c2', 'min_severity': 'high'}])
    db.conn_manager.begin_scope.assert_called_once()
    assert db.conn_manager.end_scope.call_args.kwargs.get('error') is not None


from datetime import datetime, timezone


def test_enqueue_deliveries_single_atomic_batch_insert():
    repo, db = _repo([])
    n = repo.enqueue_deliveries([
        {'alert_id': 'a1', 'channel_id': 'c1', 'channel_type': 'log',
         'payload': {'alert_id': 'a1'}, 'trigger': 'created'},
        {'alert_id': 'a1', 'channel_id': 'c2', 'channel_type': 'log',
         'payload': {'alert_id': 'a1'}, 'trigger': 'created'},
    ])
    assert n == 2
    insert_calls = [c for c in db.execute_query.call_args_list
                    if 'INSERT INTO alert_deliveries' in c.args[0]]
    # One multi-row INSERT, not one-per-row: an alert's per-channel deliveries
    # must be enqueued atomically so a single bad row can't leave a partial,
    # half-delivered fan-out.
    assert len(insert_calls) == 1
    sql, params = insert_calls[0].args
    assert "'pending'" in sql
    assert sql.count('::jsonb') == 2          # one VALUES tuple per row
    # Positional params (batch idiom): both channel_ids present, alert_id per row.
    assert 'c1' in params and 'c2' in params
    assert params.count('a1') == 2


def test_claim_pending_selects_due_pending_with_skip_locked():
    repo, db = _repo([{'id': 'd1', 'alert_id': 'a1', 'channel_id': 'c1',
                       'channel_type': 'log', 'attempts': 0, 'payload': {}}])
    out = repo.claim_pending_deliveries(50)
    sql, params = db.execute_query.call_args.args
    assert "status = 'pending'" in sql
    assert 'next_retry_at <= now()' in sql
    assert 'FOR UPDATE SKIP LOCKED' in sql
    assert 'LIMIT %(limit)s' in sql and params['limit'] == 50
    assert out[0]['id'] == 'd1'


def test_mark_sent_sets_status_and_sent_at():
    repo, db = _repo([])
    repo.mark_sent('d1', response_code=200)
    sql, params = db.execute_query.call_args.args
    assert "status = 'sent'" in sql and 'sent_at = now()' in sql
    assert params['id'] == 'd1' and params['response_code'] == 200


def test_mark_retry_sets_backoff_and_keeps_pending():
    repo, db = _repo([])
    when = datetime(2026, 7, 7, tzinfo=timezone.utc)
    repo.mark_retry('d1', attempts=2, next_retry_at=when, error='boom')
    sql, params = db.execute_query.call_args.args
    assert "status = 'pending'" in sql
    assert params['attempts'] == 2 and params['next_retry_at'] == when
    assert params['error'] == 'boom'


def test_mark_failed_sets_status_failed():
    repo, db = _repo([])
    repo.mark_failed('d1', attempts=5, error='gave up')
    sql, params = db.execute_query.call_args.args
    assert "status = 'failed'" in sql
    assert params['attempts'] == 5 and params['error'] == 'gave up'


def test_encrypt_config_serializes_and_encrypts_dict_secret():
    from src.modules.integrations.shared.encryption import is_encrypted, decrypt
    import json
    stored = AlertDeliveryRepository._encrypt_config(
        {'url': 'https://x', 'headers': {'Authorization': 'Bearer t'}}, ('headers',))
    assert stored['url'] == 'https://x'
    assert isinstance(stored['headers'], str) and is_encrypted(stored['headers'])
    assert 'Bearer t' not in stored['headers']
    assert json.loads(decrypt(stored['headers'])) == {'Authorization': 'Bearer t'}


def test_encrypt_config_skips_empty_and_none_secrets():
    stored = AlertDeliveryRepository._encrypt_config(
        {'signing_key': '', 'headers': None, 'url': 'https://x'}, ('signing_key', 'headers'))
    assert stored['signing_key'] == '' and stored['headers'] is None
