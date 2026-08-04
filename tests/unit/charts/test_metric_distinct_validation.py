# SPDX-License-Identifier: AGPL-3.0-or-later
"""distinct metric mode must require a field to count (else it silently
mis-routes to COUNT(*) in _build_metric_query)."""
import pytest
from pydantic import ValidationError
from src.api.blueprints.charts.models import ChartQueryRequest


def _base(**opts):
    return {
        "queryType": "metric",
        "from": {"table": "assets", "joins": []},
        "select": {"metric": {"agg": "COUNT"}},
        "filters": [],
        "options": {"metricType": "distinct", **opts},
    }


def test_distinct_without_dimension_rejected():
    with pytest.raises(ValidationError, match="distinct"):
        ChartQueryRequest.model_validate(_base())


def test_distinct_with_dimension_ok():
    payload = _base()
    payload["select"]["dimension"] = {"kind": "static", "name": "OS Normalized"}
    req = ChartQueryRequest.model_validate(payload)
    assert req.options.metricType == "distinct"
