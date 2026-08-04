# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Integration Group Repository

Handles database operations for integration_groups table.
Uses db_adapter pattern with named parameters.
"""

from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger(__name__)


class IntegrationGroupRepository:
    """Repository for integration group database operations."""

    def __init__(self, db_adapter):
        """
        Initialize repository with database adapter.

        Args:
            db_adapter: PostgreSQLAdapter instance
        """
        self.db = db_adapter

    def list_all(self) -> List[Dict[str, Any]]:
        """
        List all integration groups with member counts.

        Returns:
            List of group dicts with member_count
        """
        query = """
            SELECT
                ig.id,
                ig.name,
                ig.integration_type,
                ig.created_at,
                ig.updated_at,
                COUNT(i.id) AS member_count,
                (SELECT i2.logo FROM integrations i2 WHERE i2.group_id = ig.id LIMIT 1) AS logo
            FROM integration_groups ig
            LEFT JOIN integrations i ON i.group_id = ig.id
            GROUP BY ig.id, ig.name, ig.integration_type, ig.created_at, ig.updated_at
            ORDER BY ig.name
        """
        return self.db.execute_query(query)

    def get_by_id(self, group_id: str) -> Optional[Dict[str, Any]]:
        """
        Get integration group by ID with member details.

        Args:
            group_id: Group UUID as string

        Returns:
            Group dict with 'members' key, or None if not found
        """
        group_query = """
            SELECT ig.id, ig.name, ig.integration_type, ig.created_at, ig.updated_at,
                   (SELECT i2.logo FROM integrations i2 WHERE i2.group_id = ig.id LIMIT 1) AS logo
            FROM integration_groups ig
            WHERE ig.id = %(group_id)s::uuid
        """
        rows = self.db.execute_query(group_query, {'group_id': group_id})
        if not rows:
            return None

        group = rows[0]

        members_query = """
            SELECT id, name, integration_type, status, group_id, logo
            FROM integrations
            WHERE group_id = %(group_id)s::uuid
            ORDER BY name
        """
        members = self.db.execute_query(members_query, {'group_id': group_id})
        group['members'] = members or []
        group['member_count'] = len(group['members'])

        return group

    def create(self, name: str, integration_type: str) -> Dict[str, Any]:
        """
        Create a new integration group.

        Args:
            name: Group name
            integration_type: Integration type for this group

        Returns:
            Created group dict
        """
        query = """
            INSERT INTO integration_groups (name, integration_type)
            VALUES (%(name)s, %(integration_type)s)
            RETURNING id, name, integration_type, created_at, updated_at
        """
        rows = self.db.execute_query(query, {
            'name': name,
            'integration_type': integration_type,
        })
        return rows[0]

    def update(self, group_id: str, name: str) -> Optional[Dict[str, Any]]:
        """
        Update integration group name.

        Args:
            group_id: Group UUID as string
            name: New group name

        Returns:
            Updated group dict, or None if not found
        """
        query = """
            UPDATE integration_groups
            SET name = %(name)s, updated_at = now()
            WHERE id = %(group_id)s::uuid
            RETURNING id, name, integration_type, created_at, updated_at
        """
        rows = self.db.execute_query(query, {
            'group_id': group_id,
            'name': name,
        })
        return rows[0] if rows else None

    def delete(self, group_id: str) -> bool:
        """
        Delete integration group. Members get group_id set to NULL via ON DELETE SET NULL.

        Args:
            group_id: Group UUID as string

        Returns:
            True if deleted, False if not found
        """
        query = """
            DELETE FROM integration_groups
            WHERE id = %(group_id)s::uuid
        """
        result = self.db.execute_query(query, {'group_id': group_id}, fetch=False)
        # execute_query with fetch=False returns rowcount or None
        return bool(result)

    def add_members(self, group_id: str, integration_ids: List[str]) -> None:
        """
        Add integrations to a group by setting their group_id.

        Args:
            group_id: Group UUID as string
            integration_ids: List of integration UUID strings
        """
        query = """
            UPDATE integrations
            SET group_id = %(group_id)s::uuid
            WHERE id = ANY(%(integration_ids)s::uuid[])
        """
        self.db.execute_query(query, {
            'group_id': group_id,
            'integration_ids': integration_ids,
        }, fetch=False)

    def remove_member(self, group_id: str, integration_id: str) -> None:
        """
        Remove an integration from a group by clearing its group_id.

        Args:
            group_id: Group UUID as string
            integration_id: Integration UUID as string
        """
        query = """
            UPDATE integrations
            SET group_id = NULL
            WHERE id = %(integration_id)s::uuid AND group_id = %(group_id)s::uuid
        """
        self.db.execute_query(query, {
            'group_id': group_id,
            'integration_id': integration_id,
        }, fetch=False)

    def get_group_mapping(self) -> Dict[str, str]:
        """
        Get mapping of integration names to their group names.

        Returns:
            Dict of {integration_name: group_name}
        """
        query = """
            SELECT i.name AS integration_name, ig.name AS group_name
            FROM integrations i
            JOIN integration_groups ig ON i.group_id = ig.id
        """
        rows = self.db.execute_query(query)
        return {row['integration_name']: row['group_name'] for row in (rows or [])}
