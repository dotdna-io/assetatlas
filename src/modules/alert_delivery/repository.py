# SPDX-License-Identifier: AGPL-3.0-or-later
"""Data access for alert_channels, alert_category_channels, alert_deliveries (#917).

Direct SQL via db_adapter (%(name)s placeholders, UUIDs as str()+::uuid). Secrets
in a channel's config jsonb are Fernet-encrypted at rest, mirroring
IntegrationRepository. Reads do NOT decrypt unless explicitly asked.
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from src.database.postgres_adapter import _clean_nan
from src.modules.integrations.shared.encryption import decrypt, encrypt, is_encrypted

logger = logging.getLogger(__name__)


class AlertDeliveryRepository:
    def __init__(self, db_adapter):
        self.db = db_adapter

    # ---- channels -------------------------------------------------------
    @staticmethod
    def _encrypt_config(config: Dict[str, Any], sensitive_fields: Tuple[str, ...]) -> Dict[str, Any]:
        out = dict(config or {})
        for field in sensitive_fields:
            val = out.get(field)
            if val is None or val == '':
                continue
            if not isinstance(val, str):
                # Structured secret (webhook headers dict): JSON-serialize so the whole
                # value is encrypted at rest, not stored as plaintext jsonb. The channel
                # parses it back on send; string secrets are unchanged.
                val = json.dumps(val)
            if not is_encrypted(val):
                out[field] = encrypt(val)
        return out

    @staticmethod
    def _decrypt_config(config: Dict[str, Any], sensitive_fields: Tuple[str, ...]) -> Dict[str, Any]:
        out = dict(config or {})
        for field in sensitive_fields:
            val = out.get(field)
            if isinstance(val, str) and is_encrypted(val):
                try:
                    out[field] = decrypt(val)
                except ValueError:
                    logger.warning("could not decrypt channel field %s", field)
        return out

    def create_channel(self, channel_type: str, name: str, config: Dict[str, Any],
                       sensitive_fields: Tuple[str, ...], created_by: Optional[str] = None) -> str:
        stored = self._encrypt_config(config, sensitive_fields)
        rows = self.db.execute_query(
            """
            INSERT INTO alert_channels (type, name, config, created_by)
            VALUES (%(type)s, %(name)s, %(config)s::jsonb, %(created_by)s)
            RETURNING id
            """,
            {'type': channel_type, 'name': name, 'config': json.dumps(_clean_nan(stored)),
             'created_by': created_by},
        )
        return str(rows[0]['id'])

    def update_channel(self, channel_id: str, name: str, enabled: bool,
                       config: Dict[str, Any], sensitive_fields: Tuple[str, ...]) -> None:
        stored = self._encrypt_config(config, sensitive_fields)
        self.db.execute_query(
            """
            UPDATE alert_channels
            SET name = %(name)s, enabled = %(enabled)s, config = %(config)s::jsonb,
                updated_at = now()
            WHERE id = %(id)s::uuid
            """,
            {'id': str(channel_id), 'name': name, 'enabled': enabled,
             'config': json.dumps(_clean_nan(stored))},
            fetch=False,
        )

    def get_channel(self, channel_id: str, decrypt_secrets: bool = False) -> Optional[Dict[str, Any]]:
        rows = self.db.execute_query(
            "SELECT id, type, name, enabled, config, created_at, updated_at "
            "FROM alert_channels WHERE id = %(id)s::uuid",
            {'id': str(channel_id)},
        )
        if not rows:
            return None
        channel = dict(rows[0])
        channel['config'] = self._coerce_json(channel.get('config'))
        if decrypt_secrets:
            from src.modules.alert_delivery.channels import registry
            try:
                sensitive = registry.get(channel['type']).sensitive_fields
            except registry.UnknownChannelType:
                # Channel type has no registered implementation yet (#918-921 land
                # webhook/email/syslog/HEC after this repo). Don't silently skip
                # decryption in that gap — fall back to every config key as a
                # candidate; _decrypt_config only touches values that are actually
                # ENC:-prefixed, so plaintext fields are still left untouched.
                sensitive = tuple(channel['config'].keys())
            channel['config'] = self._decrypt_config(channel['config'], sensitive)
        return channel

    def list_channels(self) -> List[Dict[str, Any]]:
        rows = self.db.execute_query(
            "SELECT id, type, name, enabled, config, created_at, updated_at "
            "FROM alert_channels ORDER BY name"
        ) or []
        out = []
        for r in rows:
            c = dict(r)
            c['config'] = self._coerce_json(c.get('config'))
            out.append(c)
        return out

    def delete_channel(self, channel_id: str) -> bool:
        rows = self.db.execute_query(
            "DELETE FROM alert_channels WHERE id = %(id)s::uuid RETURNING id",
            {'id': str(channel_id)},
        )
        return bool(rows)

    # ---- routing (per category) ----------------------------------------
    def get_routes_for_category(self, category: str) -> List[Dict[str, Any]]:
        return self.db.execute_query(
            """
            SELECT acc.channel_id, ac.type AS channel_type, ac.enabled,
                   acc.min_severity, ac.name
            FROM alert_category_channels acc
            JOIN alert_channels ac ON ac.id = acc.channel_id
            WHERE acc.category = %(category)s
            ORDER BY ac.name
            """,
            {'category': category},
        ) or []

    def get_all_routes(self) -> Dict[str, List[Dict[str, Any]]]:
        rows = self.db.execute_query(
            """
            SELECT acc.category, acc.channel_id, ac.type AS channel_type, ac.enabled,
                   acc.min_severity, ac.name
            FROM alert_category_channels acc
            JOIN alert_channels ac ON ac.id = acc.channel_id
            ORDER BY acc.category, ac.name
            """
        ) or []
        out: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(r['category'], []).append({
                'channel_id': r['channel_id'], 'channel_type': r['channel_type'],
                'enabled': r['enabled'], 'min_severity': r['min_severity'], 'name': r['name'],
            })
        return out

    def set_routes_for_category(self, category: str, routes: List[Dict[str, Any]]) -> None:
        # Atomic replace (DELETE + re-INSERT as one unit), same connection-scope idiom
        # as the #917 per-rule version.
        conn_manager = getattr(self.db, 'conn_manager', None)
        if conn_manager is not None:
            conn_manager.begin_scope()
        error = None
        try:
            self.db.execute_query(
                "DELETE FROM alert_category_channels WHERE category = %(category)s",
                {'category': category}, fetch=False)
            for route in routes:
                self.db.execute_query(
                    """
                    INSERT INTO alert_category_channels (category, channel_id, min_severity)
                    VALUES (%(category)s, %(channel_id)s::uuid, %(min_severity)s)
                    ON CONFLICT (category, channel_id)
                        DO UPDATE SET min_severity = EXCLUDED.min_severity
                    """,
                    {'category': category, 'channel_id': str(route['channel_id']),
                     'min_severity': route.get('min_severity', 'low')}, fetch=False)
        except Exception as e:
            error = e
            raise
        finally:
            if conn_manager is not None:
                conn_manager.end_scope(error=error)

    @staticmethod
    def _coerce_json(value):
        """psycopg2 may hand back jsonb as a str depending on adapter config."""
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (ValueError, TypeError):
                return {}
        return value or {}

    # ---- delivery queue -------------------------------------------------
    def enqueue_deliveries(self, rows: List[Dict[str, Any]]) -> int:
        # One multi-row INSERT so an alert's per-channel deliveries are enqueued
        # atomically (a single bad row can't leave a partial, half-delivered
        # fan-out) and in one round-trip instead of N. Positional %s for the batch
        # VALUES, matching the bulk-insert idiom in
        # rule_execution_service._apply_tags_batch.
        if not rows:
            return 0
        values_sql, params = [], []
        for row in rows:
            values_sql.append("(%s::uuid, %s::uuid, %s, 'pending', %s::jsonb, %s)")
            params.extend([
                str(row['alert_id']), str(row['channel_id']), row['channel_type'],
                json.dumps(_clean_nan(row['payload'])), row.get('trigger', 'created'),
            ])
        self.db.execute_query(
            "INSERT INTO alert_deliveries "
            "(alert_id, channel_id, channel_type, status, payload, trigger) VALUES "
            + ", ".join(values_sql),
            params, fetch=False,
        )
        return len(rows)

    def claim_pending_deliveries(self, limit: int) -> List[Dict[str, Any]]:
        # FOR UPDATE SKIP LOCKED only protects this SELECT's own transaction: each
        # execute_query() call (claim, then the eventual mark_sent/mark_retry/mark_failed)
        # is its own transaction, so the row lock is released the instant this query
        # returns — it does NOT span claim -> send -> mark. That's safe only because the
        # scheduler worker runs the drain single-instance (max_instances=1). A
        # multi-instance drain would need an explicit 'sending' status claim marker
        # (set here, cleared by mark_*) to prevent two workers double-sending the same row.
        rows = self.db.execute_query(
            """
            SELECT id, alert_id, channel_id, channel_type, attempts, payload
            FROM alert_deliveries
            WHERE status = 'pending' AND next_retry_at <= now()
            ORDER BY next_retry_at
            LIMIT %(limit)s
            FOR UPDATE SKIP LOCKED
            """,
            {'limit': limit},
        ) or []
        # Coerce payload the same way channel config is coerced on read: some
        # adapter configs hand jsonb back as a str, and drain() calls
        # channel.send(config, payload) expecting a dict (payload.get(...)).
        for r in rows:
            r['payload'] = self._coerce_json(r.get('payload'))
        return rows

    def mark_sent(self, delivery_id: str, response_code: Optional[int] = None) -> None:
        self.db.execute_query(
            """
            UPDATE alert_deliveries
            SET status = 'sent', sent_at = now(), attempts = attempts + 1,
                response_code = %(response_code)s, last_error = NULL
            WHERE id = %(id)s::uuid
            """,
            {'id': str(delivery_id), 'response_code': response_code}, fetch=False,
        )

    def mark_retry(self, delivery_id: str, attempts: int, next_retry_at, error: str) -> None:
        self.db.execute_query(
            """
            UPDATE alert_deliveries
            SET status = 'pending', attempts = %(attempts)s,
                next_retry_at = %(next_retry_at)s, last_error = %(error)s
            WHERE id = %(id)s::uuid
            """,
            {'id': str(delivery_id), 'attempts': attempts,
             'next_retry_at': next_retry_at, 'error': error}, fetch=False,
        )

    def mark_failed(self, delivery_id: str, attempts: int, error: str) -> None:
        self.db.execute_query(
            """
            UPDATE alert_deliveries
            SET status = 'failed', attempts = %(attempts)s, last_error = %(error)s
            WHERE id = %(id)s::uuid
            """,
            {'id': str(delivery_id), 'attempts': attempts, 'error': error}, fetch=False,
        )

    def record_test_delivery(self, channel_id, channel_type, payload, result) -> None:
        self.db.execute_query(
            """
            INSERT INTO alert_deliveries
                (alert_id, channel_id, channel_type, status, attempts, payload,
                 trigger, sent_at, last_error, response_code)
            VALUES
                (NULL, %(channel_id)s::uuid, %(channel_type)s, %(status)s, 1,
                 %(payload)s::jsonb, 'test', now(), %(error)s, %(code)s)
            """,
            {'channel_id': str(channel_id), 'channel_type': channel_type,
             'payload': json.dumps(_clean_nan(payload)), 'status': 'sent' if result.ok else 'failed',
             'error': result.error, 'code': result.response_code}, fetch=False,
        )
