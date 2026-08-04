# SPDX-License-Identifier: AGPL-3.0-or-later
"""Acceptance test for #917: a routed alert delivers via the dummy channel and
records an audit row; a failing channel retries with backoff and ends 'failed',
auditable. Exercises AlertDeliveryService.enqueue_for_alert + drain against an
in-memory queue that mimics the repository's claim/mark semantics."""
from datetime import datetime, timezone

from src.modules.alert_delivery.service import AlertDeliveryService


class FakeQueueRepo:
    def __init__(self, routes, channel_config=None):
        self._routes = routes
        self._channel_config = channel_config or {}
        self.rows = []
        self._seq = 0

    # routing
    def get_routes_for_category(self, category):
        return self._routes

    # enqueue
    def enqueue_deliveries(self, rows):
        for r in rows:
            self._seq += 1
            self.rows.append({
                'id': f'd{self._seq}', 'alert_id': r['alert_id'],
                'channel_id': r['channel_id'], 'channel_type': r['channel_type'],
                'status': 'pending', 'attempts': 0,
                'next_retry_at': datetime.now(timezone.utc), 'payload': r['payload'],
                'last_error': None, 'response_code': None,
            })
        return len(rows)

    # drain
    def claim_pending_deliveries(self, limit):
        now = datetime.now(timezone.utc)
        due = [r for r in self.rows
               if r['status'] == 'pending' and r['next_retry_at'] <= now][:limit]
        return [dict(r) for r in due]

    def get_channel(self, channel_id, decrypt_secrets=False):
        return {'id': channel_id, 'type': 'log', 'name': 'ops',
                'enabled': True, 'config': self._channel_config}

    def _find(self, delivery_id):
        return next(r for r in self.rows if r['id'] == delivery_id)

    def mark_sent(self, delivery_id, response_code=None):
        r = self._find(delivery_id)
        r.update(status='sent', attempts=r['attempts'] + 1, response_code=response_code,
                 sent_at=datetime.now(timezone.utc))

    def mark_retry(self, delivery_id, attempts, next_retry_at, error):
        r = self._find(delivery_id)
        r.update(status='pending', attempts=attempts, next_retry_at=next_retry_at,
                 last_error=error)

    def mark_failed(self, delivery_id, attempts, error):
        r = self._find(delivery_id)
        r.update(status='failed', attempts=attempts, last_error=error)


class Settings:
    def __init__(self, **vals):
        self._v = {'ALERT_DELIVERY_ENABLED': True, 'ALERT_DELIVERY_MAX_ATTEMPTS': 3, **vals}

    def get(self, category, key, default=None):
        return self._v.get(key, default)


ALERT = {'id': 'alert-1', 'type': 'compliance', 'severity': 'high',
         'title': 'Disk low', 'description': 'd', 'threshold': {},
         'asset_count': 1, 'assets_by_category': {}, 'data_sources': [],
         'asset_ids': ['x'], 'reason': None, 'pipeline_run_id': 'run-1'}
RULE = {'id': 'r1', 'slug': 'disk-low', 'name': 'Disk low', 'severity': 'high',
        'type': 'compliance', 'source': 'system'}
ROUTES = [{'channel_id': 'c1', 'channel_type': 'log', 'enabled': True, 'min_severity': 'low'}]


def test_routed_alert_delivers_and_is_audited():
    repo = FakeQueueRepo(ROUTES)
    svc = AlertDeliveryService(repo, settings_manager=Settings())
    assert svc.enqueue_for_alert(ALERT, RULE) == 1        # created -> one pending row
    stats = svc.drain()
    assert stats['sent'] == 1
    assert repo.rows[0]['status'] == 'sent'               # auditable outcome


def test_failing_channel_retries_then_fails_auditably():
    repo = FakeQueueRepo(ROUTES, channel_config={'simulate': 'fail'})
    svc = AlertDeliveryService(repo, settings_manager=Settings(ALERT_DELIVERY_MAX_ATTEMPTS=3))
    svc.enqueue_for_alert(ALERT, RULE)

    # Attempt 1: transient fail -> retried, attempts=1, scheduled in the future.
    s1 = svc.drain()
    assert s1['retried'] == 1 and repo.rows[0]['attempts'] == 1
    assert repo.rows[0]['status'] == 'pending' and repo.rows[0]['last_error']
    assert repo.rows[0]['next_retry_at'] > datetime.now(timezone.utc)   # backoff scheduled ahead

    # Force it due again; attempt 2 -> retried, attempts=2.
    repo.rows[0]['next_retry_at'] = datetime.now(timezone.utc)
    s2 = svc.drain()
    assert s2['retried'] == 1 and repo.rows[0]['attempts'] == 2
    assert repo.rows[0]['next_retry_at'] > datetime.now(timezone.utc)   # backoff scheduled ahead

    # Force due; attempt 3 hits max_attempts -> failed, error recorded (auditable).
    repo.rows[0]['next_retry_at'] = datetime.now(timezone.utc)
    s3 = svc.drain()
    assert s3['failed'] == 1
    assert repo.rows[0]['status'] == 'failed' and repo.rows[0]['attempts'] == 3
    assert repo.rows[0]['last_error']
