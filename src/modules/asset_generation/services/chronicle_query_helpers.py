# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Chronicle Query Helpers - Utility functions for working with consolidated chronicle events.

This module provides convenient helper functions for querying and analyzing
the consolidated chronicle events.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from uuid import UUID

from src.database.postgres_adapter import PostgreSQLAdapter

logger = logging.getLogger(__name__)


class ChronicleQueryHelpers:
    """
    Helper class for querying consolidated chronicle events.

    Provides convenient methods for common chronicle queries and analysis.
    """

    def __init__(self, db_adapter: PostgreSQLAdapter):
        """
        Initialize chronicle query helpers.

        Args:
            db_adapter: PostgreSQL database adapter
        """
        self.db = db_adapter
        logger.info("ChronicleQueryHelpers initialized")

    # =========================================================================
    # ASSET HISTORY QUERIES
    # =========================================================================

    def get_asset_history(
        self,
        asset_id: UUID,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get complete event history for an asset.

        Args:
            asset_id: Asset UUID
            start_time: Optional start time filter
            end_time: Optional end time filter
            limit: Optional limit on number of events

        Returns:
            List of chronicle events for the asset
        """
        query = """
            SELECT
                id, time, event_type,
                changed_attributes, attribute_changes, full_snapshot,
                generation_run_id, operation_id, causation, related_asset_id
            FROM asset_chronicle
            WHERE asset_id = %s::uuid
        """
        params = [str(asset_id)]

        if start_time:
            query += " AND time >= %s"
            params.append(start_time)

        if end_time:
            query += " AND time <= %s"
            params.append(end_time)

        query += " ORDER BY time DESC"

        if limit:
            query += " LIMIT %s"
            params.append(limit)

        try:
            with self.db.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, params)
                columns = [desc[0] for desc in cursor.description]
                results = [dict(zip(columns, row)) for row in cursor.fetchall()]
                logger.info(f"Retrieved {len(results)} events for asset {asset_id}")
                return results
        except Exception as e:
            logger.error(f"Error getting asset history: {e}")
            return []

    def get_asset_current_state(self, asset_id: UUID) -> Optional[Dict[str, Any]]:
        """
        Get current state of an asset by finding its most recent event.

        For asset_created/asset_readded events, returns the full_snapshot.
        For attribute_updated events, you'd need to replay from last snapshot.

        Args:
            asset_id: Asset UUID

        Returns:
            Dictionary with current asset state, or None if not found
        """
        query = """
            SELECT event_type, full_snapshot, attribute_changes, time
            FROM asset_chronicle
            WHERE asset_id = %s::uuid
            ORDER BY time DESC
            LIMIT 1
        """

        try:
            with self.db.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, (str(asset_id),))
                result = cursor.fetchone()

                if not result:
                    logger.warning(f"No events found for asset {asset_id}")
                    return None

                event_type, full_snapshot, attribute_changes, time = result

                # If latest event is creation, return full snapshot
                if event_type in ('asset_created', 'asset_readded'):
                    return {
                        'state': full_snapshot,
                        'as_of': time,
                        'event_type': event_type
                    }

                # For updates, you'd need to find last snapshot and apply changes
                logger.warning(
                    f"Latest event for {asset_id} is {event_type}. "
                    "Need to implement state reconstruction from updates."
                )
                return None

        except Exception as e:
            logger.error(f"Error getting asset current state: {e}")
            return None

    # =========================================================================
    # SEARCH AND FILTERING
    # =========================================================================

    def find_assets_by_attribute(
        self,
        attribute_name: str,
        attribute_value: Any,
        event_types: Optional[List[str]] = None
    ) -> List[UUID]:
        """
        Find assets that have a specific attribute value in their snapshot.

        Args:
            attribute_name: Name of attribute to search (e.g., 'hostname', 'os_family')
            attribute_value: Value to search for
            event_types: Optional filter for event types (default: ['asset_created'])

        Returns:
            List of asset UUIDs matching the criteria
        """
        if event_types is None:
            event_types = ['asset_created', 'asset_readded']

        # Build JSONB contains query
        query = """
            SELECT DISTINCT asset_id
            FROM asset_chronicle
            WHERE event_type = ANY(%s)
            AND full_snapshot->>%s = %s
        """

        try:
            with self.db.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, (event_types, attribute_name, str(attribute_value)))
                results = [row[0] for row in cursor.fetchall()]
                logger.info(
                    f"Found {len(results)} assets with {attribute_name}={attribute_value}"
                )
                return results
        except Exception as e:
            logger.error(f"Error finding assets by attribute: {e}")
            return []

    def find_assets_with_attribute_changes(
        self,
        attribute_name: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> List[UUID]:
        """
        Find assets that had changes to a specific attribute.

        Uses the changed_attributes array for efficient querying.

        Args:
            attribute_name: Attribute to search for (e.g., 'hostname', 'ip')
            start_time: Optional start time filter
            end_time: Optional end time filter

        Returns:
            List of asset UUIDs that had changes to this attribute
        """
        query = """
            SELECT DISTINCT asset_id
            FROM asset_chronicle
            WHERE %s = ANY(changed_attributes)
        """
        params = [attribute_name]

        if start_time:
            query += " AND time >= %s"
            params.append(start_time)

        if end_time:
            query += " AND time <= %s"
            params.append(end_time)

        try:
            with self.db.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, params)
                results = [row[0] for row in cursor.fetchall()]
                logger.info(
                    f"Found {len(results)} assets with {attribute_name} changes"
                )
                return results
        except Exception as e:
            logger.error(f"Error finding assets with attribute changes: {e}")
            return []

    # =========================================================================
    # GENERATION RUN QUERIES
    # =========================================================================

    def get_events_by_run(
        self,
        generation_run_id: UUID
    ) -> List[Dict[str, Any]]:
        """
        Get all events from a specific generation run.

        Args:
            generation_run_id: UUID of the generation run

        Returns:
            List of chronicle events from this run
        """
        query = """
            SELECT
                id, time, asset_id, event_type,
                changed_attributes, attribute_changes, full_snapshot,
                operation_id, causation, related_asset_id
            FROM asset_chronicle
            WHERE generation_run_id = %s::uuid
            ORDER BY time DESC
        """

        try:
            with self.db.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, (str(generation_run_id),))
                columns = [desc[0] for desc in cursor.description]
                results = [dict(zip(columns, row)) for row in cursor.fetchall()]
                logger.info(f"Retrieved {len(results)} events from run {generation_run_id}")
                return results
        except Exception as e:
            logger.error(f"Error getting events by run: {e}")
            return []

    def get_run_summary(
        self,
        generation_run_id: UUID
    ) -> Dict[str, Any]:
        """
        Get summary statistics for a generation run.

        Args:
            generation_run_id: UUID of the generation run

        Returns:
            Dictionary with summary stats
        """
        query = """
            SELECT
                COUNT(*) as total_events,
                COUNT(DISTINCT asset_id) as unique_assets,
                MIN(time) as start_time,
                MAX(time) as end_time,
                event_type,
                COUNT(*) as count
            FROM asset_chronicle
            WHERE generation_run_id = %s::uuid
            GROUP BY event_type
        """

        try:
            with self.db.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, (str(generation_run_id),))
                results = cursor.fetchall()

                if not results:
                    return {}

                summary = {
                    'generation_run_id': str(generation_run_id),
                    'total_events': results[0][0],
                    'unique_assets': results[0][1],
                    'start_time': results[0][2],
                    'end_time': results[0][3],
                    'events_by_type': {}
                }

                for row in results:
                    event_type = row[4]
                    count = row[5]
                    summary['events_by_type'][event_type] = count

                logger.info(f"Generated summary for run {generation_run_id}")
                return summary

        except Exception as e:
            logger.error(f"Error getting run summary: {e}")
            return {}

    # =========================================================================
    # ATTRIBUTE CHANGE ANALYSIS
    # =========================================================================

    def get_attribute_change_summary(
        self,
        asset_id: UUID,
        attribute_name: str
    ) -> List[Dict[str, Any]]:
        """
        Get history of changes to a specific attribute for an asset.

        Args:
            asset_id: Asset UUID
            attribute_name: Attribute to analyze (e.g., 'hostname', 'ip')

        Returns:
            List of change events for this attribute
        """
        query = """
            SELECT
                time, event_type,
                attribute_changes->%s as changes,
                generation_run_id, causation
            FROM asset_chronicle
            WHERE asset_id = %s::uuid
            AND %s = ANY(changed_attributes)
            ORDER BY time DESC
        """

        try:
            with self.db.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, (attribute_name, str(asset_id), attribute_name))
                columns = [desc[0] for desc in cursor.description]
                results = [dict(zip(columns, row)) for row in cursor.fetchall()]
                logger.info(
                    f"Retrieved {len(results)} changes to {attribute_name} "
                    f"for asset {asset_id}"
                )
                return results
        except Exception as e:
            logger.error(f"Error getting attribute change summary: {e}")
            return []

    def get_most_changed_attributes(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get most frequently changed attributes across all assets.

        Args:
            start_time: Optional start time filter
            end_time: Optional end time filter
            limit: Number of top attributes to return

        Returns:
            List of (attribute_name, change_count) tuples
        """
        query = """
            SELECT
                unnest(changed_attributes) as attribute_name,
                COUNT(*) as change_count
            FROM asset_chronicle
            WHERE event_type = 'attribute_updated'
        """
        params = []

        if start_time:
            query += " AND time >= %s"
            params.append(start_time)

        if end_time:
            query += " AND time <= %s"
            params.append(end_time)

        query += """
            GROUP BY attribute_name
            ORDER BY change_count DESC
            LIMIT %s
        """
        params.append(limit)

        try:
            with self.db.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, params)
                results = [
                    {'attribute': row[0], 'change_count': row[1]}
                    for row in cursor.fetchall()
                ]
                logger.info(f"Retrieved top {limit} changed attributes")
                return results
        except Exception as e:
            logger.error(f"Error getting most changed attributes: {e}")
            return []

    # =========================================================================
    # STATISTICS
    # =========================================================================

    def get_chronicle_stats(self) -> Dict[str, Any]:
        """
        Get overall chronicle statistics.

        Returns:
            Dictionary with chronicle stats
        """
        query = """
            SELECT
                COUNT(*) as total_events,
                COUNT(DISTINCT asset_id) as unique_assets,
                COUNT(DISTINCT generation_run_id) as unique_runs,
                MIN(time) as earliest_event,
                MAX(time) as latest_event,
                event_type,
                COUNT(*) as count
            FROM asset_chronicle
            GROUP BY event_type
        """

        try:
            with self.db.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query)
                results = cursor.fetchall()

                if not results:
                    return {}

                stats = {
                    'total_events': results[0][0],
                    'unique_assets': results[0][1],
                    'unique_runs': results[0][2],
                    'earliest_event': results[0][3],
                    'latest_event': results[0][4],
                    'events_by_type': {}
                }

                for row in results:
                    event_type = row[5]
                    count = row[6]
                    stats['events_by_type'][event_type] = count

                logger.info("Generated chronicle statistics")
                return stats

        except Exception as e:
            logger.error(f"Error getting chronicle stats: {e}")
            return {}
