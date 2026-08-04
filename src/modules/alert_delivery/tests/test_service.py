# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.modules.alert_delivery.service import AlertDeliveryService


def _svc(routes=None, enabled=True):
    repo = MagicMock()
    repo.get_routes_for_category.return_value = routes or []
    settings = MagicMock()
    settings.get.side_effect = lambda cat, key, default=None: (
        enabled if key == 'ALERT_DELIVERY_ENABLED' else default)
    return AlertDeliveryService(repo, settings_manager=settings), repo


ALERT = {'id': 'a1', 'severity': 'high', 'title': 't', 'asset_ids': ['x']}
RULE = {'id': 'r1', 'slug': 's', 'name': 'n', 'type': 'security', 'source': 'system'}


def test_enqueue_routes_channel_at_or_above_min_severity():
    svc, repo = _svc(routes=[
        {'channel_id': 'c1', 'channel_type': 'log', 'enabled': True, 'min_severity': 'medium'},
    ])
    n = svc.enqueue_for_alert(ALERT, RULE)
    assert n == 1
    enqueued = repo.enqueue_deliveries.call_args.args[0]
    assert enqueued[0]['channel_id'] == 'c1'
    assert enqueued[0]['payload']['alert_id'] == 'a1'
    assert enqueued[0]['trigger'] == 'created'


def test_enqueue_drops_channel_below_min_severity():
    svc, repo = _svc(routes=[
        {'channel_id': 'c1', 'channel_type': 'log', 'enabled': True, 'min_severity': 'critical'},
    ])
    n = svc.enqueue_for_alert(ALERT, RULE)  # alert is 'high' < 'critical'
    assert n == 0
    repo.enqueue_deliveries.assert_not_called()


def test_enqueue_drops_disabled_channel():
    svc, repo = _svc(routes=[
        {'channel_id': 'c1', 'channel_type': 'log', 'enabled': False, 'min_severity': 'low'},
    ])
    assert svc.enqueue_for_alert(ALERT, RULE) == 0
    repo.enqueue_deliveries.assert_not_called()


def test_enqueue_noop_when_globally_disabled():
    svc, repo = _svc(routes=[
        {'channel_id': 'c1', 'channel_type': 'log', 'enabled': True, 'min_severity': 'low'},
    ], enabled=False)
    assert svc.enqueue_for_alert(ALERT, RULE) == 0
    repo.get_routes_for_category.assert_not_called()


def test_enqueue_routes_system_rule_by_type():
    from unittest.mock import MagicMock
    from src.modules.alert_delivery.service import AlertDeliveryService
    repo = MagicMock()
    repo.get_routes_for_category.return_value = []
    svc = AlertDeliveryService(repo)
    svc.enqueue_for_alert({'id': 'a1', 'severity': 'high'},
                          {'id': 'r1', 'type': 'security', 'source': 'system'})
    repo.get_routes_for_category.assert_called_once_with('security')


def test_enqueue_routes_user_rule_as_custom():
    from unittest.mock import MagicMock
    from src.modules.alert_delivery.service import AlertDeliveryService
    repo = MagicMock()
    repo.get_routes_for_category.return_value = []
    svc = AlertDeliveryService(repo)
    svc.enqueue_for_alert({'id': 'a1', 'severity': 'high'},
                          {'id': 'r1', 'type': 'security', 'source': 'user'})
    repo.get_routes_for_category.assert_called_once_with('custom')


def test_mask_hides_sensitive_fields_never_leaks_ciphertext():
    # The masking logic is tested directly (the log channel has no sensitive
    # fields, so list_channels can't exercise masking end-to-end without a
    # production-only test seam — which we deliberately avoid).
    from src.modules.integrations.shared.encryption import encrypt
    masked = AlertDeliveryService._mask({'url': 'u', 'token': encrypt('shh')}, ('token',))
    assert masked['url'] == 'u'
    assert masked['token'] == {'configured': True}  # never ciphertext/plaintext


