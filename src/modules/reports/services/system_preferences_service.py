# SPDX-License-Identifier: AGPL-3.0-or-later
"""
System Preferences Service

Business logic for system-wide dashboard preferences operations.
"""

from typing import Optional
from datetime import datetime
import logging

from src.modules.reports.repositories.system_preferences_repository import (
    SystemPreferencesRepository,
    ConflictError
)
from src.api.models.requests import DashboardUpdateRequest
from src.api.models.responses import DashboardResponse

logger = logging.getLogger(__name__)


class DashboardConflictError(Exception):
    """Raised when dashboard save conflicts with another user's changes"""
    def __init__(self, message: str, current_last_modified: datetime):
        super().__init__(message)
        self.current_last_modified = current_last_modified


class SystemPreferencesService:
    """Service for system-wide dashboard preferences operations"""

    def __init__(self, repository: SystemPreferencesRepository):
        """
        Initialize service with repository.

        Args:
            repository: System preferences repository instance
        """
        self.repo = repository

    def get_dashboard(self) -> DashboardResponse:
        """
        Get system-wide working dashboard.

        Returns:
            Dashboard response
        """
        logger.info("Fetching system-wide dashboard")

        dashboard_data = self.repo.get_dashboard()

        # Extract charts from working_dashboard JSONB
        working_dashboard = dashboard_data['working_dashboard']
        charts = working_dashboard.get('charts', []) if working_dashboard else []

        return DashboardResponse(
            charts=charts,
            last_modified=dashboard_data['last_modified']
        )

    def update_dashboard(
        self,
        request: DashboardUpdateRequest,
        modified_by: str
    ) -> DashboardResponse:
        """
        Update system-wide working dashboard with conflict detection.

        Args:
            request: Dashboard update request (includes charts and expected_last_modified)
            modified_by: UUID of user saving changes (for audit)

        Returns:
            Updated dashboard response

        Raises:
            DashboardConflictError: If dashboard was modified by another user
        """
        logger.info(f"Updating system-wide dashboard with {len(request.charts)} charts (modified by {modified_by})")

        try:
            dashboard_data = self.repo.update_dashboard(
                charts=request.charts,
                modified_by=modified_by,
                expected_last_modified=request.expected_last_modified
            )
        except ConflictError as e:
            # Re-raise as service-level exception
            raise DashboardConflictError(str(e), e.current_last_modified)

        if not dashboard_data:
            raise Exception("Failed to update dashboard")

        # Extract charts from working_dashboard JSONB
        working_dashboard = dashboard_data['working_dashboard']
        charts = working_dashboard.get('charts', []) if working_dashboard else []

        return DashboardResponse(
            charts=charts,
            last_modified=dashboard_data['last_modified']
        )

    def reset_dashboard(self) -> DashboardResponse:
        """
        Reset system-wide working dashboard to empty state.

        Returns:
            Reset dashboard response
        """
        logger.info("Resetting system-wide dashboard")

        dashboard_data = self.repo.reset_dashboard()

        # Extract charts from working_dashboard JSONB
        working_dashboard = dashboard_data['working_dashboard']
        charts = working_dashboard.get('charts', []) if working_dashboard else []

        return DashboardResponse(
            charts=charts,
            last_modified=dashboard_data['last_modified']
        )
