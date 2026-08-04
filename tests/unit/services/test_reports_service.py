# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for ReportsService

Tests create_report, get_report, get_default_report, list_reports,
update_report, delete_report, and default-report protection.
"""

from unittest.mock import MagicMock
from datetime import datetime
import pytest

from src.modules.reports.services.reports_service import ReportsService
from src.api.models.requests import ReportCreateRequest, ReportUpdateRequest, ReportListParams
from src.api.models.responses import ReportDetail, ReportListResponse


def _report_row(**overrides):
    """Build a fake report DB row dict."""
    defaults = {
        "id": "rpt-001",
        "title": "Asset Overview",
        "description": "Desc",
        "charts": [{"chartId": "c1", "name": "Chart 1"}],
        "is_default": False,
        "created_at": datetime(2025, 6, 1),
        "updated_at": datetime(2025, 6, 2),
        "created_by": "user-1",
        "updated_by": "user-1",
    }
    defaults.update(overrides)
    return defaults


class TestReportsService:
    """Tests for ReportsService."""

    @pytest.fixture
    def service(self):
        repo = MagicMock()
        svc = ReportsService(repository=repo)
        return svc, repo

    # ------------------------------------------------------------------
    # create_report
    # ------------------------------------------------------------------
    def test_create_report_success(self, service):
        svc, repo = service
        repo.create_report.return_value = _report_row()

        request = ReportCreateRequest(
            title="Asset Overview",
            description="Desc",
            charts=[{"chartId": "c1", "name": "Chart 1"}],
        )

        result = svc.create_report(request, created_by="user-1")

        assert isinstance(result, ReportDetail)
        assert result.id == "rpt-001"
        assert result.title == "Asset Overview"
        assert result.is_default is False
        repo.create_report.assert_called_once_with(
            title="Asset Overview",
            description="Desc",
            charts=[{"chartId": "c1", "name": "Chart 1"}],
            created_by="user-1",
        )

    def test_create_report_failure_raises(self, service):
        svc, repo = service
        repo.create_report.return_value = None

        request = ReportCreateRequest(
            title="Bad Report",
            description=None,
            charts=[{"chartId": "c1", "name": "Chart 1"}],
        )

        with pytest.raises(Exception, match="Failed to create report"):
            svc.create_report(request, created_by="user-1")

    # ------------------------------------------------------------------
    # get_report
    # ------------------------------------------------------------------
    def test_get_report_found(self, service):
        svc, repo = service
        repo.get_report_by_id.return_value = _report_row()

        result = svc.get_report("rpt-001")

        assert isinstance(result, ReportDetail)
        assert result.id == "rpt-001"
        assert result.charts == [{"chartId": "c1", "name": "Chart 1"}]
        repo.get_report_by_id.assert_called_once_with("rpt-001")

    def test_get_report_not_found(self, service):
        svc, repo = service
        repo.get_report_by_id.return_value = None

        result = svc.get_report("rpt-missing")

        assert result is None

    # ------------------------------------------------------------------
    # get_default_report
    # ------------------------------------------------------------------
    def test_get_default_report_found(self, service):
        svc, repo = service
        repo.get_default_report.return_value = _report_row(is_default=True)

        result = svc.get_default_report()

        assert isinstance(result, ReportDetail)
        assert result.is_default is True

    def test_get_default_report_not_found(self, service):
        svc, repo = service
        repo.get_default_report.return_value = None

        result = svc.get_default_report()

        assert result is None

    # ------------------------------------------------------------------
    # list_reports
    # ------------------------------------------------------------------
    def test_list_reports_with_results(self, service):
        svc, repo = service
        rows = [
            {
                "id": "rpt-001",
                "title": "Report A",
                "description": "Desc A",
                "chart_count": 3,
                "is_default": True,
                "created_at": datetime(2025, 6, 1),
                "updated_at": datetime(2025, 6, 2),
                "created_by": "user-1",
                "updated_by": None,
            },
            {
                "id": "rpt-002",
                "title": "Report B",
                "description": None,
                "chart_count": 0,
                "is_default": False,
                "created_at": datetime(2025, 6, 3),
                "updated_at": datetime(2025, 6, 3),
                "created_by": None,
                "updated_by": None,
            },
        ]
        repo.list_reports.return_value = (rows, 2)

        params = ReportListParams(page=1, limit=10)
        result = svc.list_reports(params)

        assert isinstance(result, ReportListResponse)
        assert result.total == 2
        assert len(result.reports) == 2
        assert result.reports[0].title == "Report A"
        assert result.reports[0].is_default is True
        assert result.has_more is False

    def test_list_reports_pagination_has_more(self, service):
        svc, repo = service
        row = {
            "id": "rpt-001",
            "title": "R",
            "description": None,
            "chart_count": 1,
            "is_default": False,
            "created_at": datetime(2025, 1, 1),
            "updated_at": datetime(2025, 1, 1),
            "created_by": None,
            "updated_by": None,
        }
        repo.list_reports.return_value = ([row], 25)

        params = ReportListParams(page=1, limit=10)
        result = svc.list_reports(params)

        assert result.has_more is True
        assert result.total == 25

    def test_list_reports_empty(self, service):
        svc, repo = service
        repo.list_reports.return_value = ([], 0)

        params = ReportListParams(page=1, limit=50)
        result = svc.list_reports(params)

        assert result.total == 0
        assert result.reports == []
        assert result.has_more is False

    def test_list_reports_search(self, service):
        svc, repo = service
        repo.list_reports.return_value = ([], 0)

        params = ReportListParams(page=1, limit=10, search="compliance")
        svc.list_reports(params)

        repo.list_reports.assert_called_once_with(page=1, limit=10, search="compliance")

    # ------------------------------------------------------------------
    # update_report
    # ------------------------------------------------------------------
    def test_update_report_success(self, service):
        svc, repo = service
        repo.update_report.return_value = _report_row(title="Updated Title")

        request = ReportUpdateRequest(title="Updated Title")
        result = svc.update_report("rpt-001", request, updated_by="user-2")

        assert isinstance(result, ReportDetail)
        assert result.title == "Updated Title"
        repo.update_report.assert_called_once_with(
            report_id="rpt-001",
            updated_by="user-2",
            title="Updated Title",
            description=None,
            charts=None,
        )

    def test_update_report_not_found(self, service):
        svc, repo = service
        repo.update_report.return_value = None

        request = ReportUpdateRequest(title="X")
        result = svc.update_report("rpt-missing", request, updated_by="user-2")

        assert result is None

    # ------------------------------------------------------------------
    # delete_report
    # ------------------------------------------------------------------
    def test_delete_report_success(self, service):
        svc, repo = service
        repo.delete_report.return_value = True

        assert svc.delete_report("rpt-001") is True
        repo.delete_report.assert_called_once_with("rpt-001")

    def test_delete_report_not_found(self, service):
        svc, repo = service
        repo.delete_report.return_value = False

        assert svc.delete_report("rpt-missing") is False

    def test_delete_default_report_raises(self, service):
        """Default report protection lives in the repository.

        The service delegates directly to repo.delete_report(), so if the
        repository raises ValueError the service propagates it.
        """
        svc, repo = service
        repo.delete_report.side_effect = ValueError("Cannot delete the default report")

        with pytest.raises(ValueError, match="Cannot delete the default report"):
            svc.delete_report("rpt-default")

    # ------------------------------------------------------------------
    # clone_report
    # ------------------------------------------------------------------
    def test_clone_report_success(self, service):
        svc, repo = service
        source = _report_row(
            id="rpt-src",
            title="Asset Overview",
            description="Original description",
            charts=[{"chartId": "c1", "name": "Chart 1"}],
        )
        cloned = _report_row(
            id="rpt-new",
            title="Copy of Asset Overview",
            description="Original description",
            charts=[{"chartId": "c1", "name": "Chart 1"}],
            created_by="user-2",
            updated_by="user-2",
        )
        repo.get_report_by_id.return_value = source
        repo.create_report.return_value = cloned

        result = svc.clone_report("rpt-src", created_by="user-2")

        assert isinstance(result, ReportDetail)
        assert result.id == "rpt-new"
        assert result.title == "Copy of Asset Overview"
        assert result.is_default is False
        repo.get_report_by_id.assert_called_once_with("rpt-src")
        repo.create_report.assert_called_once_with(
            title="Copy of Asset Overview",
            description="Original description",
            charts=[{"chartId": "c1", "name": "Chart 1"}],
            created_by="user-2",
        )

    def test_clone_report_source_not_found(self, service):
        svc, repo = service
        repo.get_report_by_id.return_value = None

        result = svc.clone_report("rpt-missing", created_by="user-2")

        assert result is None
        repo.create_report.assert_not_called()

    def test_clone_report_preserves_empty_description(self, service):
        svc, repo = service
        source = _report_row(description=None, charts=[])
        repo.get_report_by_id.return_value = source
        repo.create_report.return_value = _report_row(
            title="Copy of Asset Overview", description=None, charts=[]
        )

        svc.clone_report("rpt-src", created_by="user-2")

        repo.create_report.assert_called_once_with(
            title="Copy of Asset Overview",
            description=None,
            charts=[],
            created_by="user-2",
        )

    def test_clone_of_default_report_is_not_default(self, service):
        """Cloning the default report produces a non-default copy."""
        svc, repo = service
        source = _report_row(id="rpt-default", title="Default", is_default=True)
        repo.get_report_by_id.return_value = source
        repo.create_report.return_value = _report_row(
            id="rpt-new",
            title="Copy of Default",
            is_default=False,
        )

        result = svc.clone_report("rpt-default", created_by="user-2")

        assert result.is_default is False

    def test_clone_report_create_failure_raises(self, service):
        svc, repo = service
        repo.get_report_by_id.return_value = _report_row()
        repo.create_report.return_value = None

        with pytest.raises(Exception, match="Failed to clone report"):
            svc.clone_report("rpt-src", created_by="user-2")

    # ------------------------------------------------------------------
    # Edge cases — created_by / updated_by as None
    # ------------------------------------------------------------------
    def test_report_detail_null_audit_fields(self, service):
        svc, repo = service
        repo.get_report_by_id.return_value = _report_row(created_by=None, updated_by=None)

        result = svc.get_report("rpt-001")

        assert result.created_by is None
        assert result.updated_by is None
