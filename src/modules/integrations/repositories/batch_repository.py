# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Batch Repository

Handles database operations for ingestion_batches table.
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime
import os
import logging

from src.database.postgres_connection import get_connection_manager, PostgreSQLConnectionManager


logger = logging.getLogger(__name__)


class BatchRepository:
    """
    Repository for batch database operations.

    Works with the existing ingestion_batches table to track
    integration execution runs.
    """

    def __init__(self, connection_manager: Optional[PostgreSQLConnectionManager] = None):
        """
        Initialize repository.

        Args:
            connection_manager: PostgreSQL connection manager (uses global if not provided)
        """
        self.conn_manager = connection_manager or get_connection_manager()

    def _get_connection(self):
        """Get database connection from pool"""
        return self.conn_manager.get_connection()

    def create_batch(self, integration_id: UUID) -> UUID:
        """
        Create a new batch for an integration run.

        Args:
            integration_id: Integration UUID

        Returns:
            UUID of created batch
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO ingestion_batches (
                        integration_id,
                        status,
                        started_at
                    )
                    VALUES (%s, 'running', NOW())
                    RETURNING id
                """, (str(integration_id),))

                batch_id = cur.fetchone()['id']
                conn.commit()

                logger.info(f"Created batch {batch_id} for integration {integration_id}")
                return UUID(batch_id)

    def get_batch(self, batch_id: UUID) -> Optional[Dict[str, Any]]:
        """
        Get batch by ID.

        Args:
            batch_id: Batch UUID

        Returns:
            Batch record as dictionary, or None if not found
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM ingestion_batches WHERE id = %s
                """, (str(batch_id),))

                row = cur.fetchone()
                return dict(row) if row else None

    def update_batch_status(self, batch_id: UUID, status: str,
                           error_message: Optional[str] = None) -> bool:
        """
        Update batch status.

        Args:
            batch_id: Batch UUID
            status: New status ('running', 'completed', 'failed', 'partial')
            error_message: Error message if failed

        Returns:
            True if updated, False if not found
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE ingestion_batches
                    SET status = %s,
                        error_message = %s,
                        completed_at = CASE WHEN %s IN ('completed', 'failed', 'partial')
                                           THEN NOW()
                                           ELSE completed_at
                                      END
                    WHERE id = %s
                """, (status, error_message, status, str(batch_id)))

                updated = cur.rowcount > 0
                conn.commit()

                if updated:
                    logger.info(f"Updated batch {batch_id} status to {status}")

                return updated

    def increment_raw_count(self, batch_id: UUID, count: int = 1) -> bool:
        """
        Increment raw records count for a batch.

        Args:
            batch_id: Batch UUID
            count: Number to increment by (default: 1)

        Returns:
            True if updated, False if not found
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE ingestion_batches
                    SET raw_records_count = raw_records_count + %s
                    WHERE id = %s
                """, (count, str(batch_id)))

                updated = cur.rowcount > 0
                conn.commit()

                return updated

    def update_mapped_count(self, batch_id: UUID, count: int) -> bool:
        """
        Update mapped records count for a batch.

        Args:
            batch_id: Batch UUID
            count: Mapped records count

        Returns:
            True if updated, False if not found
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE ingestion_batches
                    SET mapped_records_count = %s,
                        mapping_started_at = COALESCE(mapping_started_at, NOW())
                    WHERE id = %s
                """, (count, str(batch_id)))

                updated = cur.rowcount > 0
                conn.commit()

                if updated:
                    logger.info(f"Updated batch {batch_id} mapped count to {count}")

                return updated

    def update_mapping_hash(self, batch_id: UUID, mapping_hash: str) -> bool:
        """
        Store the mapping config hash on a batch record.

        Args:
            batch_id: Batch UUID
            mapping_hash: SHA-256 hash prefix (16 chars) of the field_mapping config

        Returns:
            True if updated, False if not found
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE ingestion_batches
                    SET mapping_hash = %s
                    WHERE id = %s
                """, (mapping_hash, str(batch_id)))

                updated = cur.rowcount > 0
                conn.commit()

                return updated

    def complete_batch(self, batch_id: UUID, record_count: int) -> bool:
        """
        Mark batch as completed with final record count.

        Args:
            batch_id: Batch UUID
            record_count: Total number of records collected

        Returns:
            True if updated, False if not found
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE ingestion_batches
                    SET status = 'completed',
                        raw_records_count = %s,
                        completed_at = NOW()
                    WHERE id = %s
                """, (record_count, str(batch_id)))

                updated = cur.rowcount > 0
                conn.commit()

                if updated:
                    logger.info(f"Completed batch {batch_id} with {record_count} records")

                return updated

    def fail_batch(self, batch_id: UUID, error_message: str) -> bool:
        """
        Mark batch as failed with error message.

        Args:
            batch_id: Batch UUID
            error_message: Error description

        Returns:
            True if updated, False if not found
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE ingestion_batches
                    SET status = 'failed',
                        error_message = %s,
                        completed_at = NOW()
                    WHERE id = %s
                """, (error_message, str(batch_id)))

                updated = cur.rowcount > 0
                conn.commit()

                if updated:
                    logger.error(f"Failed batch {batch_id}: {error_message}")

                return updated

    def list_batches(self, integration_id: UUID, limit: int = 50,
                     offset: int = 0, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List batches for an integration.

        Args:
            integration_id: Integration UUID
            limit: Maximum number of batches to return
            offset: Number of batches to skip
            status: Filter by status (optional)

        Returns:
            List of batch records
        """
        query = """
            SELECT * FROM ingestion_batches
            WHERE integration_id = %s
        """
        params = [str(integration_id)]

        if status:
            query += " AND status = %s"
            params.append(status)

        query += " ORDER BY started_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, params)
                return [dict(row) for row in cur.fetchall()]

    def count_batches(self, integration_id: UUID, status: Optional[str] = None) -> int:
        """
        Count batches for an integration.

        Args:
            integration_id: Integration UUID
            status: Filter by status (optional)

        Returns:
            Number of batches
        """
        query = "SELECT COUNT(*) FROM ingestion_batches WHERE integration_id = %s"
        params = [str(integration_id)]

        if status:
            query += " AND status = %s"
            params.append(status)

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return cur.fetchone()[0]

    def get_latest_batches_for_all_integrations(self) -> Dict[str, Dict[str, Any]]:
        """
        Get the latest batch for ALL integrations in a single query.

        This eliminates the N+1 query problem when listing integrations.
        Uses a window function to efficiently find the most recent batch per integration.

        Returns:
            Dictionary mapping integration_id (as string) to latest batch info:
            {
                'integration_id_1': {
                    'raw_records_count': 100,
                    'started_at': datetime object,
                    'status': 'completed',
                    'assets_created_count': 50,
                    'last_sync_at': datetime object,  # started_at of latest
                                                      # pipeline-processed
                                                      # batch; may point at an
                                                      # older batch than `status`
                },
                ...
            }
        """
        query = """
            WITH latest_batches AS (
                SELECT
                    integration_id,
                    raw_records_count,
                    started_at,
                    status,
                    assets_created_count,
                    ROW_NUMBER() OVER (PARTITION BY integration_id ORDER BY started_at DESC) as rn
                FROM ingestion_batches
            ),
            last_pipeline_sync AS (
                SELECT
                    integration_id,
                    MAX(started_at) AS last_sync_at
                FROM ingestion_batches
                WHERE asset_generation_status = 'completed'
                GROUP BY integration_id
            ),
            last_ingestion_run AS (
                SELECT
                    integration_id,
                    MAX(started_at) AS last_run_at
                FROM ingestion_batches
                WHERE status = 'completed'
                GROUP BY integration_id
            )
            SELECT
                lb.integration_id,
                lb.raw_records_count,
                lb.started_at,
                lb.status,
                lb.assets_created_count,
                lps.last_sync_at,
                lir.last_run_at
            FROM latest_batches lb
            LEFT JOIN last_pipeline_sync lps USING (integration_id)
            LEFT JOIN last_ingestion_run lir USING (integration_id)
            WHERE lb.rn = 1
        """

        result = {}
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query)
                for row in cur.fetchall():
                    integration_id = str(row['integration_id'])
                    result[integration_id] = {
                        'raw_records_count': row['raw_records_count'] or 0,
                        'started_at': row['started_at'],
                        'status': row['status'],
                        'assets_created_count': row['assets_created_count'] or 0,
                        'last_sync_at': row['last_sync_at'],
                        'last_run_at': row['last_run_at'],
                    }

        return result

    def get_latest_sync_and_run_for_integration(
        self, integration_id: UUID,
    ) -> Dict[str, Any]:
        """
        Two timestamp scalars for one integration. Use for detail-page
        enrichment instead of get_latest_batches_for_all_integrations,
        which scans every integration's batches.

        Both partial indexes on ingestion_batches.started_at filtered by
        status='completed' and asset_generation_status='completed' make
        each MAX a single index lookup per integration.

        Returns:
            {'last_sync_at': datetime|None, 'last_run_at': datetime|None}
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        MAX(started_at) FILTER (
                            WHERE asset_generation_status = 'completed'
                        ) AS last_sync_at,
                        MAX(started_at) FILTER (
                            WHERE status = 'completed'
                        ) AS last_run_at
                    FROM ingestion_batches
                    WHERE integration_id = %s
                """, (str(integration_id),))
                row = cur.fetchone()
                if not row:
                    return {'last_sync_at': None, 'last_run_at': None}
                return {
                    'last_sync_at': row['last_sync_at'],
                    'last_run_at': row['last_run_at'],
                }

    def get_latest_completed_batch(self, integration_id: UUID) -> Optional[Dict[str, Any]]:
        """Get the most recent completed batch for an integration."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, integration_id, status, started_at, completed_at,
                           raw_records_count, mapped_records_count
                    FROM ingestion_batches
                    WHERE integration_id = %s AND status = 'completed'
                    ORDER BY completed_at DESC
                    LIMIT 1
                """, (str(integration_id),))
                row = cur.fetchone()
                return dict(row) if row else None

    def get_batch_stats(self, integration_id: UUID) -> Dict[str, Any]:
        """
        Get statistics for batches of an integration.

        Args:
            integration_id: Integration UUID

        Returns:
            Dictionary with statistics
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Get counts by status and record aggregations
                cur.execute("""
                    SELECT
                        COUNT(*) as total_batches,
                        COUNT(*) FILTER (WHERE status = 'completed') as completed,
                        COUNT(*) FILTER (WHERE status = 'failed') as failed,
                        COUNT(*) FILTER (WHERE status = 'running') as running,
                        COUNT(*) FILTER (WHERE status = 'partial') as partial,
                        COALESCE(SUM(raw_records_count), 0) as total_records,
                        COALESCE(SUM(mapped_records_count), 0) as total_mapped,
                        COALESCE(SUM(assets_created_count), 0) as total_assets_created,
                        COALESCE(SUM(assets_updated_count), 0) as total_assets_updated,
                        AVG(raw_records_count) FILTER (WHERE status = 'completed') as avg_records_per_batch
                    FROM ingestion_batches
                    WHERE integration_id = %s
                """, (str(integration_id),))

                stats = dict(cur.fetchone())

                # Get last successful batch
                cur.execute("""
                    SELECT id, completed_at, raw_records_count
                    FROM ingestion_batches
                    WHERE integration_id = %s AND status = 'completed'
                    ORDER BY completed_at DESC
                    LIMIT 1
                """, (str(integration_id),))

                last_batch = cur.fetchone()
                if last_batch:
                    stats['last_successful_batch'] = dict(last_batch)
                else:
                    stats['last_successful_batch'] = None

                # Calculate success rate
                total = stats['total_batches'] or 0
                completed = stats['completed'] or 0
                stats['success_rate'] = (completed / total * 100) if total > 0 else 0

                return stats

    def get_global_batch_stats(self) -> Dict[str, Any]:
        """
        Get global statistics for all batches across all integrations.

        Returns:
            Dictionary with global statistics
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Get counts by status and record aggregations (no integration_id filter)
                cur.execute("""
                    SELECT
                        COUNT(*) as total,
                        COUNT(*) FILTER (WHERE status = 'completed') as completed,
                        COUNT(*) FILTER (WHERE status = 'failed') as failed,
                        COUNT(*) FILTER (WHERE status = 'running') as processing,
                        COUNT(*) FILTER (WHERE status = 'partial') as partial,
                        COALESCE(SUM(raw_records_count), 0) as total_records,
                        COALESCE(SUM(mapped_records_count), 0) as total_mapped,
                        COALESCE(SUM(assets_created_count), 0) as total_assets_created,
                        COALESCE(SUM(assets_updated_count), 0) as total_assets_updated
                    FROM ingestion_batches
                """)

                stats = dict(cur.fetchone())

                # Get last successful batch (global)
                cur.execute("""
                    SELECT id, integration_id, completed_at, raw_records_count
                    FROM ingestion_batches
                    WHERE status = 'completed'
                    ORDER BY completed_at DESC
                    LIMIT 1
                """)

                last_batch = cur.fetchone()
                if last_batch:
                    stats['last_successful_batch'] = dict(last_batch)
                else:
                    stats['last_successful_batch'] = None

                # Calculate success rate
                total = stats['total'] or 0
                completed = stats['completed'] or 0
                stats['success_rate'] = (completed / total * 100) if total > 0 else 0

                return stats

    def delete_batch(self, batch_id: UUID) -> bool:
        """
        Delete a batch and all associated records (CASCADE).

        Args:
            batch_id: Batch UUID

        Returns:
            True if deleted, False if not found
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM ingestion_batches WHERE id = %s
                """, (str(batch_id),))

                deleted = cur.rowcount > 0
                conn.commit()

                if deleted:
                    logger.info(f"Deleted batch {batch_id}")

                return deleted
