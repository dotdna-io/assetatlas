# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Integration Group Service

Business logic layer for integration group management.
"""

import logging
import uuid as uuid_mod
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


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
        return self.group_repository.create(name, integration_type)

    def update_group(self, group_id: str, name: str) -> Optional[Dict[str, Any]]:
        """
        Update integration group name.

        Args:
            group_id: Group UUID as string
            name: New group name

        Returns:
            Updated group dict, or None if not found
        """
        return self.group_repository.update(group_id, name)

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

    def get_group_mapping(self) -> Dict[str, str]:
        """
        Get mapping of integration names to their group names.

        Returns:
            Dict of {integration_name: group_name}
        """
        return self.group_repository.get_group_mapping()
