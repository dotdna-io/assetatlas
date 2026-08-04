# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Asset History Service

Transforms consolidated chronicle events into per-attribute events
for the Asset History UI.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.database.postgres_adapter import PostgreSQLAdapter

logger = logging.getLogger(__name__)

DEFAULT_SOURCE = 'AssetAtlas'

# Time range presets
TIME_RANGES = {
    '24h': timedelta(hours=24),
    '7d': timedelta(days=7),
    '30d': timedelta(days=30),
    '90d': timedelta(days=90),
}


class AssetHistoryService:
    """Transforms consolidated chronicle events into per-attribute UI events."""

    def __init__(self, db_adapter: PostgreSQLAdapter):
        self.db = db_adapter

    def get_asset_history(
        self,
        asset_id: str,
        time_range: str = '30d',
        limit: int = 500
    ) -> Optional[Dict[str, Any]]:
        """
        Get asset history formatted for the UI.

        Args:
            asset_id: Asset UUID string
            time_range: Time window preset (24h, 7d, 30d, 90d)
            limit: Max chronicle events to fetch

        Returns:
            Dict matching UI expected format, or None if asset not found
        """
        asset_meta = self._get_asset_metadata(asset_id)
        if not asset_meta:
            return None

        delta = TIME_RANGES.get(time_range, TIME_RANGES['30d'])
        end_time = datetime.now(timezone.utc)
        start_time = end_time - delta

        chronicle_events = self._get_chronicle_events(asset_id, start_time, end_time, limit)

        all_events = []
        for chronicle_event in chronicle_events:
            all_events.extend(self._transform_chronicle_event(chronicle_event))

        all_events.sort(key=lambda e: e['timestamp'], reverse=True)

        attribute_changes = sum(1 for e in all_events if e['type'] == 'attribute')

        return {
            'asset': asset_meta,
            'summary': {
                'total_events': len(all_events),
                'attribute_changes': attribute_changes,
                'alerts': 0,
            },
            'events': all_events,
        }

    def _get_asset_metadata(self, asset_id: str) -> Optional[Dict[str, Any]]:
        """Fetch asset metadata for the response header."""
        query = """
            SELECT id, hostname, ip, mac, category, os_normalized, last_seen
            FROM assets
            WHERE id = %s::uuid
        """
        try:
            with self.db.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, (asset_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                cols = [desc[0] for desc in cursor.description]
                data = dict(zip(cols, row))

                return {
                    'id': str(data['id']),
                    'hostname': self._flatten_value(data.get('hostname')),
                    'ip': self._flatten_value(data.get('ip')),
                    'mac': self._flatten_value(data.get('mac')),
                    'category': data.get('category'),
                    'os': data.get('os_normalized'),
                    'last_seen': data['last_seen'].isoformat() if data.get('last_seen') else None,
                }
        except Exception as e:
            logger.error(f"Error fetching asset metadata for {asset_id}: {e}")
            return None

    def _get_chronicle_events(
        self, asset_id: str, start_time: datetime, end_time: datetime, limit: int
    ) -> List[Dict[str, Any]]:
        """Fetch chronicle events from DB."""
        query = """
            SELECT id, time, event_type, changed_attributes,
                   attribute_changes, full_snapshot
            FROM asset_chronicle
            WHERE asset_id = %s::uuid
              AND time >= %s AND time <= %s
            ORDER BY time DESC
            LIMIT %s
        """
        try:
            with self.db.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, (asset_id, start_time, end_time, limit))
                cols = [desc[0] for desc in cursor.description]
                return [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error fetching chronicle events for {asset_id}: {e}")
            return []

    def _transform_chronicle_event(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Transform a single consolidated chronicle event into per-attribute UI events."""
        event_type = event.get('event_type')
        event_id = event.get('id')
        timestamp = event.get('time')

        if isinstance(timestamp, datetime):
            timestamp_str = timestamp.isoformat()
        else:
            timestamp_str = str(timestamp) if timestamp else None

        if event_type == 'attribute_updated':
            return self._transform_update_event(event_id, timestamp_str, event)
        elif event_type in ('asset_created', 'asset_readded'):
            return self._transform_creation_event(event_id, timestamp_str, event)
        elif event_type == 'asset_removed':
            return self._transform_removal_event(event_id, timestamp_str)
        else:
            return []

    def _transform_update_event(
        self, event_id: int, timestamp: str, event: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Explode attribute_changes JSONB into individual events."""
        attribute_changes = event.get('attribute_changes') or {}
        results = []

        for attr_name, changes in attribute_changes.items():
            old_val = changes.get('old', [])
            new_val = changes.get('new', [])
            source = changes.get('source')

            old_dict = self._unwrap_single_dict(old_val)
            new_dict = self._unwrap_single_dict(new_val)
            if old_dict is not None or new_dict is not None:
                results.extend(self._explode_dict_diff(
                    event_id, timestamp, attr_name,
                    old_dict or {}, new_dict or {}, source,
                ))
                continue

            results.append({
                'id': f'{event_id}-{attr_name}',
                'timestamp': timestamp,
                'type': 'attribute',
                'action': self._derive_action(old_val, new_val),
                'attribute': attr_name,
                'old_value': self._flatten_value(old_val),
                'new_value': self._flatten_value(new_val),
                'source': source or DEFAULT_SOURCE,
            })

        return results

    @staticmethod
    def _unwrap_single_dict(val: Any) -> Optional[Dict[str, Any]]:
        """Return the dict if val is a single-element list wrapping a dict, else None.

        Chronicle stores dict-valued attribute changes by wrapping the dict in
        a single-element list (so ``old`` / ``new`` always carry list shape).
        Detecting that shape lets the caller drill into the dict instead of
        stringifying it.
        """
        if isinstance(val, dict):
            return val
        if isinstance(val, list) and len(val) == 1 and isinstance(val[0], dict):
            return val[0]
        return None

    def _explode_dict_diff(
        self,
        event_id: int,
        timestamp: str,
        prefix: str,
        old_d: Dict[str, Any],
        new_d: Dict[str, Any],
        source: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Walk two dicts, emit one row per leaf key whose value differs.

        Nested dicts recurse with dotted attribute names
        (e.g. ``extended_attributes.enrichment.vendor``) so each row in the
        history table shows a single scalar transition instead of a JSON blob.
        """
        results = []
        for key in set(old_d.keys()) | set(new_d.keys()):
            path = f'{prefix}.{key}'
            ov = old_d.get(key)
            nv = new_d.get(key)
            if isinstance(ov, dict) or isinstance(nv, dict):
                results.extend(self._explode_dict_diff(
                    event_id, timestamp, path,
                    ov if isinstance(ov, dict) else {},
                    nv if isinstance(nv, dict) else {},
                    source,
                ))
                continue
            if ov == nv:
                continue
            results.append({
                'id': f'{event_id}-{path}',
                'timestamp': timestamp,
                'type': 'attribute',
                'action': self._derive_action(ov, nv),
                'attribute': path,
                'old_value': self._flatten_value(ov),
                'new_value': self._flatten_value(nv),
                'source': source or DEFAULT_SOURCE,
            })
        return results

    def _transform_creation_event(
        self, event_id: int, timestamp: str, event: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Emit one 'added' event per field in full_snapshot."""
        snapshot = event.get('full_snapshot') or {}
        results = []

        for attr_name, value in snapshot.items():
            flat_value = self._flatten_value(value)
            if flat_value is not None:
                results.append({
                    'id': f'{event_id}-{attr_name}',
                    'timestamp': timestamp,
                    'type': 'attribute',
                    'action': 'added',
                    'attribute': attr_name,
                    'old_value': None,
                    'new_value': flat_value,
                    'source': DEFAULT_SOURCE,
                })

        return results

    def _transform_removal_event(self, event_id: int, timestamp: str) -> List[Dict[str, Any]]:
        """Emit a single removal event."""
        return [{
            'id': f'{event_id}-asset',
            'timestamp': timestamp,
            'type': 'attribute',
            'action': 'removed',
            'attribute': 'asset',
            'old_value': None,
            'new_value': None,
            'source': DEFAULT_SOURCE,
        }]

    @staticmethod
    def _flatten_value(value: Any) -> Optional[str]:
        """Flatten array/scalar to display string."""
        if value is None:
            return None
        if isinstance(value, list):
            if not value:
                return None
            str_vals = [str(v) for v in value if v is not None]
            if not str_vals:
                return None
            if len(str_vals) == 1:
                return str_vals[0]
            return ', '.join(str_vals)
        return str(value)

    @staticmethod
    def _derive_action(old: Any, new: Any) -> str:
        """Derive action string from old/new values."""
        old_empty = not old if isinstance(old, list) else old is None
        new_empty = not new if isinstance(new, list) else new is None

        if old_empty and not new_empty:
            return 'added'
        if not old_empty and new_empty:
            return 'removed'
        return 'updated'
