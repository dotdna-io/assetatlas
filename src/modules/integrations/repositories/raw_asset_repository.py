# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Raw Asset Repository

Handles database operations for raw_asset_data table.
"""

import psycopg2
from psycopg2.extras import RealDictCursor, Json, execute_values
from typing import List, Optional, Dict, Any
from uuid import UUID
import os
import logging

from src.database.postgres_connection import get_connection_manager, PostgreSQLConnectionManager


logger = logging.getLogger(__name__)


class RawAssetRepository:
    """
    Repository for raw_asset_data operations.

    Manages normalized asset data after field mapping.
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

    def create_mapped_record(self, batch_id: UUID, integration_id: UUID,
                            raw_source_id: int, mapped_data: Dict[str, Any]) -> int:
        """
        Insert a single mapped record.

        Args:
            batch_id: Batch UUID
            integration_id: Integration UUID
            raw_source_id: ID from raw_source_data table
            mapped_data: Mapped fields dictionary

        Returns:
            ID of inserted record
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO raw_asset_data (
                        integration_id,
                        raw_source_id,
                        batch_id,
                        hostname,
                        fqdn,
                        ip,
                        mac,
                        sn,
                        os,
                        manufacturer,
                        model,
                        vendor_category,
                        first_time,
                        last_time,
                        extended_fields,
                        ingested_at,
                        mapped_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        NOW(), NOW()
                    )
                    RETURNING id
                """, (
                    str(integration_id),
                    raw_source_id,
                    str(batch_id),
                    mapped_data.get('hostname'),
                    mapped_data.get('fqdn'),
                    mapped_data.get('ip'),
                    mapped_data.get('mac'),
                    mapped_data.get('sn'),
                    mapped_data.get('os'),
                    mapped_data.get('manufacturer'),
                    mapped_data.get('model'),
                    mapped_data.get('vendor_category'),
                    mapped_data.get('first_time'),
                    mapped_data.get('last_time'),
                    Json(mapped_data.get('extended_fields')) if mapped_data.get('extended_fields') else None
                ))

                record_id = cur.fetchone()[0]
                conn.commit()

                return record_id

    def create_mapped_records_bulk(self, records: List[Dict[str, Any]]) -> int:
        """
        Bulk insert mapped records.

        Args:
            records: List of record dictionaries with:
                - batch_id
                - integration_id
                - raw_source_id
                - mapped_data (dict with fields)

        Returns:
            Number of records inserted
        """
        if not records:
            return 0

        # Prepare values
        values = []
        for record in records:
            mapped = record['mapped_data']
            values.append((
                str(record['integration_id']),
                record['raw_source_id'],
                str(record['batch_id']),
                mapped.get('hostname'),
                mapped.get('fqdn'),
                mapped.get('ip'),
                mapped.get('mac'),
                mapped.get('sn'),
                mapped.get('os'),
                mapped.get('manufacturer'),
                mapped.get('model'),
                mapped.get('vendor_category'),
                mapped.get('first_time'),
                mapped.get('last_time'),
                Json(mapped.get('extended_fields')) if mapped.get('extended_fields') else None
            ))

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO raw_asset_data (
                        integration_id,
                        raw_source_id,
                        batch_id,
                        hostname,
                        fqdn,
                        ip,
                        mac,
                        sn,
                        os,
                        manufacturer,
                        model,
                        vendor_category,
                        first_time,
                        last_time,
                        extended_fields,
                        ingested_at,
                        mapped_at
                    )
                    VALUES %s
                    """,
                    values,
                    template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())"
                )

                inserted = cur.rowcount
                conn.commit()

                logger.info(f"Bulk inserted {inserted} mapped records")
                return inserted

    def get_by_batch(self, batch_id: UUID, limit: int = 100,
                    offset: int = 0) -> List[Dict[str, Any]]:
        """
        Get mapped records for a batch.

        Args:
            batch_id: Batch UUID
            limit: Maximum number of records
            offset: Number of records to skip

        Returns:
            List of mapped record dictionaries
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        id,
                        integration_id,
                        raw_source_id,
                        hostname,
                        fqdn,
                        ip,
                        mac,
                        sn,
                        os,
                        manufacturer,
                        model,
                        vendor_category,
                        extended_fields,
                        ingested_at,
                        mapped_at
                    FROM raw_asset_data
                    WHERE batch_id = %s
                    ORDER BY id
                    LIMIT %s OFFSET %s
                """, (str(batch_id), limit, offset))

                return [dict(row) for row in cur.fetchall()]

    def count_by_batch(self, batch_id: UUID) -> int:
        """
        Count mapped records in a batch.

        Args:
            batch_id: Batch UUID

        Returns:
            Number of records
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM raw_asset_data WHERE batch_id = %s
                """, (str(batch_id),))

                return cur.fetchone()[0]

    def delete_by_batch(self, batch_id: UUID) -> int:
        """
        Delete all mapped records for a batch.

        Used for remapping after field_mapping changes.

        Args:
            batch_id: Batch UUID

        Returns:
            Number of records deleted
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM raw_asset_data WHERE batch_id = %s
                """, (str(batch_id),))

                deleted = cur.rowcount
                conn.commit()

                if deleted > 0:
                    logger.info(f"Deleted {deleted} mapped records for batch {batch_id}")

                return deleted

    def get_by_integration(self, integration_id: UUID, limit: int = 100,
                          offset: int = 0) -> List[Dict[str, Any]]:
        """
        Get mapped records for an integration.

        Args:
            integration_id: Integration UUID
            limit: Maximum number of records
            offset: Number of records to skip

        Returns:
            List of mapped record dictionaries
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        id,
                        integration_id,
                        raw_source_id,
                        batch_id,
                        hostname,
                        fqdn,
                        ip,
                        mac,
                        sn,
                        os,
                        manufacturer,
                        model,
                        vendor_category,
                        extended_fields,
                        ingested_at,
                        mapped_at
                    FROM raw_asset_data
                    WHERE integration_id = %s
                    ORDER BY mapped_at DESC
                    LIMIT %s OFFSET %s
                """, (str(integration_id), limit, offset))

                return [dict(row) for row in cur.fetchall()]

    def count_by_integration(self, integration_id: UUID) -> int:
        """
        Count mapped records for an integration.

        Args:
            integration_id: Integration UUID

        Returns:
            Number of records
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM raw_asset_data WHERE integration_id = %s
                """, (str(integration_id),))

                return cur.fetchone()[0]

    def get_stats(self, integration_id: UUID) -> Dict[str, Any]:
        """
        Get mapping statistics for an integration.

        Args:
            integration_id: Integration UUID

        Returns:
            Dictionary with statistics
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Get mapped record stats
                cur.execute("""
                    SELECT
                        COUNT(*) as total_mapped_records,
                        COUNT(DISTINCT batch_id) as total_batches,
                        MIN(mapped_at) as first_mapping,
                        MAX(mapped_at) as last_mapping,
                        COUNT(*) FILTER (WHERE hostname IS NOT NULL AND array_length(hostname, 1) > 0) as records_with_hostname,
                        COUNT(*) FILTER (WHERE fqdn IS NOT NULL AND array_length(fqdn, 1) > 0) as records_with_fqdn,
                        COUNT(*) FILTER (WHERE ip IS NOT NULL AND array_length(ip, 1) > 0) as records_with_ip,
                        COUNT(*) FILTER (WHERE mac IS NOT NULL AND array_length(mac, 1) > 0) as records_with_mac,
                        COUNT(*) FILTER (WHERE sn IS NOT NULL AND array_length(sn, 1) > 0) as records_with_sn
                    FROM raw_asset_data
                    WHERE integration_id = %s
                """, (str(integration_id),))

                stats = dict(cur.fetchone())

                # Get raw source data count for comparison
                cur.execute("""
                    SELECT COUNT(*) as total_raw_records
                    FROM raw_source_data
                    WHERE integration_id = %s
                """, (str(integration_id),))

                raw_count = cur.fetchone()['total_raw_records']
                stats['total_raw_records'] = raw_count

                # Calculate mapping rate
                if raw_count > 0:
                    stats['mapping_rate'] = (stats['total_mapped_records'] / raw_count * 100)
                else:
                    stats['mapping_rate'] = 0.0

                return stats

    def get_unmapped_count(self, integration_id: UUID) -> int:
        """
        Get count of raw records not yet mapped for an integration.

        Args:
            integration_id: Integration UUID

        Returns:
            Number of unmapped records
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*)
                    FROM raw_source_data rsd
                    LEFT JOIN raw_asset_data rad ON rsd.id = rad.raw_source_id
                    WHERE rsd.integration_id = %s
                    AND rad.id IS NULL
                """, (str(integration_id),))

                return cur.fetchone()[0]
