# SPDX-License-Identifier: AGPL-3.0-or-later
"""#946/#497 follow-up: distribution enhancement must honor the wizard's
requested item count, not silently collapse to 6 (the old MAX_SLICES cap)."""
from unittest.mock import MagicMock
from src.modules.charts.repositories.chart_repository import ChartRepository


def _repo():
    return ChartRepository(db_adapter=MagicMock())


def test_distribution_keeps_all_rows_beyond_six():
    # SQL already limited + rolled up "Other"; the enhancer must NOT re-cap.
    rows = [{"label": f"c{i}", "value": 100 - i} for i in range(9)]  # 9 rows
    out = _repo()._enhance_distribution_results(rows)
    assert len(out) == 9, "enhancer must not truncate to 6"
    assert all("percentage" in r and "color" in r for r in out)
    # percentages sum to ~100 over the rows given
    assert abs(sum(r["percentage"] for r in out) - 100.0) < 0.5


def test_distribution_preserves_existing_other_row():
    rows = [{"label": "a", "value": 50}, {"label": "Other", "value": 50}]
    out = _repo()._enhance_distribution_results(rows)
    labels = [r["label"] for r in out]
    assert labels.count("Other") == 1
    assert len(out) == 2
