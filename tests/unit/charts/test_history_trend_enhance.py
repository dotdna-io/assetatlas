# SPDX-License-Identifier: AGPL-3.0-or-later
from unittest.mock import MagicMock
from src.modules.charts.repositories.chart_repository import ChartRepository


def test_history_trend_multiseries_gets_colors():
    repo = ChartRepository(db_adapter=MagicMock())
    rows = [
        {"timestamp": "2026-01-01", "series": "A", "value": 3},
        {"timestamp": "2026-01-01", "series": "B", "value": 5},
    ]
    out = repo.transform_results(rows, "history_trend")
    assert all("color" in r for r in out)
