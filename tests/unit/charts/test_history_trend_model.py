# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from pydantic import ValidationError
from src.api.blueprints.charts.models import ChartQueryRequest, QueryType


def _base(**over):
    payload = {
        "queryType": "history_trend",
        "from": {"table": "assets", "joins": []},
        "select": {},
        "filters": [],
        "options": {"interval": "day", "last": "30d"},
    }
    payload.update(over)
    return payload


def test_history_trend_type_exists():
    assert QueryType.HISTORY_TREND.value == "history_trend"


def test_history_trend_valid_count():
    req = ChartQueryRequest.model_validate(_base())
    assert req.queryType == QueryType.HISTORY_TREND


def test_history_trend_requires_interval():
    p = _base()
    p["options"] = {"last": "30d"}
    with pytest.raises(ValidationError, match="interval"):
        ChartQueryRequest.model_validate(p)


def test_history_trend_allows_split_dimension():
    p = _base()
    p["select"] = {"dimension": {"kind": "static", "name": "Category"}}
    req = ChartQueryRequest.model_validate(p)
    assert req.select.dimension is not None
