# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Integration Repository

Handles database operations for integrations table.
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from typing import List, Optional, Dict, Any
from uuid import UUID
import os
import logging
from contextlib import contextmanager

from src.modules.integrations.models import Integration, IntegrationStatus
from src.modules.integrations.shared import encrypt, decrypt, is_encrypted
from src.database.postgres_connection import get_connection_manager


logger = logging.getLogger(__name__)


class IntegrationRepository:
    """
    Repository for integration database operations.

    Handles CRUD operations and credential encryption/decryption.
    """

    # Core asset fields used for insight calculations
    CORE_FIELDS = ['hostname', 'ip', 'mac', 'sn', 'os', 'manufacturer', 'model']

    # Fields that contain sensitive data (should be encrypted).
    # Adding a new entry is backward-compatible: _encrypt_config opportunistically
    # encrypts plaintext on the next save (gated by is_encrypted()), and
    # _decrypt_config passes plaintext through unchanged for the same reason.
    SENSITIVE_FIELDS = [
        'password', 'client_secret', 'api_key', 'api_token', 'token',
        'secret', 'private_key', 'shared_key', 'hec_token',
        'bearer_token',
    ]

    def __init__(self, db_url: Optional[str] = None, connection_manager=None):
        """
        Initialize repository.

        Args:
            db_url: Database connection string (defaults to DATABASE_URL env var)
            connection_manager: Optional connection manager instance (uses global pool if not provided)
        """
        self.db_url = db_url or os.getenv('DATABASE_URL')
        if not self.db_url:
            raise ValueError("DATABASE_URL not set")

        # Use provided connection manager or get the global one
        self._connection_manager = connection_manager or get_connection_manager(self.db_url)

    @contextmanager
    def _get_connection(self):
        """Get database connection from pool"""
        with self._connection_manager.get_connection() as conn:
            yield conn

    def _encrypt_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Encrypt sensitive fields in config.

        Args:
            config: Configuration dictionary

        Returns:
            Config with encrypted sensitive fields
        """
        encrypted_config = config.copy()

        for field in self.SENSITIVE_FIELDS:
            if field in encrypted_config:
                value = encrypted_config[field]
                # Only encrypt if not already encrypted
                if value and not is_encrypted(value):
                    encrypted_config[field] = encrypt(value)

        return encrypted_config

    def _decrypt_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Decrypt sensitive fields in config.

        Args:
            config: Configuration dictionary with encrypted fields

        Returns:
            Config with decrypted sensitive fields
        """
        decrypted_config = config.copy()

        for field in self.SENSITIVE_FIELDS:
            if field in decrypted_config:
                value = decrypted_config[field]
                # Only decrypt if encrypted
                if value and is_encrypted(value):
                    decrypted_config[field] = decrypt(value)

        return decrypted_config

    def create(self, integration: Integration) -> Integration:
        """
        Create a new integration.

        Args:
            integration: Integration to create

        Returns:
            Created integration with ID

        Raises:
            psycopg2.IntegrityError: If name already exists
        """
        # Encrypt sensitive config fields before saving
        encrypted_config = self._encrypt_config(integration.config)

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO integrations (
                        name, integration_type, category,
                        status, config, field_mapping, transformations, data_filters, preserve_fields, logo, description,
                        last_connection_test_at, last_connection_test_status, last_connection_test_message,
                        group_id, source_method
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                """, (
                    integration.name,
                    integration.integration_type,
                    integration.category,
                    integration.status.value,
                    psycopg2.extras.Json(encrypted_config),
                    psycopg2.extras.Json(integration.field_mapping),
                    psycopg2.extras.Json(integration.transformations or {}),
                    psycopg2.extras.Json(integration.data_filters or []),
                    psycopg2.extras.Json(integration.preserve_fields or []),
                    integration.logo,
                    integration.description,
                    integration.last_connection_test_at,
                    integration.last_connection_test_status,
                    integration.last_connection_test_message,
                    str(integration.group_id) if integration.group_id else None,
                    integration.source_method
                ))

                row = dict(cur.fetchone())
                conn.commit()

                logger.info(f"Created integration: {integration.name} (ID: {row['id']})")
                return Integration.from_db_row(row)

    def get_by_id(self, integration_id: UUID, decrypt: bool = False) -> Optional[Integration]:
        """
        Get integration by ID.

        Args:
            integration_id: Integration UUID
            decrypt: Whether to decrypt sensitive config fields

        Returns:
            Integration if found, None otherwise
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT i.*, ig.name AS group_name
                    FROM integrations i
                    LEFT JOIN integration_groups ig ON i.group_id = ig.id
                    WHERE i.id = %s
                """, (str(integration_id),))

                row = cur.fetchone()
                if not row:
                    return None

                row = dict(row)

                # Decrypt if requested
                if decrypt:
                    row['config'] = self._decrypt_config(row['config'])

                return Integration.from_db_row(row)

    def get_by_name(self, name: str, decrypt: bool = False) -> Optional[Integration]:
        """
        Get integration by name.

        Args:
            name: Integration name
            decrypt: Whether to decrypt sensitive config fields

        Returns:
            Integration if found, None otherwise
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT i.*, ig.name AS group_name
                    FROM integrations i
                    LEFT JOIN integration_groups ig ON i.group_id = ig.id
                    WHERE i.name = %s
                """, (name,))

                row = cur.fetchone()
                if not row:
                    return None

                row = dict(row)

                # Decrypt if requested
                if decrypt:
                    row['config'] = self._decrypt_config(row['config'])

                return Integration.from_db_row(row)

    def list_all(self, status: Optional[IntegrationStatus] = None,
                 integration_type: Optional[str] = None,
                 decrypt: bool = False) -> List[Integration]:
        """
        List all integrations with optional filtering.

        Args:
            status: Filter by status
            integration_type: Filter by integration type
            decrypt: Whether to decrypt sensitive config fields

        Returns:
            List of integrations
        """
        query = """SELECT i.*, ig.name AS group_name
            FROM integrations i
            LEFT JOIN integration_groups ig ON i.group_id = ig.id
            WHERE 1=1"""
        params = []

        if status:
            query += " AND i.status = %s"
            params.append(status.value)

        if integration_type:
            query += " AND i.integration_type = %s"
            params.append(integration_type)

        query += " ORDER BY i.created_at DESC"

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, params)

                integrations = []
                for row in cur.fetchall():
                    row = dict(row)

                    # Decrypt if requested
                    if decrypt:
                        row['config'] = self._decrypt_config(row['config'])

                    integrations.append(Integration.from_db_row(row))

                return integrations

    def update(self, integration_id: UUID, updates: Dict[str, Any]) -> Optional[Integration]:
        """
        Update integration fields.

        Args:
            integration_id: Integration UUID
            updates: Dictionary of fields to update

        Returns:
            Updated integration if found, None otherwise
        """
        # Build SET clause dynamically
        set_parts = []
        params = []

        # Special handling for config - encrypt sensitive fields
        if 'config' in updates:
            updates['config'] = self._encrypt_config(updates['config'])

        for key, value in updates.items():
            if key in ['id', 'created_at', 'updated_at']:
                continue  # Skip immutable fields

            set_parts.append(f"{key} = %s")

            # Handle JSONB fields
            if key in ['config', 'field_mapping', 'transformations', 'data_filters', 'preserve_fields']:
                params.append(psycopg2.extras.Json(value))
            elif key == 'status' and isinstance(value, IntegrationStatus):
                params.append(value.value)
            else:
                params.append(value)

        if not set_parts:
            # Nothing to update
            return self.get_by_id(integration_id)

        query = f"""
            UPDATE integrations
            SET {', '.join(set_parts)}
            WHERE id = %s
            RETURNING *
        """
        params.append(str(integration_id))

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, params)
                row = cur.fetchone()

                if not row:
                    return None

                conn.commit()
                logger.info(f"Updated integration ID: {integration_id}")
                return Integration.from_db_row(dict(row))

    def delete(self, integration_id: UUID) -> bool:
        """
        Delete integration and all dependent records.

        This method deletes in the following order to respect foreign key constraints:
        1. raw_asset_data (references ingestion_batches)
        2. ingestion_batches (references integration)
        3. raw_source_data (references integration)
        4. Update asset_providers to set integration_id to NULL
        5. integration record

        Args:
            integration_id: Integration UUID

        Returns:
            True if deleted, False if not found
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                # First, check if integration exists
                cur.execute("""
                    SELECT id FROM integrations WHERE id = %s
                """, (str(integration_id),))

                if not cur.fetchone():
                    return False

                # Delete raw_asset_data first (has FK to ingestion_batches)
                cur.execute("""
                    DELETE FROM raw_asset_data
                    WHERE batch_id IN (
                        SELECT id FROM ingestion_batches
                        WHERE integration_id = %s
                    )
                """, (str(integration_id),))
                raw_deleted = cur.rowcount
                logger.info(f"Deleted {raw_deleted} raw_asset_data records for integration {integration_id}")

                # Delete ingestion_batches
                cur.execute("""
                    DELETE FROM ingestion_batches WHERE integration_id = %s
                """, (str(integration_id),))
                batches_deleted = cur.rowcount
                logger.info(f"Deleted {batches_deleted} batches for integration {integration_id}")

                # Delete raw_source_data
                cur.execute("""
                    DELETE FROM raw_source_data WHERE integration_id = %s
                """, (str(integration_id),))
                source_deleted = cur.rowcount
                logger.info(f"Deleted {source_deleted} raw_source_data records for integration {integration_id}")

                # Update asset_providers to set integration_id to NULL (FK has ON DELETE SET NULL)
                # This happens automatically, but we log it
                cur.execute("""
                    SELECT COUNT(*) FROM asset_providers WHERE integration_id = %s
                """, (str(integration_id),))
                provider_count = cur.fetchone()[0]
                if provider_count > 0:
                    logger.info(f"Will set integration_id to NULL for {provider_count} asset_providers")

                # Finally, delete the integration
                cur.execute("""
                    DELETE FROM integrations WHERE id = %s
                """, (str(integration_id),))

                conn.commit()
                logger.info(f"Successfully deleted integration {integration_id}")

                return True

    def update_sync_status(self, integration_id: UUID, status: str,
                          error: Optional[str] = None) -> bool:
        """
        Update sync status after a run.

        Args:
            integration_id: Integration UUID
            status: Sync status ('success', 'failed', etc.)
            error: Error message if failed

        Returns:
            True if updated, False if not found
        """
        # last_sync_at is no longer read (issue #441 moved the UI source of
        # truth to ingestion_batches.asset_generation_completed_at). The write
        # remains for any external consumers of the column; remove when safe.
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE integrations
                    SET last_sync_at = NOW(),
                        last_sync_status = %s,
                        last_error = %s,
                        sync_count = sync_count + 1
                    WHERE id = %s
                """, (status, error, str(integration_id)))

                updated = cur.rowcount > 0
                conn.commit()

                return updated

    def set_status(self, integration_id: UUID, status: IntegrationStatus) -> bool:
        """
        Set integration status.

        Args:
            integration_id: Integration UUID
            status: New status

        Returns:
            True if updated, False if not found
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE integrations
                    SET status = %s
                    WHERE id = %s
                """, (status.value, str(integration_id)))

                updated = cur.rowcount > 0
                conn.commit()

                if updated:
                    logger.info(f"Set integration {integration_id} status to {status.value}")

                return updated

    def get_enabled_integrations(self) -> List[Integration]:
        """
        Get all enabled integrations.

        Returns:
            List of enabled integrations
        """
        return self.list_all(status=IntegrationStatus.ENABLED)

    def count(self, status: Optional[IntegrationStatus] = None) -> int:
        """
        Count integrations.

        Args:
            status: Filter by status

        Returns:
            Count of integrations
        """
        query = "SELECT COUNT(*) FROM integrations WHERE 1=1"
        params = []

        if status:
            query += " AND status = %s"
            params.append(status.value)

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return cur.fetchone()[0]

    # ── Insight methods ────────────────────────────────────────────────

    def get_stale_assets_count(self, data_source, threshold_days=30):
        """
        Count assets from this integration not seen in threshold_days.

        Args:
            data_source: Integration name as stored in asset_providers.data_source
            threshold_days: Days after which an asset is considered stale

        Returns:
            Dict with stale_count and total_count
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        COUNT(*) FILTER (
                            WHERE a.last_time
                                < NOW() - make_interval(days => %s)
                            OR a.last_time IS NULL
                        ) AS stale_count,
                        COUNT(*) AS total_count
                    FROM asset_providers ap
                    JOIN assets a ON ap.asset_id = a.id
                    WHERE ap.data_source = %s
                """, (threshold_days, data_source))
                row = cur.fetchone()
                return dict(row) if row else {
                    'stale_count': 0, 'total_count': 0
                }

    def get_attribute_coverage(self, data_source):
        """
        Compare field completeness for core fields: this source vs global.

        Args:
            data_source: Integration name as stored in asset_providers.data_source

        Returns:
            Dict mapping field_name -> {source_coverage, global_coverage, gap}
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        COUNT(*) FILTER (WHERE ap.data_source = %s)
                            AS source_total,
                        COUNT(*) AS global_total,

                        COUNT(*) FILTER (
                            WHERE ap.data_source = %s
                            AND ap.hostname IS NOT NULL
                            AND ap.hostname != ''
                        ) AS source_hostname,
                        COUNT(*) FILTER (
                            WHERE ap.hostname IS NOT NULL
                            AND ap.hostname != ''
                        ) AS global_hostname,

                        COUNT(*) FILTER (
                            WHERE ap.data_source = %s
                            AND ap.ip IS NOT NULL
                        ) AS source_ip,
                        COUNT(*) FILTER (
                            WHERE ap.ip IS NOT NULL
                        ) AS global_ip,

                        COUNT(*) FILTER (
                            WHERE ap.data_source = %s
                            AND ap.mac IS NOT NULL
                        ) AS source_mac,
                        COUNT(*) FILTER (
                            WHERE ap.mac IS NOT NULL
                        ) AS global_mac,

                        COUNT(*) FILTER (
                            WHERE ap.data_source = %s
                            AND ap.sn IS NOT NULL
                            AND ap.sn != ''
                        ) AS source_sn,
                        COUNT(*) FILTER (
                            WHERE ap.sn IS NOT NULL
                            AND ap.sn != ''
                        ) AS global_sn,

                        COUNT(*) FILTER (
                            WHERE ap.data_source = %s
                            AND ap.os IS NOT NULL
                            AND ap.os != ''
                        ) AS source_os,
                        COUNT(*) FILTER (
                            WHERE ap.os IS NOT NULL
                            AND ap.os != ''
                        ) AS global_os,

                        COUNT(*) FILTER (
                            WHERE ap.data_source = %s
                            AND ap.manufacturer IS NOT NULL
                            AND ap.manufacturer != ''
                        ) AS source_manufacturer,
                        COUNT(*) FILTER (
                            WHERE ap.manufacturer IS NOT NULL
                            AND ap.manufacturer != ''
                        ) AS global_manufacturer,

                        COUNT(*) FILTER (
                            WHERE ap.data_source = %s
                            AND ap.model IS NOT NULL
                            AND ap.model != ''
                        ) AS source_model,
                        COUNT(*) FILTER (
                            WHERE ap.model IS NOT NULL
                            AND ap.model != ''
                        ) AS global_model
                    FROM asset_providers ap
                """, (
                    data_source,  # source_total
                    data_source,  # source_hostname
                    data_source,  # source_ip
                    data_source,  # source_mac
                    data_source,  # source_sn
                    data_source,  # source_os
                    data_source,  # source_manufacturer
                    data_source,  # source_model
                ))
                row = cur.fetchone()
                if not row:
                    return {}

                row = dict(row)
                source_total = row['source_total'] or 0
                global_total = row['global_total'] or 0

                result = {}
                for field in self.CORE_FIELDS:
                    src_count = row.get(f'source_{field}', 0) or 0
                    glb_count = row.get(f'global_{field}', 0) or 0

                    src_pct = round(
                        src_count / source_total * 100, 1
                    ) if source_total > 0 else 0.0
                    glb_pct = round(
                        glb_count / global_total * 100, 1
                    ) if global_total > 0 else 0.0

                    result[field] = {
                        'source_coverage': src_pct,
                        'global_coverage': glb_pct,
                        'gap': round(glb_pct - src_pct, 1),
                    }

                return result

    def get_attribute_conflicts(self, data_source):
        """
        Count fields where this source disagrees with the resolved asset value.

        Compares asset_providers fields against their resolved assets fields
        for all assets linked to this integration.

        Args:
            data_source: Integration name as stored in asset_providers.data_source

        Returns:
            Dict with total_conflicts and by_field breakdown
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        COUNT(*) FILTER (
                            WHERE ap.hostname IS NOT NULL
                            AND a.hostname IS NOT NULL
                            AND NOT ap.hostname::text = ANY(a.hostname)
                        ) AS hostname,
                        COUNT(*) FILTER (
                            WHERE ap.ip IS NOT NULL
                            AND a.ip IS NOT NULL
                            AND NOT ap.ip::text = ANY(a.ip)
                        ) AS ip,
                        COUNT(*) FILTER (
                            WHERE ap.mac IS NOT NULL
                            AND a.mac IS NOT NULL
                            AND NOT ap.mac::text = ANY(a.mac)
                        ) AS mac,
                        COUNT(*) FILTER (
                            WHERE ap.sn IS NOT NULL
                            AND a.sn IS NOT NULL
                            AND NOT ap.sn::text = ANY(a.sn)
                        ) AS sn,
                        COUNT(*) FILTER (
                            WHERE ap.manufacturer IS NOT NULL
                            AND a.manufacturer IS NOT NULL
                            AND NOT ap.manufacturer::text = ANY(a.manufacturer)
                        ) AS manufacturer,
                        COUNT(*) FILTER (
                            WHERE ap.model IS NOT NULL
                            AND a.model IS NOT NULL
                            AND NOT ap.model::text = ANY(a.model)
                        ) AS model
                    FROM asset_providers ap
                    JOIN assets a ON ap.asset_id = a.id
                    WHERE ap.data_source = %s
                """, (data_source,))
                row = cur.fetchone()
                if not row:
                    return {'total_conflicts': 0, 'by_field': {}}

                row = dict(row)
                by_field = {}
                total = 0
                # os excluded: raw ap.os vs normalized a.os_normalized always differs
                conflict_fields = [f for f in self.CORE_FIELDS if f != 'os']
                for field in conflict_fields:
                    count = row.get(field, 0) or 0
                    if count > 0:
                        by_field[field] = count
                    total += count

                return {
                    'total_conflicts': total,
                    'by_field': by_field,
                }

    def get_category_coverage(self, data_source):
        """
        Per-category coverage for a single source.

        Returns every category the source reports at least one asset for.
        Callers apply any share-threshold filtering they need.

        Args:
            data_source: Integration name as stored in asset_providers.data_source

        Returns:
            List of dicts with category, source_count, total_count,
            coverage_pct, estimated_missing
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        a.category,
                        COUNT(DISTINCT CASE
                            WHEN ap.data_source = %s THEN a.id
                        END) AS source_count,
                        COUNT(DISTINCT a.id) AS total_count
                    FROM assets a
                    LEFT JOIN asset_providers ap ON ap.asset_id = a.id
                    WHERE a.category IS NOT NULL
                    GROUP BY a.category
                    HAVING COUNT(DISTINCT CASE
                        WHEN ap.data_source = %s THEN a.id
                    END) > 0
                    ORDER BY COUNT(DISTINCT a.id) DESC
                """, (data_source, data_source))
                rows = cur.fetchall()

                result = []
                for row in rows:
                    row = dict(row)
                    source_count = row['source_count'] or 0
                    total_count = row['total_count'] or 0

                    if total_count == 0:
                        continue

                    coverage_pct = round(
                        source_count / total_count * 100, 1
                    )

                    result.append({
                        'category': row['category'],
                        'source_count': source_count,
                        'total_count': total_count,
                        'coverage_pct': coverage_pct,
                        'estimated_missing': total_count - source_count,
                    })

                return result

    def get_confidence_distribution(self, data_source, medium: float = None, high: float = None):
        """
        Count assets reported by this source bucketed by confidence.

        Bucket boundaries are driven by the configured confidence thresholds:
        high (>= high), medium (>= medium and < high), low (< medium).

        Args:
            data_source: Integration name as stored in
                asset_providers.data_source
            medium: Lower threshold for the medium bucket (defaults to
                DEFAULT_CONFIDENCE_THRESHOLDS[0] = 0.45)
            high: Lower threshold for the high bucket (defaults to
                DEFAULT_CONFIDENCE_THRESHOLDS[1] = 0.70)

        Returns:
            Dict with high, medium, low counts (zero for any missing bucket).
        """
        if medium is None or high is None:
            from src.api.modules.settings.settings_schema import DEFAULT_CONFIDENCE_THRESHOLDS
            medium, high = DEFAULT_CONFIDENCE_THRESHOLDS
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        COUNT(DISTINCT a.id) FILTER (
                            WHERE a.confidence >= %(cl_high)s
                        ) AS high,
                        COUNT(DISTINCT a.id) FILTER (
                            WHERE a.confidence >= %(cl_med)s
                            AND a.confidence < %(cl_high)s
                        ) AS medium,
                        COUNT(DISTINCT a.id) FILTER (
                            WHERE a.confidence < %(cl_med)s
                            OR a.confidence IS NULL
                        ) AS low
                    FROM assets a
                    JOIN asset_providers ap ON ap.asset_id = a.id
                    WHERE ap.data_source = %(data_source)s
                """, {'cl_high': high, 'cl_med': medium, 'data_source': data_source})
                row = cur.fetchone()
                if not row:
                    return {'high': 0, 'medium': 0, 'low': 0}
                row = dict(row)
                return {
                    'high': row.get('high') or 0,
                    'medium': row.get('medium') or 0,
                    'low': row.get('low') or 0,
                }

    def get_asset_timeline(self, data_source, days=30):
        """
        Daily New / Returned / Departed counts for one data source, net-distinct
        per day, from the asset_chronicle presence events.

        Mirrors the dashboard timeline (dashboard_repository.get_asset_timeline)
        but scoped to a single source. An asset is counted on a day only when
        its end-of-day presence differs from its start-of-day presence, so
        multiple runs/day don't inflate and same-day round-trips cancel.
        New = asset_created, Returned = asset_readded, Departed = asset_removed
        EXCLUDING pipeline_merge (identity consolidations are not departures).
        Classification runs in Python (timeline_classifier) so it's unit-testable.

        Source attribution — the membership set is the union of the live
        asset_providers table and asset_providers_history: a genuinely departed
        asset no longer has a live provider row (grace-period logic moves it to
        history when asset_removed fires), so history is required to attribute
        departures at all. An asset seen via several sources is therefore
        counted under each source it has ever belonged to (multi-source
        attribution nuance — accepted so the Departed series is not empty).

        Args:
            data_source: Integration name as stored in asset_providers.data_source
            days: Number of days to look back

        Returns:
            List of {label, added, returned, removed} dicts, oldest-first.
        """
        from src.modules.dashboard.services.timeline_classifier import (
            classify_net_timeline,
        )

        series_sql = """
            SELECT TO_CHAR(d, 'Mon DD') AS label, d::date AS day
            FROM generate_series(
                CURRENT_DATE - make_interval(days => %(days)s - 1),
                CURRENT_DATE,
                '1 day'::interval
            ) d
            ORDER BY d
        """

        # One row per (asset, day-with-a-presence-event) for member assets
        # active in the window, PLUS a baseline row per asset
        # (day = window_start - 1) carrying its last pre-window state so day-1
        # transitions are seeded. `members` scopes the chronicle to assets ever
        # attributed to this source (live providers ∪ archived history).
        state_sql = """
            WITH members AS (
                SELECT asset_id FROM asset_providers          WHERE data_source = %(ds)s
                UNION
                SELECT asset_id FROM asset_providers_history  WHERE data_source = %(ds)s
            ),
            pe AS (
                SELECT c.asset_id, c.time,
                       (c.event_type IN ('asset_created','asset_readded')) AS is_enter,
                       c.event_type
                FROM asset_chronicle c
                JOIN members m ON m.asset_id = c.asset_id
                WHERE c.event_type IN ('asset_created','asset_readded','asset_removed')
                  AND (c.event_type <> 'asset_removed'
                       OR c.causation IS DISTINCT FROM 'pipeline_merge')
                  AND c.time < CURRENT_DATE + 1
            ),
            windowed AS (
                SELECT DISTINCT asset_id FROM pe
                WHERE time >= CURRENT_DATE - make_interval(days => %(days)s - 1)
            ),
            ev AS (
                SELECT p.asset_id, p.time::date AS day, p.time,
                       p.is_enter, p.event_type
                FROM pe p JOIN windowed w USING (asset_id)
                WHERE p.time >= CURRENT_DATE - make_interval(days => %(days)s - 1)
                UNION ALL
                SELECT b.asset_id,
                       (CURRENT_DATE - make_interval(days => %(days)s - 1))::date - 1 AS day,
                       b.time, b.is_enter, b.event_type
                FROM (
                    SELECT DISTINCT ON (p.asset_id)
                           p.asset_id, p.time, p.is_enter, p.event_type
                    FROM pe p JOIN windowed w USING (asset_id)
                    WHERE p.time < CURRENT_DATE - make_interval(days => %(days)s - 1)
                    ORDER BY p.asset_id, p.time DESC
                ) b
            )
            SELECT asset_id, day, end_enter, last_evt
            FROM (
                SELECT DISTINCT ON (asset_id, day)
                       asset_id, day,
                       is_enter AS end_enter, event_type AS last_evt
                FROM ev
                ORDER BY asset_id, day, time DESC
            ) d
            ORDER BY asset_id, day
        """

        try:
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(series_sql, {'days': days})
                    series = [dict(r) for r in cur.fetchall()]
                    if not series:
                        return []
                    cur.execute(state_sql, {'ds': data_source, 'days': days})
                    state_rows = [dict(r) for r in cur.fetchall()]

            window_start = series[0]['day']
            buckets = classify_net_timeline(state_rows, window_start)
            zero = {'added': 0, 'returned': 0, 'removed': 0}
            return [
                {
                    'label': row['label'],
                    'added': buckets.get(row['day'], zero)['added'],
                    'returned': buckets.get(row['day'], zero)['returned'],
                    'removed': buckets.get(row['day'], zero)['removed'],
                }
                for row in series
            ]
        except Exception as e:
            logger.warning(f"Failed to get asset timeline for {data_source}: {e}")
            return []
