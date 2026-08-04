# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Integration Model

Represents a configured integration instance in the system.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional
from uuid import UUID


class IntegrationStatus(str, Enum):
    """Integration status enumeration"""
    ENABLED = "enabled"
    DISABLED = "disabled"
    ERROR = "error"


@dataclass
class Integration:
    """
    Integration model representing a configured data source integration.

    Maps to the 'integrations' database table.
    """

    # Required fields
    name: str
    integration_type: str
    category: str
    config: Dict[str, Any]
    field_mapping: Dict[str, Any]
    transformations: Dict[str, Any] = None  # Field transformation definitions (separate from mappings)
    data_filters: list = field(default_factory=list)  # Data filtering rules applied during ingestion
    preserve_fields: list = field(default_factory=list)  # Data-source-specific (DSS) field names; wizard source-of-truth for DSS-vs-mapping classification (#809)

    # Optional fields with defaults
    id: Optional[UUID] = None
    status: IntegrationStatus = IntegrationStatus.ENABLED
    logo: Optional[str] = None
    description: Optional[str] = None
    last_sync_at: Optional[datetime] = None
    last_sync_status: Optional[str] = None
    last_error: Optional[str] = None
    sync_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Schedule fields
    schedule_enabled: bool = False
    schedule_cron: Optional[str] = None
    schedule_timezone: str = 'UTC'
    next_run_at: Optional[datetime] = None
    is_running: bool = False
    source_method: str = 'direct'

    # Connection test fields
    last_connection_test_at: Optional[datetime] = None
    last_connection_test_status: Optional[str] = None
    last_connection_test_message: Optional[str] = None

    # Group fields
    group_id: Optional[UUID] = None
    group_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert integration to dictionary.

        Returns:
            Dictionary representation
        """
        return {
            'id': str(self.id) if self.id else None,
            'name': self.name,
            'integration_type': self.integration_type,
            'category': self.category,
            'status': self.status.value if isinstance(self.status, IntegrationStatus) else self.status,
            'logo': self.logo,
            'description': self.description,
            'config': self.config,
            'field_mapping': self.field_mapping,
            'transformations': self.transformations if self.transformations else {},
            'data_filters': self.data_filters if self.data_filters else [],
            'preserve_fields': self.preserve_fields or [],
            'last_sync_at': self.last_sync_at.isoformat() if self.last_sync_at else None,
            'last_sync_status': self.last_sync_status,
            'last_error': self.last_error,
            'sync_count': self.sync_count,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'schedule_enabled': self.schedule_enabled,
            'schedule_cron': self.schedule_cron,
            'schedule_timezone': self.schedule_timezone,
            'next_run_at': self.next_run_at.isoformat() if self.next_run_at else None,
            'is_running': self.is_running,
            'source_method': self.source_method,
            'last_connection_test_at': self.last_connection_test_at.isoformat() if self.last_connection_test_at else None,
            'last_connection_test_status': self.last_connection_test_status,
            'last_connection_test_message': self.last_connection_test_message,
            'group_id': str(self.group_id) if self.group_id else None,
            'group_name': self.group_name,
        }

    @classmethod
    def from_db_row(cls, row: Dict[str, Any]) -> 'Integration':
        """
        Create Integration from database row.

        Args:
            row: Database row as dictionary

        Returns:
            Integration instance
        """
        # Convert status string to enum if needed
        status = row.get('status', 'enabled')
        if isinstance(status, str):
            status = IntegrationStatus(status)

        return cls(
            id=row.get('id'),
            name=row['name'],
            integration_type=row['integration_type'],
            category=row['category'],
            status=status,
            logo=row.get('logo'),
            description=row.get('description'),
            config=row['config'],
            field_mapping=row['field_mapping'],
            transformations=row.get('transformations', {}),
            data_filters=row.get('data_filters', []),
            preserve_fields=row.get('preserve_fields', []),
            last_sync_at=row.get('last_sync_at'),
            last_sync_status=row.get('last_sync_status'),
            last_error=row.get('last_error'),
            sync_count=row.get('sync_count', 0),
            created_at=row.get('created_at'),
            updated_at=row.get('updated_at'),
            schedule_enabled=row.get('schedule_enabled', False),
            schedule_cron=row.get('schedule_cron'),
            schedule_timezone=row.get('schedule_timezone', 'UTC'),
            next_run_at=row.get('next_run_at'),
            is_running=row.get('is_running', False),
            source_method=row.get('source_method', 'direct'),
            last_connection_test_at=row.get('last_connection_test_at'),
            last_connection_test_status=row.get('last_connection_test_status'),
            last_connection_test_message=row.get('last_connection_test_message'),
            group_id=row.get('group_id'),
            group_name=row.get('group_name'),
        )

    def is_enabled(self) -> bool:
        """Check if integration is enabled"""
        return self.status == IntegrationStatus.ENABLED

    def has_error(self) -> bool:
        """Check if integration has errors"""
        return self.status == IntegrationStatus.ERROR or self.last_error is not None

    def is_scheduled(self) -> bool:
        """Check if integration has scheduling enabled"""
        return self.schedule_enabled and self.schedule_cron is not None

    def can_run(self) -> bool:
        """Check if integration can be run (enabled and not currently running)"""
        return self.is_enabled() and not self.is_running

    @property
    def effective_connector_type(self) -> str:
        """The connector type to instantiate: transport when via SIEM, otherwise the technology itself."""
        if self.source_method and self.source_method != 'direct':
            return self.source_method
        return self.integration_type
