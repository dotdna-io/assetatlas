# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression net for #493: every Reporting wizard chart type's wire
payload must validate against ChartQueryRequest.

Each case mirrors what the UI actually sends to POST /api/charts/query —
i.e. the output of `chartQuestions.generateQuery(answers)` after passing
through `chartDataPipeline.transformToAPIRequest`. If the wizard's
generator drifts from the Pydantic schema again (as in #493), one of these
parametrized cases will fail.

Source files this test guards:
- ui/prod/src/pages/reporting/data/chartQuestions.ts  (per-chart generators)
- ui/prod/src/pages/reporting/data/chartDataPipeline.ts  (transform)
- src/api/blueprints/charts/models.py  (ChartQueryRequest)
"""

import pytest

from src.api.blueprints.charts.models import ChartQueryRequest


def _fid(name):
    """Typed static FieldId dict — what coerceFieldId emits on the wire."""
    return {"kind": "static", "name": name}


# Boilerplate options that transformToAPIRequest always sets.
_BASE_OPTS = {
    "limit": 100,
    "offset": 0,
    "sortOrder": "desc",
    "includeNull": False,
    "calculatePercentage": True,
}


# Per-chart wire payloads. Each case is one (chart, mode) the wizard supports.
WIZARD_PAYLOADS = {
    # ---- distribution template ----
    "donut": {
        "queryType": "distribution",
        "from": {"table": "assets", "joins": []},
        "select": {"dimension": _fid("Category")},
        "filters": [],
        "options": {**_BASE_OPTS, "limit": 8},
    },
    "pie": {
        "queryType": "distribution",
        "from": {"table": "assets", "joins": []},
        "select": {"dimension": _fid("Category")},
        "filters": [],
        "options": {**_BASE_OPTS, "limit": 8},
    },
    "bar_simple": {
        "queryType": "distribution",
        "from": {"table": "assets", "joins": []},
        "select": {"dimension": _fid("Category")},
        "filters": [],
        "options": {**_BASE_OPTS, "limit": 10},
    },
    "force_graph": {
        "queryType": "distribution",
        "from": {"table": "assets", "joins": []},
        "select": {"dimension": _fid("Category")},
        "filters": [],
        "options": {**_BASE_OPTS, "limit": 12},
        # Wizard also emits level1/level2/level3 at root — Pydantic ignores
        # extras, but they need to remain harmless.
        "level1": "static:Category",
        "level2": "static:OS%20Normalized",
        "level3": "static:Data%20Source",
    },

    # ---- barChart grouped + twoDimension stacked ----
    "bar_grouped": {
        "queryType": "comparison",
        "from": {"table": "assets", "joins": []},
        "select": {
            "dimension": _fid("Category"),
            "segment": _fid("OS Normalized"),
        },
        "filters": [],
        "options": {**_BASE_OPTS, "limit": 8, "segmentLimit": 5},
    },
    "stacked_column": {
        "queryType": "comparison",
        "from": {"table": "assets", "joins": []},
        "select": {
            "dimension": _fid("Category"),
            "segment": _fid("OS Normalized"),
        },
        "filters": [],
        "options": {**_BASE_OPTS, "limit": 8, "segmentLimit": 5},
    },

    # ---- correlation (punchcard) ----
    "punchcard": {
        "queryType": "correlation",
        "from": {"table": "assets", "joins": []},
        "select": {
            "sourceField": _fid("Category"),
            "targetField": _fid("OS Normalized"),
        },
        "filters": [],
        "options": {**_BASE_OPTS, "rowLimit": 8, "columnLimit": 8},
    },

    # ---- metric template (#493 root cause) ----
    "metric_percentage": {
        "queryType": "metric",
        "from": {"table": "assets", "joins": []},
        "select": {"metric": {"agg": "COUNT"}},
        "filters": [],
        "comparisonFilters": [],
        "options": {
            **_BASE_OPTS,
            "metricType": "percentage",
            "showPercentage": True,
            "maxValue": 100,
        },
    },
    "metric_filtered": {
        "queryType": "metric",
        "from": {"table": "assets", "joins": []},
        "select": {"metric": {"agg": "COUNT"}},
        "filters": [],
        "comparisonFilters": [],
        "options": {
            **_BASE_OPTS,
            "metricType": "filtered",
            "showPercentage": False,
            "maxValue": 100,
        },
    },
    "metric_distinct": {
        "queryType": "metric",
        "from": {"table": "assets", "joins": []},
        "select": {
            "metric": {"agg": "COUNT"},
            "dimension": _fid("Category"),
        },
        "filters": [],
        "comparisonFilters": [],
        "options": {
            **_BASE_OPTS,
            "metricType": "distinct",
            "showPercentage": False,
            "maxValue": 100,
        },
    },

    # ---- trend template (#493 second root cause; Option A fix) ----
    "trend_count": {
        "queryType": "trend",
        "from": {"table": "assets", "joins": []},
        "select": {"timeField": _fid("Last Seen")},
        "filters": [],
        "options": {
            **_BASE_OPTS,
            "limit": 1000,
            "interval": "day",
            "last": "30d",
            "dimensionLimit": 5,
        },
    },
    "trend_count_with_dimension": {
        "queryType": "trend",
        "from": {"table": "assets", "joins": []},
        "select": {
            "timeField": _fid("Last Seen"),
            "dimension": _fid("Category"),
        },
        "filters": [],
        "options": {
            **_BASE_OPTS,
            "limit": 1000,
            "interval": "week",
            "last": "90d",
            "dimensionLimit": 5,
        },
    },
    "trend_percentage": {
        "queryType": "trend_percentage",
        "from": {"table": "assets", "joins": []},
        "select": {"timeField": _fid("Last Seen")},
        "filters": [],
        "comparisonFilters": [],
        "options": {
            **_BASE_OPTS,
            "limit": 1000,
            "interval": "day",
            "last": "30d",
            "dimensionLimit": 5,
        },
    },

    # ---- history_trend template (Line Chart, Chronicle Bar Chart) ----
    "line_history": {
        "queryType": "history_trend",
        "from": {"table": "assets", "joins": []},
        "select": {},
        "filters": [],
        "options": {**_BASE_OPTS, "interval": "day", "last": "30d", "dimensionLimit": 5},
    },
    "chronicle_bar": {
        "queryType": "history_trend",
        "from": {"table": "assets", "joins": []},
        "select": {"dimension": _fid("Category")},
        "filters": [],
        "options": {**_BASE_OPTS, "interval": "week", "last": "90d", "dimensionLimit": 5},
    },
}


@pytest.mark.parametrize(
    "chart_id,payload",
    list(WIZARD_PAYLOADS.items()),
    ids=list(WIZARD_PAYLOADS.keys()),
)
def test_wizard_payload_validates(chart_id, payload):
    """Wizard wire payload must validate against ChartQueryRequest."""
    ChartQueryRequest(**payload)
