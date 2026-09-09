# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Integration Group Service

Business logic layer for integration group management.
"""

import logging
import uuid as uuid_mod
from typing import Dict, Any, List, Optional

from src.modules.fields.unified.materialize import (
    outside_request_scope, refresh_for_sources,
)
from src.modules.fields.unified.repository import UnifiedFieldsRepository
from src.modules.shared import asset_data_version
from src.modules.shared.field_resolver_factory import build_field_resolver

logger = logging.getLogger(__name__)

# Encoded prefix of integration-group field sources (group:<group>:<attr>).
_GROUP_SOURCE_PREFIX = "group:"


class IntegrationGroupService:
    """
    Service layer for integration group management.

    Handles validation and orchestration for grouping integrations.
    """

    def __init__(self, group_repository, integration_repository):
        """
        Initialize service.

        Args:
            group_repository: IntegrationGroupRepository instance
            integration_repository: IntegrationRepository instance (for member validation)
        """
        self.group_repository = group_repository
        self.integration_repository = integration_repository

    def _refresh_group_sourced_values(self) -> None:
        """Re-materialize unified fields with a ``group:`` source after a group
        mutation changed what those sources resolve to. Best-effort: the helper
        clears the affected stamps on failure (resolver falls back to live
        SQL), so a refresh problem never fails the mutation. Runs outside the
        request scope so the mutation commits first — a refresh DB-error can
        then neither roll it back nor poison its commit.

        The resolver is built DB-direct (not the app-scoped, cache-primed one):
        the 300s group-mapping cache is invalidated by the CALLER after this
        service returns, and a refresh built on the stale cached mapping would
        bake — and stamp — values from the pre-mutation membership.

        Also bumps the asset-data version stamp (#1410): a group mutation
        changes what every ``group:`` field's values are, so the Explorer
        field-values cache must invalidate. Placed after the refresh block,
        outside the request scope, so a Redis error can never roll the
        mutation back (bump() swallows everything anyway).
        """
        db = self.group_repository.db
        with outside_request_scope(db.conn_manager):
            try:
                defs = UnifiedFieldsRepository(db).list_for_catalog()
                refresh_for_sources(db, build_field_resolver(db), defs, (_GROUP_SOURCE_PREFIX,))
            except Exception:
                logger.warning(
                    "materialized refresh of group-sourced unified fields failed "
                    "after an integration-group change — stored values stay as "
                    "they are until the next pipeline run", exc_info=True)
        asset_data_version.bump()

    def list_groups(self) -> List[Dict[str, Any]]:
        """
        List all integration groups.

        Returns:
            List of group dicts
        """
        return self.group_repository.list_all()

    def get_group(self, group_id: str) -> Optional[Dict[str, Any]]:
        """
        Get integration group by ID.

        Args:
            group_id: Group UUID as string

        Returns:
            Group dict with members, or None if not found
        """
        return self.group_repository.get_by_id(group_id)

    def create_group(self, name: str, integration_type: str) -> Dict[str, Any]:
        """
        Create a new integration group.

        Args:
            name: Group name
            integration_type: Integration type for this group

        Returns:
            Created group dict
        """
        group = self.group_repository.create(name, integration_type)
        self._refresh_group_sourced_values()
        return group

    def update_group(self, group_id: str, name: str) -> Optional[Dict[str, Any]]:
        """
        Update integration group name.

        Args:
            group_id: Group UUID as string
            name: New group name

        Returns:
            Updated group dict, or None if not found
        """
        group = self.group_repository.update(group_id, name)
        if group is not None:
            self._refresh_group_sourced_values()
        return group

    def delete_group(self, group_id: str) -> None:
        """
        Delete integration group.

        Args:
            group_id: Group UUID as string

        Raises:
            ValueError: If group does not exist
        """
        group = self.group_repository.get_by_id(group_id)
        if not group:
            raise ValueError(f"Integration group not found: {group_id}")
        self.group_repository.delete(group_id)
        self._refresh_group_sourced_values()

    def add_members(self, group_id: str, integration_ids: List[str]) -> None:
        """
        Add integrations to a group.

        Validates that:
        - The group exists
        - Each integration exists
        - Each integration's type matches the group's type
        - Each integration is not already in a group

        Args:
            group_id: Group UUID as string
            integration_ids: List of integration UUID strings

        Raises:
            ValueError: On any validation failure
        """
        group = self.group_repository.get_by_id(group_id)
        if not group:
            raise ValueError(f"Integration group not found: {group_id}")

        for int_id in integration_ids:
            integration = self.integration_repository.get_integration(uuid_mod.UUID(int_id))
            if not integration:
                raise ValueError(f"Integration not found: {int_id}")

            int_type = integration.integration_type
            existing_group = integration.group_id

            if int_type != group['integration_type']:
                raise ValueError(
                    f"Integration type mismatch: integration {int_id} "
                    f"is '{int_type}', group requires '{group['integration_type']}'"
                )

            if existing_group is not None:
                raise ValueError(
                    f"Integration {int_id} is already in a group"
                )

        self.group_repository.add_members(group_id, integration_ids)
        self._refresh_group_sourced_values()

    def remove_member(self, group_id: str, integration_id: str) -> None:
        """
        Remove an integration from a group.

        Args:
            group_id: Group UUID as string
            integration_id: Integration UUID as string

        Raises:
            ValueError: If group does not exist
        """
        group = self.group_repository.get_by_id(group_id)
        if not group:
            raise ValueError(f"Integration group not found: {group_id}")
        self.group_repository.remove_member(group_id, integration_id)
        self._refresh_group_sourced_values()

    def get_group_mapping(self) -> Dict[str, str]:
        """
        Get mapping of integration names to their group names.

        Returns:
            Dict of {integration_name: group_name}
        """
        return self.group_repository.get_group_mapping()
