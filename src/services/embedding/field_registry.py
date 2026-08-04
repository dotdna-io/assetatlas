# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Field Registry - Database operations for field definitions

Handles CRUD operations for both static and extended fields stored
in the field_registry PostgreSQL table.
"""

import logging
from typing import Optional, List
from dataclasses import dataclass
from datetime import datetime

from psycopg2 import extras as pg_extras

from .config import EmbeddingConfig, default_config
from src.database.postgres_connection import PostgreSQLConnectionManager, get_connection_manager

logger = logging.getLogger(__name__)


@dataclass
class FieldDefinition:
    """Represents a field definition from the registry."""
    id: int
    field_name: str
    field_type: str  # 'static' or 'extended'
    db_path: str
    display_name: str
    description: str
    description_source: Optional[str]  # 'hardcoded', 'llm', 'manual'
    data_source: Optional[str]  # Integration name for extended fields
    searchable: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_dict(cls, data: dict) -> 'FieldDefinition':
        """Create FieldDefinition from database row dict."""
        return cls(
            id=data['id'],
            field_name=data['field_name'],
            field_type=data['field_type'],
            db_path=data['db_path'],
            display_name=data['display_name'],
            description=data['description'],
            description_source=data.get('description_source'),
            data_source=data.get('data_source'),
            searchable=data.get('searchable', True),
            created_at=data['created_at'],
            updated_at=data['updated_at'],
        )

    def to_embedding_document(self) -> str:
        """Convert to document string for embedding generation."""
        return f"{self.field_name} - {self.description}"


class FieldRegistry:
    """
    Manages field definitions in PostgreSQL.

    Handles both static fields (AssetAtlas core) and extended fields
    (from integrations via extended_attributes JSONB).
    """

    def __init__(self, config: Optional[EmbeddingConfig] = None,
                 connection_manager: Optional[PostgreSQLConnectionManager] = None):
        """
        Initialize field registry.

        Args:
            config: Embedding configuration. Uses default if not provided.
            connection_manager: PostgreSQL connection manager. Uses global if not provided.
        """
        self.config = config or default_config
        self._conn_manager = connection_manager or get_connection_manager()

    def _execute_query(self, query: str, params: tuple = None, fetch: bool = True):
        """Execute a query and optionally fetch results."""
        try:
            with self._conn_manager.get_cursor(cursor_factory=pg_extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, params)
                if fetch:
                    rows = cursor.fetchall()
                    # Commit write queries that use RETURNING (INSERT/UPDATE/DELETE)
                    query_upper = query.lstrip().upper()
                    if query_upper.startswith(('INSERT', 'UPDATE', 'DELETE')):
                        conn.commit()
                    return rows
                conn.commit()
                return cursor.rowcount
        except Exception as e:
            logger.error(f"Database error: {e}")
            raise

    def get_all_fields(self) -> List[FieldDefinition]:
        """Get all field definitions."""
        query = """
            SELECT * FROM field_registry
            ORDER BY field_type, field_name
        """
        rows = self._execute_query(query)
        return [FieldDefinition.from_dict(row) for row in rows]

    def get_searchable_fields(self) -> List[FieldDefinition]:
        """Get all searchable field definitions."""
        query = """
            SELECT * FROM field_registry
            WHERE searchable = true
            ORDER BY field_type, field_name
        """
        rows = self._execute_query(query)
        return [FieldDefinition.from_dict(row) for row in rows]

    def get_field_by_name(self, field_name: str) -> Optional[FieldDefinition]:
        """Get a specific field by name."""
        query = """
            SELECT * FROM field_registry
            WHERE field_name = %s
        """
        rows = self._execute_query(query, (field_name,))
        if rows:
            return FieldDefinition.from_dict(rows[0])
        return None

    def get_static_fields(self) -> List[FieldDefinition]:
        """Get only static (core AssetAtlas) fields."""
        query = """
            SELECT * FROM field_registry
            WHERE field_type = 'static' AND searchable = true
            ORDER BY field_name
        """
        rows = self._execute_query(query)
        return [FieldDefinition.from_dict(row) for row in rows]

    def get_extended_fields(self) -> List[FieldDefinition]:
        """Get only extended (integration-specific) fields."""
        query = """
            SELECT * FROM field_registry
            WHERE field_type = 'extended' AND searchable = true
            ORDER BY data_source, field_name
        """
        rows = self._execute_query(query)
        return [FieldDefinition.from_dict(row) for row in rows]

    def register_extended_field(
        self,
        field_name: str,
        display_name: str,
        description: str,
        data_source: str,
        description_source: str = 'llm',
        searchable: bool = True
    ) -> FieldDefinition:
        """
        Register a new extended field from an integration.

        Args:
            field_name: Internal field name (e.g., 'crowdstrike_agent_id')
            display_name: Human-readable name (e.g., 'CrowdStrike Agent ID')
            description: Field description (usually LLM-generated)
            data_source: Integration name (e.g., 'crowdstrike')
            description_source: How description was created ('llm', 'manual')
            searchable: Whether field should be searchable in Explorer

        Returns:
            Created FieldDefinition
        """
        # Build db_path for JSONB access
        db_path = f"extended_attributes->>'{field_name}'"

        query = """
            INSERT INTO field_registry
            (field_name, field_type, db_path, display_name, description,
             description_source, data_source, searchable)
            VALUES (%s, 'extended', %s, %s, %s, %s, %s, %s)
            ON CONFLICT (field_name) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                description = EXCLUDED.description,
                description_source = EXCLUDED.description_source,
                updated_at = NOW()
            RETURNING *
        """
        params = (
            field_name, db_path, display_name, description,
            description_source, data_source, searchable
        )
        rows = self._execute_query(query, params)

        if rows:
            logger.info(f"Registered extended field: {field_name} from {data_source}")
            return FieldDefinition.from_dict(rows[0])

        raise RuntimeError(f"Failed to register field: {field_name}")

    def update_description(
        self,
        field_name: str,
        description: str,
        description_source: str = 'manual',
        display_name: Optional[str] = None,
    ) -> Optional[FieldDefinition]:
        """
        Update a field's description and optionally its display_name.

        Args:
            field_name: Field to update
            description: New description
            description_source: How description was created
            display_name: New display name (unchanged if None)

        Returns:
            Updated FieldDefinition or None if not found
        """
        # Never overwrite static (core) field descriptions with LLM-generated ones
        if description_source == 'llm':
            existing = self.get_field_by_name(field_name)
            if existing and existing.field_type == 'static':
                logger.debug(f"Skipping LLM description update for static field: {field_name}")
                return existing

        if display_name is not None:
            query = """
                UPDATE field_registry
                SET description = %s, description_source = %s,
                    display_name = %s, updated_at = NOW()
                WHERE field_name = %s
                RETURNING *
            """
            rows = self._execute_query(query, (description, description_source, display_name, field_name))
        else:
            query = """
                UPDATE field_registry
                SET description = %s, description_source = %s, updated_at = NOW()
                WHERE field_name = %s
                RETURNING *
            """
            rows = self._execute_query(query, (description, description_source, field_name))

        if rows:
            logger.info(f"Updated description for field: {field_name}")
            return FieldDefinition.from_dict(rows[0])
        return None

    def set_searchable(self, field_name: str, searchable: bool) -> bool:
        """
        Enable or disable searchability for a field.

        Returns:
            True if field was updated, False if not found
        """
        query = """
            UPDATE field_registry
            SET searchable = %s, updated_at = NOW()
            WHERE field_name = %s
        """
        rows_affected = self._execute_query(query, (searchable, field_name), fetch=False)
        return rows_affected > 0

    def delete_field(self, field_name: str) -> bool:
        """
        Delete a field definition (usually only for extended fields).

        Returns:
            True if field was deleted, False if not found
        """
        query = "DELETE FROM field_registry WHERE field_name = %s"
        rows_affected = self._execute_query(query, (field_name,), fetch=False)
        if rows_affected > 0:
            logger.info(f"Deleted field: {field_name}")
        return rows_affected > 0

    def get_fields_by_data_source(self, data_source: str) -> List[FieldDefinition]:
        """Get all fields from a specific data source/integration."""
        query = """
            SELECT * FROM field_registry
            WHERE data_source = %s
            ORDER BY field_name
        """
        rows = self._execute_query(query, (data_source,))
        return [FieldDefinition.from_dict(row) for row in rows]

    def field_exists(self, field_name: str) -> bool:
        """Check if a field exists in the registry."""
        query = "SELECT 1 FROM field_registry WHERE field_name = %s"
        rows = self._execute_query(query, (field_name,))
        return len(rows) > 0

    def has_description(self, field_name: str) -> bool:
        """Check if a field has a non-empty description."""
        query = """
            SELECT EXISTS(
                SELECT 1 FROM field_registry
                WHERE field_name = %s
                AND description IS NOT NULL
                AND description != ''
                AND description_source IN ('llm', 'manual', 'hardcoded')
            ) AS exists
        """
        rows = self._execute_query(query, (field_name,))
        return rows[0]['exists'] if rows else False

    def get_fields_needing_descriptions(self) -> list:
        """Get extended fields that have no description or empty description."""
        query = """
            SELECT field_name, data_source
            FROM field_registry
            WHERE field_type = 'extended'
            AND (description IS NULL OR description = '' OR description_source IS NULL)
            ORDER BY field_name
        """
        return self._execute_query(query)

    def close(self):
        """Close database connection (no-op, connections managed by pool)."""
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
