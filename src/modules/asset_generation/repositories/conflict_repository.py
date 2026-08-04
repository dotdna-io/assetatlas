# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Conflict Repository - Persistence layer for conflict detection.

DEPRECATED: The ConflictDetector no longer writes to asset_conflicts table.
Conflict detection is now handled via business-level checks in the detector
and SQL-based alert rules evaluated at S20. This repository is retained for
backward compatibility but is no longer called by the pipeline.
"""

import json
import logging
from typing import List, Dict, Optional
from uuid import UUID
from datetime import datetime

from src.database.postgres_adapter import _clean_nan

logger = logging.getLogger(__name__)


class ConflictRepository:
    """
    Repository for conflict persistence operations.

    Provides interface for storing and retrieving conflict data.
    """

    def __init__(self, db_adapter):
        """
        Initialize conflict repository.

        Args:
            db_adapter: PostgreSQL database adapter
        """
        self.db = db_adapter
        logger.info("ConflictRepository initialized")

    def save_conflicts(self, conflicts: List) -> int:
        """
        Save multiple conflicts to database.

        Args:
            conflicts: List of Conflict objects

        Returns:
            Number of conflicts saved
        """
        if not conflicts:
            return 0

        logger.info(f"Saving {len(conflicts)} conflicts to database...")

        try:
            with self.db.conn_manager.get_cursor() as (cursor, conn):
                for conflict in conflicts:
                    cursor.execute("""
                        INSERT INTO asset_conflicts (
                            id, generation_run_id, conflict_type, severity,
                            asset_id, component, conflicting_asset_id,
                            evidence, status, created_at, updated_at
                        )
                        VALUES (
                            %s::uuid, %s::uuid, %s, %s,
                            %s::uuid, %s, %s::uuid,
                            %s::jsonb, %s, %s, %s
                        )
                    """, (
                        str(conflict.id),
                        str(conflict.generation_run_id),
                        conflict.conflict_type,
                        conflict.severity,
                        str(conflict.asset_id) if conflict.asset_id else None,
                        conflict.component,
                        str(conflict.conflicting_asset_id) if conflict.conflicting_asset_id else None,
                        json.dumps(_clean_nan(conflict.evidence)),
                        conflict.status,
                        conflict.created_at,
                        conflict.created_at
                    ))

                self.db.conn_manager.commit_unless_scoped(conn)
                count = len(conflicts)
                logger.info(f"Successfully saved {count} conflicts")
                return count

        except Exception as e:
            logger.error(f"Error saving conflicts: {e}")
            raise

    def get_conflicts_by_run(self, generation_run_id: UUID) -> List[Dict]:
        """
        Get all conflicts for a specific generation run.

        Args:
            generation_run_id: Generation run ID

        Returns:
            List of conflict dictionaries
        """
        try:
            with self.db.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute("""
                    SELECT
                        id, generation_run_id, conflict_type, severity,
                        asset_id, component, conflicting_asset_id,
                        evidence, status, resolution_strategy,
                        resolved_at, resolved_by, resolution_notes,
                        created_at, updated_at
                    FROM asset_conflicts
                    WHERE generation_run_id = %s::uuid
                    ORDER BY severity DESC, created_at DESC
                """, (str(generation_run_id),))

                conflicts = []
                for row in cursor.fetchall():
                    conflicts.append({
                        'id': row[0],
                        'generation_run_id': row[1],
                        'conflict_type': row[2],
                        'severity': row[3],
                        'asset_id': row[4],
                        'component': row[5],
                        'conflicting_asset_id': row[6],
                        'evidence': row[7],
                        'status': row[8],
                        'resolution_strategy': row[9],
                        'resolved_at': row[10],
                        'resolved_by': row[11],
                        'resolution_notes': row[12],
                        'created_at': row[13],
                        'updated_at': row[14]
                    })

                return conflicts

        except Exception as e:
            logger.error(f"Error retrieving conflicts: {e}")
            raise

    def get_open_conflicts(self, limit: Optional[int] = None) -> List[Dict]:
        """
        Get all open (unresolved) conflicts.

        Args:
            limit: Optional limit on number of results

        Returns:
            List of conflict dictionaries
        """
        try:
            with self.db.conn_manager.get_cursor() as (cursor, conn):
                query = """
                    SELECT
                        id, generation_run_id, conflict_type, severity,
                        asset_id, component, conflicting_asset_id,
                        evidence, status, created_at, updated_at
                    FROM asset_conflicts
                    WHERE status = 'open'
                    ORDER BY severity DESC, created_at DESC
                """

                if limit:
                    query += f" LIMIT {limit}"

                cursor.execute(query)

                conflicts = []
                for row in cursor.fetchall():
                    conflicts.append({
                        'id': row[0],
                        'generation_run_id': row[1],
                        'conflict_type': row[2],
                        'severity': row[3],
                        'asset_id': row[4],
                        'component': row[5],
                        'conflicting_asset_id': row[6],
                        'evidence': row[7],
                        'status': row[8],
                        'created_at': row[9],
                        'updated_at': row[10]
                    })

                return conflicts

        except Exception as e:
            logger.error(f"Error retrieving open conflicts: {e}")
            raise

    def update_conflict_status(
        self,
        conflict_id: UUID,
        status: str,
        resolution_strategy: Optional[str] = None,
        resolved_by: Optional[str] = None,
        resolution_notes: Optional[str] = None
    ) -> bool:
        """
        Update conflict status and resolution information.

        Args:
            conflict_id: Conflict ID
            status: New status ('open', 'resolved', 'ignored')
            resolution_strategy: How it was resolved
            resolved_by: Who resolved it
            resolution_notes: Additional notes

        Returns:
            True if updated successfully
        """
        try:
            with self.db.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute("""
                    UPDATE asset_conflicts
                    SET
                        status = %s,
                        resolution_strategy = %s,
                        resolved_at = %s,
                        resolved_by = %s,
                        resolution_notes = %s,
                        updated_at = %s
                    WHERE id = %s::uuid
                """, (
                    status,
                    resolution_strategy,
                    datetime.now() if status != 'open' else None,
                    resolved_by,
                    resolution_notes,
                    datetime.now(),
                    str(conflict_id)
                ))

                self.db.conn_manager.commit_unless_scoped(conn)
                updated = cursor.rowcount > 0

                if updated:
                    logger.info(f"Updated conflict {conflict_id} to status '{status}'")
                else:
                    logger.warning(f"Conflict {conflict_id} not found")

                return updated

        except Exception as e:
            logger.error(f"Error updating conflict status: {e}")
            raise

    def get_conflict_stats(self) -> Dict:
        """
        Get statistics about conflicts in the database.

        Returns:
            Dictionary with conflict statistics
        """
        try:
            with self.db.conn_manager.get_cursor() as (cursor, conn):
                # Total conflicts
                cursor.execute("SELECT COUNT(*) FROM asset_conflicts")
                total = cursor.fetchone()[0]

                # By status
                cursor.execute("""
                    SELECT status, COUNT(*)
                    FROM asset_conflicts
                    GROUP BY status
                """)
                by_status = dict(cursor.fetchall())

                # By severity
                cursor.execute("""
                    SELECT severity, COUNT(*)
                    FROM asset_conflicts
                    GROUP BY severity
                """)
                by_severity = dict(cursor.fetchall())

                # By type
                cursor.execute("""
                    SELECT conflict_type, COUNT(*)
                    FROM asset_conflicts
                    GROUP BY conflict_type
                """)
                by_type = dict(cursor.fetchall())

                return {
                    'total_conflicts': total,
                    'by_status': by_status,
                    'by_severity': by_severity,
                    'by_type': by_type
                }

        except Exception as e:
            logger.error(f"Error retrieving conflict stats: {e}")
            raise
