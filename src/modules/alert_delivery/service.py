# SPDX-License-Identifier: AGPL-3.0-or-later
"""AlertDeliveryService: resolve routing, enqueue deliveries on alert create/re-fire,
and (Task 6) drain the queue. Secrets are masked on read; never returned decrypted.
"""
import logging
from typing import Any, Dict, List, Optional

from src.modules.alert_delivery.channels import registry
from src.modules.alert_delivery.channels.base import DeliveryResult
from src.modules.alert_delivery.payload import build_alert_payload

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {'low': 0, 'medium': 1, 'high': 2, 'critical': 3}
ROUTING_CATEGORIES = ('security', 'compliance', 'operational', 'data_quality', 'custom')


class AlertDeliveryService:
    def __init__(self, repository, settings_manager=None):
        self.repo = repository
        self.settings = settings_manager

    # ---- settings helpers ----------------------------------------------
    def _enabled(self) -> bool:
        if self.settings is None:
            return True
        return bool(self.settings.get('alerting', 'ALERT_DELIVERY_ENABLED', True))

    def _max_attempts(self) -> int:
        if self.settings is None:
            return 5
        return int(self.settings.get('alerting', 'ALERT_DELIVERY_MAX_ATTEMPTS', 5))

    def _timeout(self) -> int:
        if self.settings is None:
            return 10
        return int(self.settings.get('alerting', 'ALERT_DELIVERY_TIMEOUT_SECONDS', 10))

    @staticmethod
    def _sensitive(channel_type: str):
        try:
            return registry.get(channel_type).sensitive_fields
        except registry.UnknownChannelType:
            return ()

    @staticmethod
    def _category_for(rule: Dict[str, Any]) -> Optional[str]:
        rule = rule or {}
        return 'custom' if rule.get('source') == 'user' else rule.get('type')

    # ---- enqueue --------------------------------------------------------
    def enqueue_for_alert(self, alert: Dict[str, Any], rule: Dict[str, Any],
                          trigger: str = 'created') -> int:
        """Enqueue a pending delivery for each routed channel whose min_severity the
        alert meets. Called only on the created/re-fired transition (one delivery per
        firing episode). Never raises for a routing/enqueue miss — returns 0."""
        if not self._enabled():
            return 0
        category = self._category_for(rule)
        routes = self.repo.get_routes_for_category(category) if category else []
        sev = SEVERITY_ORDER.get(alert.get('severity') or '', 0)
        eligible = [
            r for r in routes
            if r.get('enabled') and sev >= SEVERITY_ORDER.get(r.get('min_severity') or '', 0)
        ]
        if not eligible:
            return 0
        payload = build_alert_payload(alert, rule)
        rows = [{
            'alert_id': alert['id'], 'channel_id': r['channel_id'],
            'channel_type': r['channel_type'], 'payload': payload, 'trigger': trigger,
        } for r in eligible]
        self.repo.enqueue_deliveries(rows)
        return len(rows)

    # ---- channel CRUD (secrets masked on read) --------------------------
    @staticmethod
    def _mask(config: Dict[str, Any], sensitive) -> Dict[str, Any]:
        from src.modules.integrations.shared.encryption import is_encrypted
        out = dict(config or {})
        for field, val in list(out.items()):
            if field in sensitive and val not in (None, ''):
                out[field] = {'configured': True}
            elif isinstance(val, str) and is_encrypted(val):
                # Defense-in-depth: never let ciphertext escape a read, even for
                # a channel whose type isn't currently registered (so
                # sensitive_fields is unknown). Mirrors the repository's
                # fail-safe decrypt fallback.
                out[field] = {'configured': True}
        return out

    def list_channels(self) -> List[Dict[str, Any]]:
        out = []
        for c in self.repo.list_channels():
            c = dict(c)
            c['config'] = self._mask(c.get('config') or {}, self._sensitive(c['type']))
            out.append(c)
        return out

    def get_channel(self, channel_id: str) -> Optional[Dict[str, Any]]:
        c = self.repo.get_channel(channel_id, decrypt_secrets=False)
        if not c:
            return None
        c = dict(c)
        c['config'] = self._mask(c.get('config') or {}, self._sensitive(c['type']))
        return c

    def create_channel(self, channel_type: str, name: str, config: Dict[str, Any],
                        created_by: Optional[str] = None) -> Optional[Dict[str, Any]]:
        channel = registry.get(channel_type)     # raises UnknownChannelType
        channel.validate_config(config or {})     # raises ValueError on bad config
        cid = self.repo.create_channel(channel_type, name, config or {},
                                       channel.sensitive_fields, created_by)
        return self.get_channel(cid)

    def update_channel(self, channel_id: str, name: str, enabled: bool,
                       config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        existing = self.repo.get_channel(channel_id, decrypt_secrets=True)
        if not existing:
            return None
        channel = registry.get(existing['type'])
        merged = dict(config or {})
        # Write-only round-trip: a blank / masked / still-encrypted secret means "keep".
        for field in channel.sensitive_fields:
            incoming = merged.get(field)
            if incoming in (None, '', {'configured': True}) or (
                    isinstance(incoming, str) and incoming.startswith('ENC:')):
                merged[field] = existing['config'].get(field)
        # Any field _mask replaced with the {'configured': True} sentinel — including
        # a value masked by the defense-in-depth ciphertext branch for a field that
        # isn't in sensitive_fields — must be restored to its stored value, never
        # persisted as the literal sentinel dict.
        for field, incoming in list(merged.items()):
            if incoming == {'configured': True}:
                merged[field] = existing['config'].get(field)
        channel.validate_config(merged)
        self.repo.update_channel(channel_id, name, enabled, merged, channel.sensitive_fields)
        return self.get_channel(channel_id)

    def delete_channel(self, channel_id: str) -> bool:
        return self.repo.delete_channel(channel_id)

    def get_all_routes(self) -> Dict[str, List[Dict[str, Any]]]:
        return self.repo.get_all_routes()

    def set_routes_for_category(self, category: str, routes: List[Dict[str, Any]]) -> None:
        self.repo.set_routes_for_category(category, routes)

    # ---- send test (synchronous, bypasses the queue) --------------------
    def send_test(self, channel_id: str) -> DeliveryResult:
        channel_row = self.repo.get_channel(channel_id, decrypt_secrets=True)
        if not channel_row:
            return DeliveryResult(ok=False, error='channel not found', retriable=False)
        try:
            impl = registry.get(channel_row['type'])
        except registry.UnknownChannelType:
            # A stored channel whose type has no registered impl (version skew /
            # direct DB insert). Return a clear result rather than letting the
            # KeyError surface as handle_errors' misleading "Missing required
            # field: <type>" 400.
            return DeliveryResult(
                ok=False, error=f"unknown channel type '{channel_row['type']}'",
                retriable=False)
        payload = {'alert_id': 'test', 'rule_name': 'Test alert',
                   'severity': 'low', 'title': f"Test delivery to {channel_row['name']}",
                   'description': 'This is a test alert from AssetAtlas.'}
        result = impl.send(channel_row.get('config') or {}, payload)
        self.repo.record_test_delivery(channel_id, channel_row['type'], payload, result)
        return result

    # ---- drain (worker side) -------------------------------------------
    @staticmethod
    def _backoff_seconds(attempts: int) -> int:
        return min(2 ** max(attempts - 1, 0) * 5, 300)

    def drain(self, limit: int = 50) -> Dict[str, int]:
        """Claim due pending deliveries and send them. Per-row isolation: one bad
        row can never stop the drain. Runs in the scheduler worker."""
        from datetime import datetime, timedelta, timezone
        # Master switch is enforced at the send boundary: the drain is the single
        # chokepoint through which all automated sends flow, and (unlike the s20
        # DB-direct enqueue path) the worker's drain service always has a
        # settings_manager. When delivery is disabled, deliveries stay pending and
        # resume when it's re-enabled (a pause, not a drop).
        if not self._enabled():
            return {'claimed': 0, 'sent': 0, 'retried': 0, 'failed': 0}
        claimed = self.repo.claim_pending_deliveries(limit)
        stats = {'claimed': len(claimed), 'sent': 0, 'retried': 0, 'failed': 0}
        max_attempts = self._max_attempts()
        channel_cache: Dict[str, Any] = {}   # one fetch+decrypt per channel per batch
        for row in claimed:
            # Full per-row isolation: the mark_* persistence calls are inside this
            # try too, so a transient DB error on ONE row's UPDATE can't abort the
            # whole tick (which would leave already-sent rows unmarked -> re-sent).
            try:
                delivery_id = row['id']
                attempts = row.get('attempts', 0) + 1
                result = None
                try:
                    impl = registry.get(row['channel_type'])
                    cid = row['channel_id']
                    if cid in channel_cache:
                        channel = channel_cache[cid]
                    else:
                        channel = self.repo.get_channel(cid, decrypt_secrets=True)
                        channel_cache[cid] = channel
                    if channel is None or not channel.get('enabled'):
                        result = DeliveryResult(ok=False, retriable=False,
                                                error='channel missing or disabled')
                    else:
                        result = impl.send(channel.get('config') or {},
                                           row.get('payload') or {})
                except Exception as e:             # unknown type / impl blew up
                    result = DeliveryResult(ok=False, error=str(e), retriable=False)
                if result.ok:
                    self.repo.mark_sent(delivery_id, response_code=result.response_code)
                    stats['sent'] += 1
                elif result.retriable and attempts < max_attempts:
                    next_at = datetime.now(timezone.utc) + timedelta(
                        seconds=self._backoff_seconds(attempts))
                    self.repo.mark_retry(delivery_id, attempts=attempts,
                                         next_retry_at=next_at, error=result.error)
                    stats['retried'] += 1
                else:
                    self.repo.mark_failed(delivery_id, attempts=attempts, error=result.error)
                    stats['failed'] += 1
            except Exception as e:
                logger.error("alert delivery drain: could not persist delivery %s: %s",
                             row.get('id'), e, exc_info=True)
        if stats['claimed']:
            logger.info("alert delivery drain: %s", stats)
        return stats
