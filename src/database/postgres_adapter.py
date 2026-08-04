# SPDX-License-Identifier: AGPL-3.0-or-later
"""
PostgreSQL Database Adapter for AssetAtlas

Provides high-level database operations for asset generation, including:
- Asset ID mapping persistence
- Asset CRUD operations
- Chronicle event writing
- Raw data queries
"""

import logging
import math
import time
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from uuid import UUID

import psycopg2
from psycopg2 import sql, extras

from src.database.postgres_connection import PostgreSQLConnectionManager, get_connection_manager

logger = logging.getLogger(__name__)


# Lazily-bound Prometheus hook. Bound on first use (then a direct call) so the
# DB layer doesn't import flask/prometheus at module-import time — keeping the
# adapter importable in minimal contexts and free of any import cycle.
_track_db_query = None

# Columns bulk_upsert_assets must never overwrite with a NULL it was not given.
# See the ON CONFLICT clause there for why (#1154).
_PRESERVE_IF_NOT_SUPPLIED = frozenset({'category_predicted', 'subcategory_predicted'})


def _classify_query_type(query: str) -> str:
    """Coarse first-keyword bucket for the db_query_duration metric label.
    Kept tiny — it runs on every execute_query()."""
    kw = query.lstrip()[:6].lower()
    if kw.startswith("select"):
        return "select"
    if kw.startswith("insert"):
        return "insert"
    if kw.startswith("update"):
        return "update"
    if kw.startswith("delete"):
        return "delete"
    return "other"


def _observe_db_query(query: str, start_monotonic: float) -> None:
    """Best-effort: record a query's duration. Never raises — a metrics error
    must not break a database call."""
    global _track_db_query
    try:
        if _track_db_query is None:
            from src.api.utils.metrics import track_db_query

            _track_db_query = track_db_query
        _track_db_query(
            _classify_query_type(query), time.monotonic() - start_monotonic
        )
    except Exception:
        pass


def _clean_nan(obj):
    """Recursively replace float NaN with None in nested dicts/lists.

    Required because psycopg2's Json adapter serializes Python float('nan')
    as the invalid JSON token ``NaN``.
    """
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_nan(v) for v in obj]
    if isinstance(obj, float) and math.isnan(obj):
        return None
    return obj


# pandas is imported lazily inside _is_nan_or_nat to avoid loading numpy
# at module-import time. numpy raises "cannot load module more than once"
# when reloaded under coverage.py instrumentation, which broke
# `pytest --cov=src` collection of tests that imported postgres_adapter
# transitively (notably tests/unit/blueprints/test_mfa_bp.py).
_pd_isna = None


def _is_nan_or_nat(val):
    """Check for pandas NaT, numpy NaN types, and other NA-like values.

    Catches values that escape the basic ``isinstance(val, float) and val != val``
    check, such as pandas NaT and numpy non-float64 NaN types.
    """
    global _pd_isna
    if _pd_isna is None:
        import pandas as _pd
        _pd_isna = _pd.isna
    try:
        if _pd_isna(val):
            return True
    except (ValueError, TypeError):
        pass
    return False


