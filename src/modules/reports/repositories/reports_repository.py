# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Reports Repository

Handles database queries for system-wide reports data.
"""

from typing import Optional, List, Dict, Any, Tuple
from uuid import UUID
import json
from src.database.postgres_adapter import PostgreSQLAdapter


class ReportsRepository:
    """Repository for system-wide reports data access"""

    def __init__(self, db_adapter: PostgreSQLAdapter):
        """
        Initialize repository with database adapter.

        Args:
            db_adapter: PostgreSQL database adapter
        """
        self.db = db_adapter

    def create_report(
        self,
        title: str,
        description: Optional[str],
        charts: List[Dict[str, Any]],
        created_by: str
    ) -> Dict[str, Any]:
        """
        Create a new system-wide report.

        Args:
            title: Report title
            description: Report description (optional)
            charts: List of chart configurations
            created_by: UUID of user creating the report (for audit)

        Returns:
            Created report dict
        """
        query = """
            INSERT INTO reports (title, description, charts, created_by, updated_by)
            VALUES (%(title)s, %(description)s, %(charts)s, %(created_by)s, %(created_by)s)
            RETURNING id, title, description, charts, is_default,
                      created_at, updated_at, created_by, updated_by
        """

        params = {
            'title': title,
            'description': description,
            'charts': json.dumps(charts),
            'created_by': created_by
        }

        results = self.db.execute_query(query, params)

        if results and len(results) > 0:
            return results[0]
        return None

    def get_report_by_id(self, report_id: str) -> Optional[Dict[str, Any]]:
        """
        Get report by ID (system-wide access).

        Args:
            report_id: Report UUID

        Returns:
            Report dict or None if not found
        """
        query = """
            SELECT
                id,
                title,
                description,
                charts,
                is_default,
                created_at,
                updated_at,
                created_by,
                updated_by
            FROM reports
            WHERE id = %(report_id)s
        """

        params = {
            'report_id': report_id
        }

        results = self.db.execute_query(query, params)

        if results and len(results) > 0:
            return results[0]
        return None

    def get_default_report(self) -> Optional[Dict[str, Any]]:
        """
        Get the default report.

        Returns:
            Default report dict or None if not found
        """
        query = """
            SELECT
                id,
                title,
                description,
                charts,
                is_default,
                created_at,
                updated_at,
                created_by,
                updated_by
            FROM reports
            WHERE is_default = TRUE
            LIMIT 1
        """

        results = self.db.execute_query(query, {})

        if results and len(results) > 0:
            return results[0]
        return None

    def list_reports(
        self,
        page: int = 1,
        limit: int = 50,
        search: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        List all system-wide reports with pagination.

        Args:
            page: Page number (1-indexed)
            limit: Results per page
            search: Search term (optional)

        Returns:
            Tuple of (reports list, total count)
        """
        offset = (page - 1) * limit

        # Build WHERE clause
        where_conditions = []
        params = {
            'limit': limit,
            'offset': offset
        }

        if search:
            where_conditions.append("(title ILIKE %(search)s OR description ILIKE %(search)s)")
            params['search'] = f'%{search}%'

        where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""

        # Get total count
        count_query = f"""
            SELECT COUNT(*) as total
            FROM reports
            {where_clause}
        """
        count_results = self.db.execute_query(count_query, params)
        total = count_results[0]['total'] if count_results else 0

        # Get paginated reports
        query = f"""
            SELECT
                id,
                title,
                description,
                charts,
                jsonb_array_length(charts) as chart_count,
                is_default,
                created_at,
                updated_at,
                created_by,
                updated_by
            FROM reports
            {where_clause}
            ORDER BY is_default DESC, updated_at DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """

        reports = self.db.execute_query(query, params)

        return reports or [], total

    def update_report(
        self,
        report_id: str,
        updated_by: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        charts: Optional[List[Dict[str, Any]]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Update an existing system-wide report.

        Args:
            report_id: Report UUID
            updated_by: UUID of user updating the report (for audit)
            title: New title (optional)
            description: New description (optional)
            charts: New charts array (optional)

        Returns:
            Updated report dict or None if not found
        """
        # Build SET clause dynamically
        set_clauses = ["updated_by = %(updated_by)s"]
        params = {
            'report_id': report_id,
            'updated_by': updated_by
        }

        if title is not None:
            set_clauses.append("title = %(title)s")
            params['title'] = title

        if description is not None:
            set_clauses.append("description = %(description)s")
            params['description'] = description

        if charts is not None:
            set_clauses.append("charts = %(charts)s")
            params['charts'] = json.dumps(charts)

        if len(set_clauses) == 1:
            # Only updated_by, nothing else to update
            return self.get_report_by_id(report_id)

        set_clause = ", ".join(set_clauses)

        query = f"""
            UPDATE reports
            SET {set_clause}
            WHERE id = %(report_id)s
            RETURNING id, title, description, charts, is_default,
                      created_at, updated_at, created_by, updated_by
        """

        results = self.db.execute_query(query, params)

        if results and len(results) > 0:
            return results[0]
        return None

    def delete_report(self, report_id: str) -> bool:
        """
        Delete a system-wide report.

        Args:
            report_id: Report UUID

        Returns:
            True if deleted, False if not found

        Raises:
            ValueError: If attempting to delete the default report
        """
        # Check if this is the default report
        check_query = """
            SELECT is_default
            FROM reports
            WHERE id = %(report_id)s
        """

        check_results = self.db.execute_query(check_query, {'report_id': report_id})

        if not check_results or len(check_results) == 0:
            # Report not found
            return False

        if check_results[0].get('is_default', False):
            raise ValueError("Cannot delete the default report")

        # Proceed with deletion
        query = """
            DELETE FROM reports
            WHERE id = %(report_id)s
            RETURNING id
        """

        params = {
            'report_id': report_id
        }

        results = self.db.execute_query(query, params)

        return results is not None and len(results) > 0
