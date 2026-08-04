# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Rule Management Service for Tag Rules

Handles CRUD operations for tag rules with validation, JSONB serialization,
and rule ID generation.

Key Features:
- Create, Read, Update, Delete, List rules
- Pydantic model validation
- Unique rule ID generation
- JSONB serialization/deserialization
- Pagination support
"""

import logging
import json
import secrets
import time
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime

from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.tag_rules.models.rule import (
    TagRuleCreate,
    TagRuleUpdate,
    TagRule,
    TagRulesList
)

logger = logging.getLogger(__name__)


class RuleManagementService:
    """
    Service for managing tag rules (CRUD operations).

    Handles all database operations for the tag_rules table with
    proper validation and error handling.
    """

    def __init__(
        self,
        db_adapter: PostgreSQLAdapter,
        auto_execute: bool = True,
        execution_service: Optional[Any] = None,
    ):
        """
        Initialize rule management service.

        Args:
            db_adapter: PostgreSQL database adapter
            auto_execute: If True, automatically execute rules after create/update
            execution_service: Shared RuleExecutionService for auto-execute on
                create/update. Optional because RuleExecutionService depends on
                this service (circular dep) — the factory constructs both and
                injects this attribute after both exist. When None, auto-execute
                is a no-op.
        """
        self.db = db_adapter
        self.auto_execute = auto_execute
        self.execution_service = execution_service

    def generate_rule_id(self) -> str:
        """
        Generate a unique rule ID.

        Format: rule_{timestamp}_{random}
        Example: rule_1698765432_a1b2c3d4

        Returns:
            Unique rule ID string
        """
        timestamp = int(time.time())
        random_suffix = secrets.token_hex(4)  # 8 character hex string
        return f"rule_{timestamp}_{random_suffix}"

    def _serialize_jsonb_fields(self, rule_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert dict/list fields to JSON strings for PostgreSQL JSONB storage.

        Args:
            rule_data: Dictionary with rule data

        Returns:
            Dictionary with JSONB fields serialized
        """
        serialized = rule_data.copy()

        # Fields that need JSON serialization
        jsonb_fields = [
            "target_filters",
            "tags",
            "compliance_criteria",
            "detection_config"
        ]

        for field in jsonb_fields:
            if field in serialized and serialized[field] is not None:
                if isinstance(serialized[field], (dict, list)):
                    serialized[field] = json.dumps(serialized[field])

        return serialized

    def _deserialize_jsonb_fields(self, row: Dict[str, Any], columns: List[str]) -> Dict[str, Any]:
        """
        Convert database row to dictionary with JSONB fields deserialized.

        Args:
            row: Database row dictionary (from RealDictCursor)
            columns: List of column names

        Returns:
            Dictionary with deserialized data
        """
        rule_dict = {}

        for col_name in columns:
            value = row.get(col_name)

            # Handle datetime conversion
            if isinstance(value, datetime):
                rule_dict[col_name] = value
            # JSONB fields are already deserialized by psycopg2
            # but we handle them explicitly for clarity
            elif col_name in ["target_filters", "tags", "compliance_criteria", "detection_config"]:
                rule_dict[col_name] = value  # Already a dict/list from psycopg2
            else:
                rule_dict[col_name] = value

        return rule_dict

    def create_rule(self, rule_create: TagRuleCreate) -> TagRule:
        """
        Create a new tag rule.

        Args:
            rule_create: Validated rule creation data

        Returns:
            Created TagRule instance

        Raises:
            Exception: If rule creation fails

        Example:
            rule = service.create_rule(TagRuleCreate(
                name="Windows Server Classification",
                type="classification",
                target_filters=[...],
                tags={"label": "Windows-Server", "color": "#0078d4"}
            ))
        """
        try:
            # Generate unique rule ID
            rule_id = self.generate_rule_id()

            # Prepare data for insertion
            rule_data = rule_create.model_dump()
            rule_data["id"] = rule_id

            # Serialize JSONB fields
            rule_data = self._serialize_jsonb_fields(rule_data)

            # Build INSERT query
            columns = [
                "id", "name", "description", "type", "enabled",
                "target_filters", "tags", "compliance_criteria", "detection_config"
            ]

            placeholders = ", ".join([f"%({col})s" for col in columns])
            columns_str = ", ".join(columns)

            query = f"""
                INSERT INTO tag_rules ({columns_str})
                VALUES ({placeholders})
                RETURNING id, name, description, type, enabled,
                          target_filters, tags, compliance_criteria, detection_config,
                          created_at, last_modified, last_executed_at,
                          execution_count, last_execution_duration_ms,
                          last_execution_assets_matched, last_execution_assets_passed,
                          last_execution_assets_failed
            """

            # Execute query
            result = self.db.execute_query(query, rule_data)

            if not result or len(result) == 0:
                raise Exception("Failed to create rule - no data returned")

            # Get column names from cursor description
            # For now, we'll manually specify them (in production, get from cursor.description)
            result_columns = [
                "id", "name", "description", "type", "enabled",
                "target_filters", "tags", "compliance_criteria", "detection_config",
                "created_at", "last_modified", "last_executed_at",
                "execution_count", "last_execution_duration_ms",
                "last_execution_assets_matched", "last_execution_assets_passed",
                "last_execution_assets_failed"
            ]

            # Deserialize and create TagRule model
            rule_dict = self._deserialize_jsonb_fields(result[0], result_columns)
            created_rule = TagRule(**rule_dict)

            logger.info(f"Created rule: {rule_id} - {rule_create.name}")

            # Auto-execute rule if enabled
            if self.auto_execute and created_rule.enabled and self.execution_service is not None:
                try:
                    logger.info(f"Auto-executing newly created rule: {rule_id}")
                    exec_result = self.execution_service.execute_rule(
                        rule_id=rule_id,
                        triggered_by="api"
                    )
                    logger.info(
                        f"Rule {rule_id} executed after creation: "
                        f"{exec_result.get('assets_matched', 0)} assets matched"
                    )

                    # Fetch rule again to get updated execution stats
                    # Reason: execute_rule() updates the database, but created_rule object is stale
                    created_rule = self.get_rule(rule_id)
                except Exception as exec_error:
                    logger.error(
                        f"Error executing rule after creation: {exec_error}",
                        exc_info=True
                    )
                    # Don't fail the creation if execution fails
                    pass

            return created_rule

        except Exception as e:
            logger.error(f"Error creating rule: {e}", exc_info=True)
            raise

    def get_rule(self, rule_id: str) -> Optional[TagRule]:
        """
        Get a rule by ID.

        Args:
            rule_id: Rule ID to fetch

        Returns:
            TagRule instance if found, None otherwise

        Example:
            rule = service.get_rule("rule_1234567890_abc123")
        """
        try:
            query = """
                SELECT id, name, description, type, enabled,
                       target_filters, tags, compliance_criteria, detection_config,
                       created_at, last_modified, last_executed_at,
                       execution_count, last_execution_duration_ms,
                       last_execution_assets_matched, last_execution_assets_passed,
                       last_execution_assets_failed
                FROM tag_rules
                WHERE id = %(rule_id)s
            """

            result = self.db.execute_query(query, {"rule_id": rule_id})

            if not result or len(result) == 0:
                logger.debug(f"Rule not found: {rule_id}")
                return None

            # Column names matching SELECT order
            columns = [
                "id", "name", "description", "type", "enabled",
                "target_filters", "tags", "compliance_criteria", "detection_config",
                "created_at", "last_modified", "last_executed_at",
                "execution_count", "last_execution_duration_ms",
                "last_execution_assets_matched", "last_execution_assets_passed",
                "last_execution_assets_failed"
            ]

            rule_dict = self._deserialize_jsonb_fields(result[0], columns)
            return TagRule(**rule_dict)

        except Exception as e:
            logger.error(f"Error fetching rule {rule_id}: {e}", exc_info=True)
            raise

    def update_rule(self, rule_id: str, rule_update: TagRuleUpdate) -> Optional[TagRule]:
        """
        Update an existing rule.

        Args:
            rule_id: Rule ID to update
            rule_update: Update data (only provided fields will be updated)

        Returns:
            Updated TagRule instance if found, None otherwise

        Example:
            updated = service.update_rule(
                "rule_1234567890_abc123",
                TagRuleUpdate(enabled=False, description="Updated description")
            )
        """
        try:
            # Get only the fields that were provided (exclude None values from optional fields)
            update_data = rule_update.model_dump(exclude_unset=True)

            if not update_data:
                logger.warning(f"No fields to update for rule {rule_id}")
                return self.get_rule(rule_id)

            # Serialize JSONB fields
            update_data = self._serialize_jsonb_fields(update_data)

            # Build UPDATE query dynamically based on provided fields
            set_clauses = [f"{field} = %({field})s" for field in update_data.keys()]
            set_clause = ", ".join(set_clauses)

            # Add rule_id to params
            params = update_data.copy()
            params["rule_id"] = rule_id

            query = f"""
                UPDATE tag_rules
                SET {set_clause}, last_modified = NOW()
                WHERE id = %(rule_id)s
                RETURNING *
            """

            result = self.db.execute_query(query, params)

            if not result or len(result) == 0:
                logger.warning(f"Rule not found for update: {rule_id}")
                return None

            # Column names
            columns = [
                "id", "name", "description", "type", "enabled",
                "target_filters", "tags", "compliance_criteria", "detection_config",
                "created_at", "last_modified", "last_executed_at",
                "execution_count", "last_execution_duration_ms",
                "last_execution_assets_matched", "last_execution_assets_passed",
                "last_execution_assets_failed"
            ]

            rule_dict = self._deserialize_jsonb_fields(result[0], columns)
            updated_rule = TagRule(**rule_dict)

            logger.info(f"Updated rule: {rule_id}")

            # Auto-execute rule if enabled and rule is still enabled
            if self.auto_execute and updated_rule.enabled and self.execution_service is not None:
                try:
                    logger.info(f"Auto-executing updated rule: {rule_id}")
                    exec_result = self.execution_service.execute_rule(
                        rule_id=rule_id,
                        triggered_by="api"
                    )
                    logger.info(
                        f"Rule {rule_id} executed after update: "
                        f"{exec_result.get('assets_matched', 0)} assets matched"
                    )

                    # Fetch rule again to get updated execution stats
                    # Reason: execute_rule() updates the database, but updated_rule object is stale
                    updated_rule = self.get_rule(rule_id)
                except Exception as exec_error:
                    logger.error(
                        f"Error executing rule after update: {exec_error}",
                        exc_info=True
                    )
                    # Don't fail the update if execution fails
                    pass

            return updated_rule

        except Exception as e:
            logger.error(f"Error updating rule {rule_id}: {e}", exc_info=True)
            raise

    def delete_rule(self, rule_id: str) -> bool:
        """
        Delete a rule by ID.

        This performs a hard delete. All associated tags and history will remain
        but will reference a deleted rule_id.

        Args:
            rule_id: Rule ID to delete

        Returns:
            True if rule was deleted, False if not found

        Example:
            deleted = service.delete_rule("rule_1234567890_abc123")
        """
        try:
            query = """
                DELETE FROM tag_rules
                WHERE id = %(rule_id)s
                RETURNING id
            """

            result = self.db.execute_query(query, {"rule_id": rule_id})

            if not result or len(result) == 0:
                logger.warning(f"Rule not found for deletion: {rule_id}")
                return False

            logger.info(f"Deleted rule: {rule_id}")
            return True

        except Exception as e:
            logger.error(f"Error deleting rule {rule_id}: {e}", exc_info=True)
            raise

    def list_rules(
        self,
        page: int = 1,
        limit: int = 50,
        rule_type: Optional[str] = None,
        enabled_only: bool = False
    ) -> TagRulesList:
        """
        List all rules with pagination and optional filtering.

        Args:
            page: Page number (1-indexed)
            limit: Number of rules per page
            rule_type: Filter by rule type ('compliance' or 'classification')
            enabled_only: If True, only return enabled rules

        Returns:
            TagRulesList with rules and pagination info

        Example:
            rules_list = service.list_rules(page=1, limit=20, enabled_only=True)
            for rule in rules_list.rules:
                print(rule.name)
        """
        try:
            # Build WHERE clause
            where_conditions = []
            params: Dict[str, Any] = {}

            if rule_type:
                where_conditions.append("type = %(rule_type)s")
                params["rule_type"] = rule_type

            if enabled_only:
                where_conditions.append("enabled = TRUE")

            where_clause = ""
            if where_conditions:
                where_clause = "WHERE " + " AND ".join(where_conditions)

            # Get total count
            count_query = f"SELECT COUNT(*) FROM tag_rules {where_clause}"
            count_result = self.db.execute_query(count_query, params)
            total = count_result[0].get('count', 0) if count_result else 0

            # Calculate offset
            offset = (page - 1) * limit
            params["limit"] = limit
            params["offset"] = offset

            # Get rules
            query = f"""
                SELECT id, name, description, type, enabled,
                       target_filters, tags, compliance_criteria, detection_config,
                       created_at, last_modified, last_executed_at,
                       execution_count, last_execution_duration_ms,
                       last_execution_assets_matched, last_execution_assets_passed,
                       last_execution_assets_failed
                FROM tag_rules
                {where_clause}
                ORDER BY created_at DESC
                LIMIT %(limit)s OFFSET %(offset)s
            """

            results = self.db.execute_query(query, params)

            # Column names
            columns = [
                "id", "name", "description", "type", "enabled",
                "target_filters", "tags", "compliance_criteria", "detection_config",
                "created_at", "last_modified", "last_executed_at",
                "execution_count", "last_execution_duration_ms",
                "last_execution_assets_matched", "last_execution_assets_passed",
                "last_execution_assets_failed"
            ]

            # Convert to TagRule objects
            rules = []
            for row in results:
                rule_dict = self._deserialize_jsonb_fields(row, columns)
                rules.append(TagRule(**rule_dict))

            logger.debug(f"Listed {len(rules)} rules (page {page}, limit {limit})")

            return TagRulesList(
                rules=rules,
                total=total,
                page=page,
                limit=limit
            )

        except Exception as e:
            logger.error(f"Error listing rules: {e}", exc_info=True)
            raise

    def enable_rule(self, rule_id: str) -> Optional[TagRule]:
        """
        Enable a rule.

        Args:
            rule_id: Rule ID to enable

        Returns:
            Updated TagRule if found, None otherwise
        """
        return self.update_rule(rule_id, TagRuleUpdate(enabled=True))

    def disable_rule(self, rule_id: str) -> Optional[TagRule]:
        """
        Disable a rule.

        Args:
            rule_id: Rule ID to disable

        Returns:
            Updated TagRule if found, None otherwise
        """
        return self.update_rule(rule_id, TagRuleUpdate(enabled=False))

    def get_rule_statistics(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """
        Get execution statistics for a rule.

        Args:
            rule_id: Rule ID

        Returns:
            Dictionary with statistics or None if rule not found

        Example:
            stats = service.get_rule_statistics("rule_1234567890_abc123")
            # {
            #     "execution_count": 42,
            #     "last_executed_at": "2025-10-31T15:00:00Z",
            #     "avg_execution_time_ms": 2340,
            #     "last_assets_matched": 127
            # }
        """
        rule = self.get_rule(rule_id)
        if not rule:
            return None

        return {
            "execution_count": rule.execution_count,
            "last_executed_at": rule.last_executed_at,
            "last_execution_duration_ms": rule.last_execution_duration_ms,
            "last_assets_matched": rule.last_execution_assets_matched,
            "last_assets_passed": rule.last_execution_assets_passed,
            "last_assets_failed": rule.last_execution_assets_failed
        }


# =============================================================================
# Helper Functions
# =============================================================================

def create_rule(db_adapter: PostgreSQLAdapter, rule_create: TagRuleCreate) -> TagRule:
    """
    Convenience function to create a rule.

    Args:
        db_adapter: Database adapter
        rule_create: Rule creation data

    Returns:
        Created TagRule
    """
    service = RuleManagementService(db_adapter)
    return service.create_rule(rule_create)


def get_rule(db_adapter: PostgreSQLAdapter, rule_id: str) -> Optional[TagRule]:
    """
    Convenience function to get a rule.

    Args:
        db_adapter: Database adapter
        rule_id: Rule ID

    Returns:
        TagRule if found, None otherwise
    """
    service = RuleManagementService(db_adapter)
    return service.get_rule(rule_id)


def list_rules(
    db_adapter: PostgreSQLAdapter,
    page: int = 1,
    limit: int = 50,
    rule_type: Optional[str] = None,
    enabled_only: bool = False
) -> TagRulesList:
    """
    Convenience function to list rules.

    Args:
        db_adapter: Database adapter
        page: Page number
        limit: Results per page
        rule_type: Optional type filter
        enabled_only: Only enabled rules

    Returns:
        TagRulesList
    """
    service = RuleManagementService(db_adapter)
    return service.list_rules(page, limit, rule_type, enabled_only)
