# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Raw Data Repository

Handles database operations for raw_source_data table.
"""

import psycopg2
from psycopg2.extras import RealDictCursor, Json
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime, timedelta
import os
import logging

from src.database.postgres_connection import get_connection_manager, PostgreSQLConnectionManager


logger = logging.getLogger(__name__)


class RawDataRepository:
    """
    Repository for raw source data operations.

    Works with the existing raw_source_data table to store
    raw records collected from integration sources.
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

    def store_record(self, batch_id: UUID, integration_id: UUID,
                    record: Dict[str, Any],
                    source_record_id: Optional[str] = None) -> int:
        """
        Store a single raw record.

        Args:
            batch_id: Batch UUID
            integration_id: Integration UUID
            record: Raw record data (will be stored as JSONB)
            source_record_id: Optional ID from source system

        Returns:
            ID of inserted record
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO raw_source_data (
                        batch_id,
                        integration_id,
                        raw_payload,
                        source_record_id,
                        ingested_at
                    )
                    VALUES (%s, %s, %s, %s, NOW())
                    RETURNING id
                """, (
                    str(batch_id),
                    str(integration_id),
                    Json(record),
                    source_record_id
                ))

                record_id = cur.fetchone()[0]
                conn.commit()

                return record_id

    def store_records_batch(self, batch_id: UUID, integration_id: UUID,
                           records: List[Dict[str, Any]]) -> int:
        """
        Store multiple records in a single transaction (bulk insert).

        Args:
            batch_id: Batch UUID
            integration_id: Integration UUID
            records: List of raw record data dictionaries

        Returns:
            Number of records inserted
        """
        if not records:
            return 0

        # Prepare values for batch insert
        values = []
        for record in records:
            # Extract source_record_id if present. Connectors that normalize
            # to a domain-specific identifier (e.g. CrowdStrike's device_id)
            # must be listed here so dedup-by-source-id can fire on re-ingest.
            source_record_id = (
                record.get('id')
                or record.get('_id')
                or record.get('source_id')
                or record.get('device_id')
            )

            values.append((
                str(batch_id),
                str(integration_id),
                Json(record),
                source_record_id
            ))

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                # Use execute_values for efficient bulk insert
                from psycopg2.extras import execute_values
                execute_values(
                    cur,
                    """
                    INSERT INTO raw_source_data (
                        batch_id,
                        integration_id,
                        raw_payload,
                        source_record_id,
                        ingested_at
                    )
                    VALUES %s
                    """,
                    values,
                    template="(%s, %s, %s, %s, NOW())"
                )

                inserted = cur.rowcount
                conn.commit()

                logger.info(f"Bulk inserted {inserted} records for batch {batch_id}")
                return inserted

    def get_records(self, batch_id: UUID, limit: int = 100,
                   offset: int = 0) -> List[Dict[str, Any]]:
        """
        Get raw records for a batch.

        Args:
            batch_id: Batch UUID
            limit: Maximum number of records to return
            offset: Number of records to skip

        Returns:
            List of raw record dictionaries
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        id,
                        source_record_id,
                        raw_payload as data,
                        ingested_at as collected_at
                    FROM raw_source_data
                    WHERE batch_id = %s
                    ORDER BY id
                    LIMIT %s OFFSET %s
                """, (str(batch_id), limit, offset))

                return [dict(row) for row in cur.fetchall()]

    def count_records(self, batch_id: UUID) -> int:
        """
        Count records for a batch.

        Args:
            batch_id: Batch UUID

        Returns:
            Number of records
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM raw_source_data WHERE batch_id = %s
                """, (str(batch_id),))

                return cur.fetchone()[0]

    def get_record_by_id(self, record_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a single raw record by ID.

        Args:
            record_id: Record ID

        Returns:
            Record dictionary or None if not found
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        id,
                        batch_id,
                        integration_id,
                        source_record_id,
                        raw_payload as data,
                        ingested_at as collected_at
                    FROM raw_source_data
                    WHERE id = %s
                """, (record_id,))

                row = cur.fetchone()
                return dict(row) if row else None

    def get_by_source_id(self, integration_id: UUID,
                        source_record_id: str) -> Optional[Dict[str, Any]]:
        """
        Get raw record by source system ID.

        Useful for checking if a record was already ingested.

        Args:
            integration_id: Integration UUID
            source_record_id: ID from source system

        Returns:
            Most recent record with this source ID, or None
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        id,
                        batch_id,
                        integration_id,
                        source_record_id,
                        raw_payload as data,
                        ingested_at as collected_at
                    FROM raw_source_data
                    WHERE integration_id = %s AND source_record_id = %s
                    ORDER BY ingested_at DESC
                    LIMIT 1
                """, (str(integration_id), source_record_id))

                row = cur.fetchone()
                return dict(row) if row else None

    def delete_batch_records(self, batch_id: UUID) -> int:
        """
        Delete all records for a batch.

        Args:
            batch_id: Batch UUID

        Returns:
            Number of records deleted
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM raw_source_data WHERE batch_id = %s
                """, (str(batch_id),))

                deleted = cur.rowcount
                conn.commit()

                if deleted > 0:
                    logger.info(f"Deleted {deleted} records for batch {batch_id}")

                return deleted

    def delete_old_records(self, integration_id: UUID, days: int) -> int:
        """
        Delete records older than specified days for an integration.

        Used for retention policy enforcement.

        Args:
            integration_id: Integration UUID
            days: Delete records older than this many days

        Returns:
            Number of records deleted
        """
        cutoff_date = datetime.now() - timedelta(days=days)

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM raw_source_data
                    WHERE integration_id = %s AND ingested_at < %s
                """, (str(integration_id), cutoff_date))

                deleted = cur.rowcount
                conn.commit()

                if deleted > 0:
                    logger.info(f"Deleted {deleted} old records for integration {integration_id}")

                return deleted

    def get_latest_records(self, integration_id: UUID, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get the most recently ingested records for an integration.

        Useful for debugging and monitoring.

        Args:
            integration_id: Integration UUID
            limit: Maximum number of records to return

        Returns:
            List of raw record dictionaries
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        id,
                        batch_id,
                        source_record_id,
                        raw_payload as data,
                        ingested_at as collected_at
                    FROM raw_source_data
                    WHERE integration_id = %s
                    ORDER BY ingested_at DESC
                    LIMIT %s
                """, (str(integration_id), limit))

                return [dict(row) for row in cur.fetchall()]

    def get_integration_stats(self, integration_id: UUID) -> Dict[str, Any]:
        """
        Get statistics about raw data for an integration.

        Args:
            integration_id: Integration UUID

        Returns:
            Dictionary with statistics
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        COUNT(*) as total_records,
                        COUNT(DISTINCT batch_id) as total_batches,
                        MIN(ingested_at) as first_ingestion,
                        MAX(ingested_at) as last_ingestion,
                        COUNT(DISTINCT source_record_id) as unique_source_ids
                    FROM raw_source_data
                    WHERE integration_id = %s
                """, (str(integration_id),))

                return dict(cur.fetchone())