class PostgreSQLAdapter:
    """Database adapter for AssetAtlas asset generation operations."""

    def __init__(self, connection_manager: Optional[PostgreSQLConnectionManager] = None):
        """
        Initialize PostgreSQL adapter.

        Args:
            connection_manager: Optional connection manager (uses global if not provided)
        """
        self.conn_manager = connection_manager or get_connection_manager()
        logger.info("PostgreSQL adapter initialized")

    # ============================================================================
    # ASSET ID MAPPING OPERATIONS
    # ============================================================================

    def get_asset_id_mapping(self, component: str) -> Optional[UUID]:
        """
        Get asset ID for a component.

        Args:
            component: Component identifier (e.g., "hostname", "sn:ABC123")

        Returns:
            Asset UUID if found, None otherwise
        """
        query = "SELECT asset_id FROM asset_id_mappings WHERE component = %s"

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, (component,))
                result = cursor.fetchone()
                return UUID(result[0]) if result else None

        except Exception as e:
            logger.error(f"Error getting asset ID mapping for {component}: {e}")
            raise

    def set_asset_id_mapping(self, component: str, asset_id: UUID) -> None:
        """
        Set or update asset ID mapping for a component.

        Updates last_seen timestamp on every update to track component activity.

        Args:
            component: Component identifier
            asset_id: Asset UUID
        """
        query = """
            INSERT INTO asset_id_mappings (component, asset_id, created_at, first_seen, last_seen)
            VALUES (%s, %s::uuid, NOW(), NOW(), NOW())
            ON CONFLICT (component)
            DO UPDATE SET
                asset_id = EXCLUDED.asset_id,
                updated_at = NOW(),
                last_seen = NOW()
        """

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, (component, str(asset_id)))
                self.conn_manager.commit_unless_scoped(conn)
                logger.debug(f"Set asset ID mapping: {component} -> {asset_id}")

        except Exception as e:
            logger.error(f"Error setting asset ID mapping for {component}: {e}")
            raise

    def bulk_set_asset_id_mappings(self, mappings: List[Tuple[str, UUID]]) -> int:
        """
        Bulk insert/update asset ID mappings.

        Updates last_seen timestamp for all mappings to track component activity.

        Args:
            mappings: List of (component, asset_id) tuples

        Returns:
            Number of mappings updated
        """
        if not mappings:
            return 0

        query = """
            INSERT INTO asset_id_mappings (component, asset_id, created_at, first_seen, last_seen)
            VALUES %s
            ON CONFLICT (component)
            DO UPDATE SET
                asset_id = EXCLUDED.asset_id,
                updated_at = NOW(),
                last_seen = NOW()
        """

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                now = datetime.now()
                extras.execute_values(
                    cursor,
                    query,
                    [(comp, str(asset_id), now, now, now) for comp, asset_id in mappings],
                    template="(%s, %s::uuid, %s, %s, %s)",
                    page_size=1000,
                )
                count = cursor.rowcount
                self.conn_manager.commit_unless_scoped(conn)
                logger.info(f"Bulk updated {count} asset ID mappings")
                return count

        except Exception as e:
            logger.error(f"Error bulk setting asset ID mappings: {e}")
            raise

    def get_all_asset_id_mappings(self) -> Dict[str, UUID]:
        """
        Get all asset ID mappings.

        Returns:
            Dictionary of component -> asset_id mappings
        """
        query = "SELECT component, asset_id FROM asset_id_mappings"

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query)
                return {row[0]: UUID(row[1]) for row in cursor.fetchall()}

        except Exception as e:
            logger.error(f"Error getting all asset ID mappings: {e}")
            raise

    def get_asset_id_mappings_for_components(self, components: list) -> Dict[str, UUID]:
        """
        Get asset ID mappings only for specified components.

        More efficient than get_all_asset_id_mappings when you know which
        components will be used.

        Args:
            components: List of component identifiers

        Returns:
            Dictionary of component -> asset_id mappings (only for specified components)
        """
        if not components:
            return {}

        query = "SELECT component, asset_id FROM asset_id_mappings WHERE component = ANY(%s)"

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, (components,))
                return {row[0]: UUID(row[1]) for row in cursor.fetchall()}

        except Exception as e:
            logger.error(f"Error getting asset ID mappings for components: {e}")
            raise

    def delete_expired_mappings(self, expiration_days: int = 30) -> int:
        """
        Delete component mappings that haven't been seen in the specified number of days.

        Components expire after the specified period of inactivity. This prevents
        stale components (e.g., reassigned IPs, decommissioned devices) from
        incorrectly linking assets.

        Args:
            expiration_days: Number of days of inactivity before expiration (default: 30)

        Returns:
            Number of mappings deleted
        """
        expiration_days = expiration_days or 30  # Default fallback if None (#104)
        query = """
            DELETE FROM asset_id_mappings
            WHERE last_seen < NOW() - INTERVAL '%s days'
        """

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, (expiration_days,))
                count = cursor.rowcount
                self.conn_manager.commit_unless_scoped(conn)

                if count > 0:
                    logger.info(
                        f"Deleted {count} expired component mappings "
                        f"(inactive for >{expiration_days} days)"
                    )
                else:
                    logger.debug(f"No expired mappings found (expiration: {expiration_days} days)")

                return count

        except Exception as e:
            logger.error(f"Error deleting expired mappings: {e}")
            raise

    def touch_asset_mappings(self, asset_ids: List[str]) -> int:
        """
        Refresh last_seen=NOW() for all mappings of the given asset ids.

        Used by the grace carry-forward stage (#795): while an asset is kept
        in inventory despite being absent from input, its identifier mappings
        must stay alive so a return reconstructs the SAME id (no duplicate row).

        Returns the number of mapping rows updated.
        """
        if not asset_ids:
            return 0
        ids = [str(a) for a in asset_ids]
        query = """
            UPDATE asset_id_mappings
            SET last_seen = NOW()
            WHERE asset_id = ANY(%s::uuid[])
        """
        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, (ids,))
                count = cursor.rowcount
                self.conn_manager.commit_unless_scoped(conn)
                logger.info("Refreshed last_seen on %d mappings for %d carried assets", count, len(ids))
                return count
        except Exception as e:
            logger.error(f"Error refreshing mapping last_seen: {e}")
            raise

    # ============================================================================
    # ASSET CRUD OPERATIONS
    # ============================================================================

    def get_asset_by_id(self, asset_id: UUID) -> Optional[Dict[str, Any]]:
        """
        Get asset by ID.

        Args:
            asset_id: Asset UUID

        Returns:
            Asset dictionary if found, None otherwise
        """
        query = "SELECT * FROM assets WHERE id = %s::uuid"

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, (str(asset_id),))
                result = cursor.fetchone()
                return dict(result) if result else None

        except Exception as e:
            logger.error(f"Error getting asset {asset_id}: {e}")
            raise

    def get_all_assets(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get all assets.

        Args:
            limit: Optional cap on returned rows. None = no LIMIT clause.
                   limit=0 is honoured literally (returns []), unlike the
                   pre-#596 truthy-check behaviour that silently dropped the
                   LIMIT for any falsy value.

        Returns:
            List of asset dictionaries
        """
        query = "SELECT * FROM assets ORDER BY updated_at DESC"
        params: Optional[Tuple[Any, ...]] = None
        if limit is not None:
            query += " LIMIT %s"
            params = (int(limit),)

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            logger.error(f"Error getting all assets: {e}")
            raise

    def bulk_upsert_assets(self, assets: List[Dict[str, Any]]) -> int:
        """
        Bulk insert or update assets.

        Args:
            assets: List of asset dictionaries with all required fields

        Returns:
            Number of assets processed (note: with execute_values and ON CONFLICT,
            rowcount may not accurately reflect inserts vs updates)
        """
        if not assets:
            return 0

        # Define columns (excluding auto-managed fields)
        columns = [
            'id', 'asset', 'hostname', 'fqdn', 'ip', 'mac', 'sn',
            'category', 'subcategory', 'os_normalized', 'os', 'os_orig',
            'mac_vendor_enriched', 'manufacturer', 'model',
            'data_source', 'data_source_category', 'vendor_category',
            'confidence', 'confidence_breakdown',
            'ipam_location', 'owner', 'location',
            'user_field', 'extended_attributes', 'os_enrichment',
            # OS EOL tracking columns
            'os_eol_status', 'os_eol_date', 'os_support_end_date',
            'os_extended_support_date', 'os_is_lts', 'os_eol_checked_at',
            'first_time', 'last_time',
            'first_seen', 'last_seen', 'missing_since', 'last_generation_run_id',  # Lifecycle tracking columns
            'asset_label_override',  # Per-asset manual override (#639); re-applied by s15b
            # Pre-override pipeline prediction, snapshotted by s13 (#1154). Omitting
            # these is what left category_predicted NULL on every row since 035 and
            # made "Reset to predicted" blank the asset instead of restoring it.
            'category_predicted', 'subcategory_predicted',
        ]

        # Build update clause for ON CONFLICT. The predicted columns COALESCE
        # against the stored row: values come from `asset.get(col)`, so a write
        # path that doesn't supply the key sends NULL rather than raising, and a
        # plain EXCLUDED assignment would erase the snapshot that clearing a
        # category override restores. A prediction is never legitimately NULL
        # (category_best always carries a value, even 'unknown'), so preferring
        # the stored value costs nothing in the happy path.
        #
        # Scope, so nobody over-trusts it: the pipeline's only caller
        # (asset_repository.save_assets) archives + TRUNCATEs `assets` first, so
        # ON CONFLICT never fires there and every row is a plain INSERT. This
        # protects the update path (a future partial writer); what keeps the
        # snapshot alive across a rebuild is s13 populating it on every run.
        update_clause = sql.SQL(', ').join([
            sql.SQL("{} = COALESCE(EXCLUDED.{}, assets.{})").format(
                sql.Identifier(col), sql.Identifier(col), sql.Identifier(col)
            )
            if col in _PRESERVE_IF_NOT_SUPPLIED
            else sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(col), sql.Identifier(col))
            for col in columns if col != 'id'  # Don't update ID
        ])

        query = sql.SQL("""
            INSERT INTO assets ({columns})
            VALUES %s
            ON CONFLICT (id)
            DO UPDATE SET {updates}, updated_at = NOW()
        """).format(
            columns=sql.SQL(', ').join(map(sql.Identifier, columns)),
            updates=update_clause
        )

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                # Prepare values tuples - convert UUID to string and dict to Json
                # Pre-identify which columns have dict/UUID values to reduce per-cell checks
                _Json = psycopg2.extras.Json
                _is_nan = _is_nan_or_nat
                values = []
                for asset in assets:
                    row = []
                    _get = asset.get
                    for col in columns:
                        val = _get(col)
                        if val is None:
                            row.append(None)
                        elif isinstance(val, str):
                            # Most values are strings — fast path
                            row.append(val)
                        elif isinstance(val, list):
                            row.append(val)
                        elif isinstance(val, dict):
                            row.append(_Json(_clean_nan(val)))
                        elif isinstance(val, float):
                            row.append(None if val != val else val)
                        elif isinstance(val, UUID):
                            row.append(str(val))
                        elif _is_nan(val):
                            row.append(None)
                        else:
                            row.append(val)
                    values.append(tuple(row))

                # Use execute_values for efficient batch insert
                extras.execute_values(cursor, query.as_string(conn), values, page_size=1000)

                # NOTE: cursor.rowcount with execute_values and ON CONFLICT is unreliable
                # It may return a partial count due to internal batching
                # We return the total number of assets processed instead
                count = len(assets)
                self.conn_manager.commit_unless_scoped(conn)
                logger.info(f"Bulk upserted {count} assets (all processed, inserted or updated)")
                return count

        except Exception as e:
            logger.error(f"Error bulk upserting assets: {e}")
            raise

    def delete_asset(self, asset_id: UUID) -> bool:
        """
        Delete an asset by ID.

        Args:
            asset_id: Asset UUID

        Returns:
            True if deleted, False if not found
        """
        query = "DELETE FROM assets WHERE id = %s::uuid"

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, (str(asset_id),))
                deleted = cursor.rowcount > 0
                self.conn_manager.commit_unless_scoped(conn)

                if deleted:
                    logger.info(f"Deleted asset {asset_id}")
                else:
                    logger.warning(f"Asset {asset_id} not found for deletion")

                return deleted

        except Exception as e:
            logger.error(f"Error deleting asset {asset_id}: {e}")
            raise

    # ============================================================================
    # CHRONICLE EVENT OPERATIONS
    # ============================================================================

    def write_chronicle_event(
        self,
        asset_id: UUID,
        event_type: str,
        attribute: Optional[str] = None,
        old_value: Any = None,
        new_value: Any = None,
        values_added: Any = None,
        values_removed: Any = None,
        time: Optional[datetime] = None,
    ) -> None:
        """
        Write a single chronicle event.

        Args:
            asset_id: Asset UUID
            event_type: Event type (e.g., 'created', 'updated', 'deleted')
            attribute: Attribute that changed
            old_value: Previous value
            new_value: New value
            values_added: Values added (for arrays)
            values_removed: Values removed (for arrays)
            time: Event timestamp (defaults to now)
        """
        query = """
            INSERT INTO asset_chronicle (
                time, asset_id, event_type, attribute,
                old_value, new_value, values_added, values_removed
            )
            VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s)
        """

        event_time = time or datetime.now()

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(
                    query,
                    (
                        event_time, str(asset_id), event_type, attribute,
                        psycopg2.extras.Json(_clean_nan(old_value)) if old_value is not None else None,
                        psycopg2.extras.Json(_clean_nan(new_value)) if new_value is not None else None,
                        psycopg2.extras.Json(_clean_nan(values_added)) if values_added is not None else None,
                        psycopg2.extras.Json(_clean_nan(values_removed)) if values_removed is not None else None,
                    ),
                )
                self.conn_manager.commit_unless_scoped(conn)
                logger.debug(f"Wrote chronicle event for asset {asset_id}: {event_type}")

        except Exception as e:
            logger.error(f"Error writing chronicle event: {e}")
            raise

    def bulk_write_chronicle_events(self, events: List[Dict[str, Any]]) -> int:
        """
        Bulk write chronicle events with consolidated schema.

        Args:
            events: List of event dictionaries with keys:
                Required:
                - asset_id: UUID of the asset
                - event_type: Type of event (asset_created, attribute_updated, asset_removed, etc.)

                Consolidated fields:
                - changed_attributes: Array of attribute names that changed
                - attribute_changes: JSONB with detailed changes per attribute
                - full_snapshot: Complete asset state (for creation/re-add events)

                Audit trail:
                - generation_run_id: UUID of pipeline run
                - operation_id: UUID linking related events
                - causation: Why this event occurred
                - related_asset_id: Related asset (for merges)

                Optional:
                - time: Event timestamp (defaults to now)

        Returns:
            Number of events written
        """
        if not events:
            return 0

        query = """
            INSERT INTO asset_chronicle (
                time, asset_id, event_type,
                generation_run_id, changed_attributes, attribute_changes,
                full_snapshot, operation_id, causation, related_asset_id
            )
            VALUES %s
            ON CONFLICT (time, asset_id) DO NOTHING
        """

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                # Prepare values
                now = datetime.now()
                values = [
                    (
                        event.get('time', now),
                        str(event['asset_id']) if isinstance(event['asset_id'], UUID) else event['asset_id'],
                        event['event_type'],
                        # Audit trail
                        event.get('generation_run_id'),
                        # Consolidated fields
                        event.get('changed_attributes'),  # Array field
                        psycopg2.extras.Json(_clean_nan(event['attribute_changes'])) if event.get('attribute_changes') is not None else None,
                        psycopg2.extras.Json(_clean_nan(event['full_snapshot'])) if event.get('full_snapshot') is not None else None,
                        event.get('operation_id'),
                        event.get('causation'),
                        event.get('related_asset_id'),
                    )
                    for event in events
                ]

                extras.execute_values(cursor, query, values, page_size=1000)
                count = cursor.rowcount
                self.conn_manager.commit_unless_scoped(conn)
                logger.info(f"Bulk wrote {count} chronicle events")
                return count

        except Exception as e:
            logger.error(f"Error bulk writing chronicle events: {e}")
            raise

    def get_asset_chronicle(
        self,
        asset_id: UUID,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get chronicle events for an asset.

        Args:
            asset_id: Asset UUID
            start_time: Optional start time filter
            end_time: Optional end time filter
            limit: Maximum number of events to return

        Returns:
            List of chronicle event dictionaries
        """
        query = "SELECT * FROM asset_chronicle WHERE asset_id = %s::uuid"
        params = [str(asset_id)]

        if start_time:
            query += " AND time >= %s"
            params.append(start_time)

        if end_time:
            query += " AND time <= %s"
            params.append(end_time)

        query += " ORDER BY time DESC LIMIT %s"
        params.append(limit)

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            logger.error(f"Error getting chronicle for asset {asset_id}: {e}")
            raise

    # ============================================================================
    # RAW DATA OPERATIONS
    # ============================================================================

    def get_raw_asset_data(
        self,
        integration_id: Optional[int] = None,
        batch_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get raw asset data for processing.

        Args:
            integration_id: Optional integration filter
            batch_id: Optional batch filter
            limit: Optional result limit

        Returns:
            List of raw asset data dictionaries
        """
        query = "SELECT * FROM raw_asset_data WHERE 1=1"
        params = []

        if integration_id:
            query += " AND integration_id = %s"
            params.append(integration_id)

        if batch_id:
            query += " AND batch_id = %s"
            params.append(batch_id)

        query += " ORDER BY ingested_at DESC"

        if limit:
            query += " LIMIT %s"
            params.append(limit)

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            logger.error(f"Error getting raw asset data: {e}")
            raise

    # ============================================================================
    # UTILITY OPERATIONS
    # ============================================================================

    def execute_query(
        self,
        query: str,
        params: Optional[tuple] = None,
        fetch: bool = True,
        commit: bool = True,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Execute a custom SQL query.

        Args:
            query: SQL query string
            params: Query parameters
            fetch: Whether to fetch and return results
            commit: Whether to commit the transaction after execution

        Returns:
            Query results if fetch=True, None otherwise
        """
        _q_start = time.monotonic()
        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, params or ())

                if fetch:
                    results = [dict(row) for row in cursor.fetchall()]
                    if commit:
                        self.conn_manager.commit_unless_scoped(conn)
                    return results
                else:
                    if commit:
                        self.conn_manager.commit_unless_scoped(conn)
                    return None

        except Exception as e:
            logger.error(f"Error executing query: {e}")
            raise
        finally:
            _observe_db_query(query, _q_start)

    def get_table_stats(self) -> Dict[str, int]:
        """
        Get row counts for all main tables.

        Returns:
            Dictionary of table_name -> row_count
        """
        tables = [
            'assets',
            'asset_id_mappings',
            'asset_chronicle',
            'raw_asset_data',
            'raw_source_data',
        ]

        stats = {}

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                for table in tables:
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    stats[table] = cursor.fetchone()[0]

            logger.info(f"Table stats: {stats}")
            return stats

        except Exception as e:
            logger.error(f"Error getting table stats: {e}")
            raise

    # ============================================================================
    # HISTORY OPERATIONS
    # ============================================================================

    def archive_assets_to_history(self, generation_run_id: Optional[UUID] = None) -> int:
        """
        Archive all current assets to assets_history table.
        This should be called BEFORE new assets are saved.

        Args:
            generation_run_id: Optional UUID to link this archive to a specific generation run

        Returns:
            Number of assets archived
        """
        query = """
            INSERT INTO assets_history (
                generation_run_id, archived_at,
                id, asset, hostname, fqdn, ip, mac, sn,
                category, subcategory,
                os_normalized, os, os_orig, os_enrichment, mac_vendor_enriched,
                manufacturer, model, data_source,
                data_source_category, vendor_category,
                confidence,
                ipam_location, owner, location,
                user_field, extended_attributes,
                first_time, last_time, updated_at
            )
            SELECT
                %s, NOW(),
                id, asset, hostname, fqdn, ip, mac, sn,
                category, subcategory,
                os_normalized, os, os_orig, os_enrichment, mac_vendor_enriched,
                manufacturer, model, data_source,
                data_source_category, vendor_category,
                confidence,
                ipam_location, owner, location,
                user_field, extended_attributes,
                first_time, last_time, updated_at
            FROM assets
        """

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                # Archive current assets
                cursor.execute(query, (str(generation_run_id) if generation_run_id else None,))
                archived_count = cursor.rowcount

                # Clear current assets table (CASCADE will also clear asset_providers)
                cursor.execute("TRUNCATE assets CASCADE")

                self.conn_manager.commit_unless_scoped(conn)
                logger.info(f"Archived {archived_count} assets to history (run_id: {generation_run_id})")
                return archived_count

        except Exception as e:
            logger.error(f"Error archiving assets to history: {e}")
            raise

    def archive_asset_providers_to_history(self, generation_run_id: Optional[UUID] = None) -> int:
        """
        Archive all current asset_providers to asset_providers_history table.
        This should be called BEFORE new providers are saved.

        Note: This is typically not called separately since TRUNCATE assets CASCADE
        already clears asset_providers. This method is provided for explicit control.

        Args:
            generation_run_id: Optional UUID to link this archive to a specific generation run

        Returns:
            Number of provider records archived
        """
        query = """
            INSERT INTO asset_providers_history (
                generation_run_id, archived_at,
                id, asset_id, data_source, data_source_category,
                hostname, fqdn, ip, mac, sn, os,
                manufacturer, model, vendor_category,
                data_source_fields, category_prediction, updated_at
            )
            SELECT
                %s, NOW(),
                id, asset_id, data_source, data_source_category,
                hostname, fqdn, ip, mac, sn, os,
                manufacturer, model, vendor_category,
                data_source_fields, category_prediction, updated_at
            FROM asset_providers
        """

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                # Archive current providers
                cursor.execute(query, (str(generation_run_id) if generation_run_id else None,))
                archived_count = cursor.rowcount

                # Clear current providers table
                cursor.execute("TRUNCATE asset_providers")

                self.conn_manager.commit_unless_scoped(conn)
                logger.info(f"Archived {archived_count} provider records to history (run_id: {generation_run_id})")
                return archived_count

        except Exception as e:
            logger.error(f"Error archiving asset providers to history: {e}")
            raise

    def archive_all_to_history(self, generation_run_id: Optional[UUID] = None) -> Dict[str, int]:
        """
        Archive both assets and asset_providers to history in a single transaction.
        This is the preferred method to ensure consistency.

        Args:
            generation_run_id: Optional UUID to link this archive to a specific generation run

        Returns:
            Dictionary with counts: {'assets': count, 'providers': count}
        """
        query_assets = """
            INSERT INTO assets_history (
                generation_run_id, archived_at,
                id, asset, hostname, fqdn, ip, mac, sn,
                category, subcategory,
                os_normalized, os, os_orig, os_enrichment, mac_vendor_enriched,
                os_eol_status, os_eol_date, os_is_lts,
                manufacturer, model, data_source,
                data_source_category, vendor_category,
                confidence, confidence_breakdown,
                ipam_location, owner, location,
                user_field, extended_attributes, tags, posture_rules,
                first_time, last_time, updated_at,
                first_seen, last_seen, missing_since, last_generation_run_id
            )
            SELECT
                %s, NOW(),
                id, asset, hostname, fqdn, ip, mac, sn,
                category, subcategory,
                os_normalized, os, os_orig, os_enrichment, mac_vendor_enriched,
                os_eol_status, os_eol_date, os_is_lts,
                manufacturer, model, data_source,
                data_source_category, vendor_category,
                confidence, confidence_breakdown,
                ipam_location, owner, location,
                user_field, extended_attributes, tags, posture_rules,
                first_time, last_time, updated_at,
                first_seen, last_seen, missing_since, last_generation_run_id
            FROM assets
        """

        query_providers = """
            INSERT INTO asset_providers_history (
                generation_run_id, archived_at,
                id, asset_id, data_source, data_source_category,
                hostname, fqdn, ip, mac, sn, os,
                manufacturer, model, vendor_category,
                data_source_fields, category_prediction, updated_at
            )
            SELECT
                %s, NOW(),
                id, asset_id, data_source, data_source_category,
                hostname, fqdn, ip, mac, sn, os,
                manufacturer, model, vendor_category,
                data_source_fields, category_prediction, updated_at
            FROM asset_providers
        """

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                run_id_str = str(generation_run_id) if generation_run_id else None

                # Archive assets
                cursor.execute(query_assets, (run_id_str,))
                assets_count = cursor.rowcount

                # Archive providers
                cursor.execute(query_providers, (run_id_str,))
                providers_count = cursor.rowcount

                # Clear both tables (CASCADE from assets will clear providers, but be explicit)
                cursor.execute("TRUNCATE assets CASCADE")

                self.conn_manager.commit_unless_scoped(conn)

                logger.info(
                    f"Archived to history: {assets_count} assets, {providers_count} providers "
                    f"(run_id: {generation_run_id})"
                )

                return {
                    'assets': assets_count,
                    'providers': providers_count
                }

        except Exception as e:
            logger.error(f"Error archiving data to history: {e}")
            raise

    def health_check(self) -> Dict[str, Any]:
        """
        Perform health check on database operations.

        Returns:
            Health check results
        """
        health = {
            'connection': False,
            'tables_accessible': False,
            'write_test': False,
            'read_test': False,
            'error': None,
        }

        try:
            # Test connection
            conn_health = self.conn_manager.health_check()
            health['connection'] = conn_health['healthy']

            if not health['connection']:
                health['error'] = conn_health.get('error')
                return health

            # Test table access
            stats = self.get_table_stats()
            health['tables_accessible'] = True
            health['table_counts'] = stats

            # Test write (to a safe location)
            test_component = f"__health_check_test_{datetime.now().timestamp()}"
            test_uuid = UUID('00000000-0000-0000-0000-000000000001')
            self.set_asset_id_mapping(test_component, test_uuid)
            health['write_test'] = True

            # Test read
            result = self.get_asset_id_mapping(test_component)
            health['read_test'] = (result == test_uuid)

            # Clean up test record to prevent accumulation
            try:
                with self.conn_manager.get_cursor() as (cursor, conn):
                    cursor.execute(
                        "DELETE FROM asset_id_mappings WHERE component = %s",
                        (test_component,)
                    )
                    conn.commit()
            except Exception as e:
                logger.warning(f"Could not clean up health check test record: {e}")

            logger.info("Database adapter health check passed")

        except Exception as e:
            health['error'] = str(e)
            logger.error(f"Database adapter health check failed: {e}")

        return health
