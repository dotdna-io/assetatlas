# SPDX-License-Identifier: AGPL-3.0-or-later
"""
System Preferences Repository

Handles database queries for system-wide dashboard preferences (single-row table).
"""

from typing import Optional, Dict, Any, List
from datetime import datetime
import json
from src.database.postgres_adapter import PostgreSQLAdapter


class ConflictError(Exception):
    """Raised when optimistic locking detects a conflict"""
    def __init__(self, message: str, current_last_modified: datetime):
        super().__init__(message)
        self.current_last_modified = current_last_modified


class SystemPreferencesRepository:
    """Repository for system-wide dashboard preferences data access"""

    def __init__(self, db_adapter: PostgreSQLAdapter):
        """
        Initialize repository with database adapter.

        Args:
            db_adapter: PostgreSQL database adapter
        """
        self.db = db_adapter

    def get_dashboard(self) -> Dict[str, Any]:
        """
        Get system-wide working dashboard.

        Returns:
            Dashboard dict with working_dashboard JSONB and last_modified timestamp
        """
        query = """
            SELECT
                working_dashboard,
                last_modified,
                last_modified_by
            FROM system_dashboard_preferences
            WHERE id = 1
        """

        results = self.db.execute_query(query, {})

        if results and len(results) > 0:
            return results[0]

        # Return empty dashboard if not found (should not happen with migration)
        return {
            'working_dashboard': {'charts': []},
            'last_modified': None,
            'last_modified_by': None
        }

    def update_dashboard(
        self,
        charts: List[Dict[str, Any]],
        modified_by: str,
        expected_last_modified: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Update system-wide working dashboard with optimistic locking.

        Args:
            charts: List of chart configurations
            modified_by: UUID of user saving changes (for audit)
            expected_last_modified: Expected last_modified timestamp (for conflict detection)
                                   If None, skip conflict check

        Returns:
            Updated dashboard dict

        Raises:
            ConflictError: If dashboard was modified since expected_last_modified
        """
        working_dashboard = {'charts': charts}

        # If expected_last_modified is provided, check for conflicts
        if expected_last_modified is not None:
            # First, check current state
            current = self.get_dashboard()
            current_last_modified = current.get('last_modified')

            # Compare timestamps (allowing for microsecond differences)
            if current_last_modified and expected_last_modified:
                # Convert to timestamps for comparison
                if isinstance(current_last_modified, str):
                    from dateutil import parser
                    current_last_modified = parser.parse(current_last_modified)
                if isinstance(expected_last_modified, str):
                    from dateutil import parser
                    expected_last_modified = parser.parse(expected_last_modified)

                # Check if they differ by more than 1 second (to handle precision issues)
                time_diff = abs((current_last_modified - expected_last_modified).total_seconds())
                if time_diff > 1:
                    raise ConflictError(
                        "Dashboard was modified by another user. Please refresh and try again.",
                        current_last_modified
                    )

        # Perform the update
        query = """
            UPDATE system_dashboard_preferences
            SET
                working_dashboard = %(working_dashboard)s,
                last_modified_by = %(modified_by)s
            WHERE id = 1
            RETURNING working_dashboard, last_modified, last_modified_by
        """

        params = {
            'working_dashboard': json.dumps(working_dashboard),
            'modified_by': modified_by
        }

        results = self.db.execute_query(query, params)

        if results and len(results) > 0:
            return results[0]

        # Should not happen - single row always exists
        raise Exception("Failed to update system dashboard preferences")

    def reset_dashboard(self) -> Dict[str, Any]:
        """
        Reset system-wide working dashboard to empty state.

        Returns:
            Reset dashboard dict
        """
        query = """
            UPDATE system_dashboard_preferences
            SET
                working_dashboard = '{"charts": []}'::jsonb,
                last_modified_by = NULL
            WHERE id = 1
            RETURNING working_dashboard, last_modified, last_modified_by
        """

        results = self.db.execute_query(query, {})

        if results and len(results) > 0:
            return results[0]

        raise Exception("Failed to reset system dashboard preferences")
