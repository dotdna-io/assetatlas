# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Integration Group Model

Represents a logical grouping of integrations that share the same type.
Used to treat multiple instances (e.g., 10 Entra tenants) as one logical
source for scoring and reporting.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, List, Optional
from uuid import UUID


@dataclass
class IntegrationGroup:
    """
    Integration group model representing a logical grouping of integrations.

    Maps to the 'integration_groups' database table.
    """

    # Required fields
    name: str
    integration_type: str

    # Optional fields with defaults
    id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    member_count: int = 0
    members: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert integration group to dictionary.

        Returns:
            Dictionary representation
        """
        return {
            'id': str(self.id) if self.id else None,
            'name': self.name,
            'integration_type': self.integration_type,
            'member_count': len(self.members) if self.members else self.member_count,
            'members': self.members,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_db_row(cls, row: Dict[str, Any]) -> 'IntegrationGroup':
        """
        Create IntegrationGroup from database row.

        Args:
            row: Database row as dictionary

        Returns:
            IntegrationGroup instance
        """
        return cls(
            id=row.get('id'),
            name=row['name'],
            integration_type=row['integration_type'],
            created_at=row.get('created_at'),
            updated_at=row.get('updated_at'),
            member_count=row.get('member_count', 0),
        )