def test_mask_masks_ciphertext_even_when_field_not_in_sensitive():
    from src.modules.integrations.shared.encryption import encrypt
    # 'token' is NOT declared sensitive here (simulating an unregistered type),
    # but its stored value is ciphertext -> must still be masked, never leaked.
    masked = AlertDeliveryService._mask({'url': 'u', 'token': encrypt('shh')}, ())
    assert masked['url'] == 'u'
    assert masked['token'] == {'configured': True}


def test_send_test_sends_and_records_test_row():
    repo = MagicMock()
    repo.get_channel.return_value = {'id': 'c1', 'type': 'log', 'name': 'ops',
                                     'enabled': True, 'config': {}}
    svc = AlertDeliveryService(repo, settings_manager=MagicMock())
    res = svc.send_test('c1')
    assert res.ok is True
    # A test audit row is written with trigger='test'.
    assert repo.record_test_delivery.called


def test_update_channel_keeps_secret_when_blank_overwrites_when_set():
    # The built-in log channel has no sensitive fields, so register a throwaway
    # secret-bearing type to exercise the write-only round-trip, then remove it.
    from src.modules.alert_delivery.channels import registry
    from src.modules.alert_delivery.channels.base import AlertChannel, DeliveryResult

    class _SecretChannel(AlertChannel):
        type = 'test-secret'
        sensitive_fields = ('token',)
        def send(self, config, payload):
            return DeliveryResult(ok=True)

    registry.register(_SecretChannel)
    try:
        repo = MagicMock()
        # existing channel with the secret DECRYPTED (get_channel(decrypt_secrets=True))
        repo.get_channel.return_value = {'id': 'c1', 'type': 'test-secret', 'name': 'ops',
                                         'enabled': True, 'config': {'url': 'u', 'token': 'oldsecret'}}
        svc = AlertDeliveryService(repo, settings_manager=MagicMock())

        # blank token -> keep the existing secret; other fields update
        svc.update_channel('c1', 'ops', True, {'url': 'u2', 'token': ''})
        passed = repo.update_channel.call_args.args[3]   # (id, name, enabled, config, sensitive_fields)
        assert passed['token'] == 'oldsecret' and passed['url'] == 'u2'

        # new plaintext token -> overwrite
        svc.update_channel('c1', 'ops', True, {'url': 'u2', 'token': 'newsecret'})
        assert repo.update_channel.call_args.args[3]['token'] == 'newsecret'
    finally:
        registry._REGISTRY.pop('test-secret', None)   # don't pollute the global registry


def _drain_svc(claimed, channel_type='log', channel_config=None):
    repo = MagicMock()
    repo.claim_pending_deliveries.return_value = claimed
    repo.get_channel.return_value = {'id': 'c1', 'type': channel_type, 'name': 'ops',
                                     'enabled': True, 'config': channel_config or {}}
    settings = MagicMock()
    settings.get.side_effect = lambda cat, key, default=None: (
        3 if key == 'ALERT_DELIVERY_MAX_ATTEMPTS' else default)
    return AlertDeliveryService(repo, settings_manager=settings), repo


def test_drain_marks_sent_on_success():
    svc, repo = _drain_svc([{'id': 'd1', 'channel_id': 'c1', 'channel_type': 'log',
                             'attempts': 0, 'payload': {'alert_id': 'a1'}}])
    stats = svc.drain()
    assert stats['sent'] == 1
    repo.mark_sent.assert_called_once_with('d1', response_code=None)


def test_drain_retries_transient_failure_with_backoff():
    svc, repo = _drain_svc(
        [{'id': 'd1', 'channel_id': 'c1', 'channel_type': 'log', 'attempts': 0,
          'payload': {}}], channel_config={'simulate': 'fail'})
    stats = svc.drain()
    assert stats['retried'] == 1
    assert repo.mark_retry.call_args.args[0] == 'd1'
    # attempts incremented to 1, next_retry_at in the future.
    assert repo.mark_retry.call_args.kwargs['attempts'] == 1
    assert repo.mark_retry.call_args.kwargs['next_retry_at'] > datetime.now(timezone.utc)


