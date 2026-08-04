# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Reports Service

Business logic for system-wide report management operations.
"""

from typing import Optional, List
import logging
from datetime import datetime

from src.modules.reports.repositories.reports_repository import ReportsRepository
from src.api.models.requests import ReportCreateRequest, ReportUpdateRequest, ReportListParams
from src.api.models.responses import (
    ReportDetail,
    ReportSummary,
    ReportListResponse
)

logger = logging.getLogger(__name__)


class ReportsService:
    """Service for system-wide report management operations"""

    def __init__(self, repository: ReportsRepository):
        """
        Initialize service with repository.

        Args:
            repository: Reports repository instance
        """
        self.repo = repository

    def create_report(
        self,
        request: ReportCreateRequest,
        created_by: str
    ) -> ReportDetail:
        """
        Create a new system-wide report.

        Args:
            request: Report creation request
            created_by: UUID of user creating the report (for audit)

        Returns:
            Created report detail
        """
        logger.info(f"Creating report: {request.title} (created by {created_by})")

        # Create report in database
        report_data = self.repo.create_report(
            title=request.title,
            description=request.description,
            charts=request.charts,
            created_by=created_by
        )

        if not report_data:
            raise Exception("Failed to create report")

        # Convert to response model
        return ReportDetail(
            id=str(report_data['id']),
            title=report_data['title'],
            description=report_data['description'],
            charts=report_data['charts'],
            is_default=report_data.get('is_default', False),
            created_at=report_data['created_at'],
            updated_at=report_data['updated_at'],
            created_by=str(report_data['created_by']) if report_data.get('created_by') else None,
            updated_by=str(report_data['updated_by']) if report_data.get('updated_by') else None
        )

    def get_report(self, report_id: str) -> Optional[ReportDetail]:
        """
        Get a specific system-wide report.

        Args:
            report_id: Report UUID

        Returns:
            Report detail or None if not found
        """
        logger.info(f"Fetching report {report_id}")

        report_data = self.repo.get_report_by_id(report_id)

        if not report_data:
            return None

        return ReportDetail(
            id=str(report_data['id']),
            title=report_data['title'],
            description=report_data['description'],
            charts=report_data['charts'],
            is_default=report_data.get('is_default', False),
            created_at=report_data['created_at'],
            updated_at=report_data['updated_at'],
            created_by=str(report_data['created_by']) if report_data.get('created_by') else None,
            updated_by=str(report_data['updated_by']) if report_data.get('updated_by') else None
        )

    def get_default_report(self) -> Optional[ReportDetail]:
        """
        Get the default report.

        Returns:
            Default report detail or None if not found
        """
        logger.info("Fetching default report")

        report_data = self.repo.get_default_report()

        if not report_data:
            return None

        return ReportDetail(
            id=str(report_data['id']),
            title=report_data['title'],
            description=report_data['description'],
            charts=report_data['charts'],
            is_default=report_data.get('is_default', False),
            created_at=report_data['created_at'],
            updated_at=report_data['updated_at'],
            created_by=str(report_data['created_by']) if report_data.get('created_by') else None,
            updated_by=str(report_data['updated_by']) if report_data.get('updated_by') else None
        )

    def list_reports(
        self,
        params: ReportListParams
    ) -> ReportListResponse:
        """
        List all system-wide reports.

        Args:
            params: List query parameters

        Returns:
            List of reports with pagination
        """
        logger.info(f"Listing reports (page {params.page}, limit {params.limit})")

        reports_data, total = self.repo.list_reports(
            page=params.page,
            limit=params.limit,
            search=params.search
        )

        # Convert to response models
        reports = [
            ReportSummary(
                id=str(report['id']),
                title=report['title'],
                description=report['description'],
                chart_count=report['chart_count'],
                charts=report.get('charts', []),
                is_default=report.get('is_default', False),
                created_at=report['created_at'],
                updated_at=report['updated_at'],
                created_by=str(report['created_by']) if report.get('created_by') else None,
                updated_by=str(report['updated_by']) if report.get('updated_by') else None
            )
            for report in reports_data
        ]

        has_more = (params.page * params.limit) < total

        return ReportListResponse(
            reports=reports,
            total=total,
            page=params.page,
            limit=params.limit,
            has_more=has_more
        )

    def update_report(
        self,
        report_id: str,
        request: ReportUpdateRequest,
        updated_by: str
    ) -> Optional[ReportDetail]:
        """
        Update an existing system-wide report.

        Args:
            report_id: Report UUID
            request: Report update request
            updated_by: UUID of user updating the report (for audit)

        Returns:
            Updated report detail or None if not found
        """
        logger.info(f"Updating report {report_id} (updated by {updated_by})")

        report_data = self.repo.update_report(
            report_id=report_id,
            updated_by=updated_by,
            title=request.title,
            description=request.description,
            charts=request.charts
        )

        if not report_data:
            return None

        return ReportDetail(
            id=str(report_data['id']),
            title=report_data['title'],
            description=report_data['description'],
            charts=report_data['charts'],
            is_default=report_data.get('is_default', False),
            created_at=report_data['created_at'],
            updated_at=report_data['updated_at'],
            created_by=str(report_data['created_by']) if report_data.get('created_by') else None,
            updated_by=str(report_data['updated_by']) if report_data.get('updated_by') else None
        )

    def clone_report(self, report_id: str, created_by: str) -> Optional[ReportDetail]:
        """
        Clone an existing report.

        Args:
            report_id: UUID of the report to clone
            created_by: UUID of user creating the clone (for audit)

        Returns:
            Cloned report detail or None if source not found
        """
        logger.info(f"Cloning report {report_id} (created by {created_by})")

        source = self.repo.get_report_by_id(report_id)
        if source is None:
            return None

        # Reuse existing create method
        report_data = self.repo.create_report(
            title=f"Copy of {source['title']}",
            description=source.get('description'),
            charts=source.get('charts', []),
            created_by=created_by,
        )

        if not report_data:
            raise Exception("Failed to clone report")

        # Convert to response model (same pattern as create_report)
        return ReportDetail(
            id=str(report_data['id']),
            title=report_data['title'],
            description=report_data['description'],
            charts=report_data['charts'],
            is_default=report_data.get('is_default', False),
            created_at=report_data['created_at'],
            updated_at=report_data['updated_at'],
            created_by=str(report_data['created_by']) if report_data.get('created_by') else None,
            updated_by=str(report_data['updated_by']) if report_data.get('updated_by') else None
        )

    def delete_report(self, report_id: str) -> bool:
        """
        Delete a system-wide report.

        Args:
            report_id: Report UUID

        Returns:
            True if deleted, False if not found
        """
        logger.info(f"Deleting report {report_id}")

        return self.repo.delete_report(report_id)
