# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for DashboardRepository coverage-trend methods (mocked db)."""
from unittest.mock import MagicMock

from src.modules.dashboard.repositories.dashboard_repository import DashboardRepository


def _repo():
    return DashboardRepository(MagicMock())


class TestGetCoverageTrend:
    def test_builds_carry_forward_query_with_params(self):
        repo = _repo()
        repo.db.execute_query.return_value = [
            {"label": "Jun 01", "added": 5, "removed": 3},
        ]
        out = repo.get_coverage_trend("secured", ["server"], days=7)

        assert out == [{"label": "Jun 01", "added": 5, "removed": 3}]
        sql, params = repo.db.execute_query.call_args[0][:2]
        assert "generate_series" in sql
        assert "LEFT JOIN LATERAL" in sql
        assert "SUM(pass_count)" in sql
        assert "asset_category = ANY(%(cats)s)" in sql
        assert params == {"days": 7, "control": "secured", "cats": ["server"]}

    def test_returns_empty_on_error(self):
        repo = _repo()
        repo.db.execute_query.side_effect = RuntimeError("boom")
        assert repo.get_coverage_trend("secured", ["server"], days=30) == []


class TestGetCoverageCurrent:
    def test_counts_covered_and_total_for_categories(self):
        repo = _repo()
        repo.db.execute_query.return_value = [{"covered": 12, "total": 40}]
        out = repo.get_coverage_current("secured", ["server", "workstation"])

        assert out == {"covered": 12, "total": 40}
        sql, params = repo.db.execute_query.call_args[0][:2]
        assert "-> %(control)s ->> 'state' = 'true'" in sql
        assert "LOWER(TRIM(category))" in sql
        assert params == {"control": "secured", "cats": ["server", "workstation"]}

    def test_returns_zeros_on_error(self):
        repo = _repo()
        repo.db.execute_query.side_effect = RuntimeError("boom")
        assert repo.get_coverage_current("secured", ["server"]) == {"covered": 0, "total": 0}