def test_drain_fails_after_max_attempts():
    # attempts already 2, max is 3 -> this attempt (3) exhausts it.
    svc, repo = _drain_svc(
        [{'id': 'd1', 'channel_id': 'c1', 'channel_type': 'log', 'attempts': 2,
          'payload': {}}], channel_config={'simulate': 'fail'})
    stats = svc.drain()
    assert stats['failed'] == 1
    assert repo.mark_failed.call_args.kwargs['attempts'] == 3


def test_drain_fails_permanent_failure_immediately():
    svc, repo = _drain_svc(
        [{'id': 'd1', 'channel_id': 'c1', 'channel_type': 'log', 'attempts': 0,
          'payload': {}}], channel_config={'simulate': 'fail_permanent'})
    stats = svc.drain()
    assert stats['failed'] == 1
    repo.mark_retry.assert_not_called()


def test_drain_isolates_one_bad_row():
    svc, repo = _drain_svc([
        {'id': 'd1', 'channel_id': 'c1', 'channel_type': 'log', 'attempts': 0, 'payload': {}},
        {'id': 'd2', 'channel_id': 'c1', 'channel_type': 'nope', 'attempts': 0, 'payload': {}},
    ])
    # d2 has an unknown channel type -> its send blows up but d1 still sends.
    stats = svc.drain()
    assert stats['sent'] == 1 and stats['failed'] == 1


def test_drain_row_isolation_survives_mark_persistence_failure():
    # A transient DB error while PERSISTING one row's outcome (mark_sent) must not
    # abort the whole tick -- the remaining claimed rows still get processed. Before
    # the fix the mark_* calls sat outside the per-row try, so this raised out of
    # drain() and left every already-sent-but-unmarked row to be re-sent next tick.
    svc, repo = _drain_svc([
        {'id': 'd1', 'channel_id': 'c1', 'channel_type': 'log', 'attempts': 0, 'payload': {}},
        {'id': 'd2', 'channel_id': 'c1', 'channel_type': 'log', 'attempts': 0, 'payload': {}},
    ])
    repo.mark_sent.side_effect = [RuntimeError('db reset'), None]
    stats = svc.drain()          # must not raise
    assert repo.mark_sent.call_count == 2   # d2 still processed after d1's failure
    assert stats['sent'] == 1               # only d2's mark succeeded


def test_drain_fetches_each_channel_once_per_batch():
    # Rows sharing a channel must not each trigger a get_channel SELECT + Fernet
    # decrypt -- the drain caches per channel within a batch.
    svc, repo = _drain_svc([
        {'id': 'd1', 'channel_id': 'c1', 'channel_type': 'log', 'attempts': 0, 'payload': {}},
        {'id': 'd2', 'channel_id': 'c1', 'channel_type': 'log', 'attempts': 0, 'payload': {}},
        {'id': 'd3', 'channel_id': 'c1', 'channel_type': 'log', 'attempts': 0, 'payload': {}},
    ])
    svc.drain()
    repo.get_channel.assert_called_once()


def test_drain_fails_when_channel_missing_or_disabled():
    svc, repo = _drain_svc([{'id': 'd1', 'channel_id': 'c1', 'channel_type': 'log',
                             'attempts': 0, 'payload': {}}])
    repo.get_channel.return_value = None      # deleted since the row was enqueued
    stats = svc.drain()
    assert stats['failed'] == 1
    repo.mark_failed.assert_called_once_with('d1', attempts=1, error='channel missing or disabled')
    repo.mark_sent.assert_not_called()
    repo.mark_retry.assert_not_called()


def test_drain_noop_when_globally_disabled():
    repo = MagicMock()
    settings = MagicMock()
    settings.get.side_effect = lambda cat, key, default=None: (
        False if key == 'ALERT_DELIVERY_ENABLED' else default)
    svc = AlertDeliveryService(repo, settings_manager=settings)
    stats = svc.drain()
    assert stats == {'claimed': 0, 'sent': 0, 'retried': 0, 'failed': 0}
    repo.claim_pending_deliveries.assert_not_called()
