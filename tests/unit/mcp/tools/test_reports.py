# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the reports MCP tools."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from src.mcp.tools.reports import get_report_impl, list_reports_impl


def _report_row(**overrides):
    base = {
        "id": str(uuid4()),
        "title": "Quarterly Posture",
        "description": "Posture across all servers",
        "updated_at": "2026-04-20T10:00:00Z",
        "chart_count": 2,
        "author": "alice",
    }
    base.update(overrides)
    return base


def _params_of(call):
    return call.args[1] if len(call.args) > 1 else call.kwargs.get("params")


def test_list_reports_renders_all_with_real_total():
    db = MagicMock()
    rows = [
        _report_row(title="Quarterly Posture"),
        _report_row(title="Firewall Coverage", description=None),
        _report_row(title="EOL Devices", description="x" * 200),
    ]
    # A real COUNT runs first (§4 "real totals"), then the paginated SELECT —
    # the LIMIT+1 trick could say "more exist" but never how many.
    db.execute_query.side_effect = [[{"total": 3}], rows]

    out = list_reports_impl(db)

    assert "Quarterly Posture" in out
    assert "Firewall Coverage" in out
    assert "EOL Devices" in out
    assert "3 total, showing 3" in out
    # COUNT + SELECT — a real total query now runs (was 1).
    assert db.execute_query.call_count == 2
    assert "no description" in out
    assert "by alice" in out


def test_list_reports_overflow_reports_true_remaining_count():
    db = MagicMock()
    # Total 47, page of 2 → 45 genuinely remain (not just "+").
    rows = [_report_row(title=f"Report {i}") for i in range(2)]
    db.execute_query.side_effect = [[{"total": 47}], rows]

    out = list_reports_impl(db, limit=2)
    assert "47 total, showing 2" in out
    assert "45 more" in out


def test_list_reports_search_passes_ilike_param():
    db = MagicMock()
    db.execute_query.side_effect = [[{"total": 0}], []]

    list_reports_impl(db, search="firewall")

    # Both COUNT and SELECT carry the ILIKE param.
    assert _params_of(db.execute_query.call_args_list[0])["q"] == "%firewall%"


def test_list_reports_author_filter_joins_users():
    db = MagicMock()
    db.execute_query.side_effect = [[{"total": 0}], []]

    list_reports_impl(db, author="bob")

    count_call = db.execute_query.call_args_list[0]
    sql = count_call.args[0]
    assert "JOIN users" in sql
    assert "u.username ILIKE %(author)s" in sql
    assert _params_of(count_call)["author"] == "%bob%"


def test_list_reports_bad_sort_is_loud():
    # An unknown sort must error (not silently fall back to a different order —
    # a different order is a different answer, §4).
    out = list_reports_impl(MagicMock(), sort="sideways")
    assert "Unknown sort" in out


def test_list_reports_bad_sort_dir_is_loud():
    out = list_reports_impl(MagicMock(), sort_dir="zigzag")
    assert "Unknown sort_dir" in out


def test_list_reports_sort_column_whitelisted_in_sql():
    db = MagicMock()
    db.execute_query.side_effect = [[{"total": 0}], []]
    list_reports_impl(db, sort="created", sort_dir="asc")
    select_sql = db.execute_query.call_args_list[1].args[0]
    assert "ORDER BY r.created_at ASC" in select_sql


def test_list_reports_empty():
    db = MagicMock()
    db.execute_query.side_effect = [[{"total": 0}], []]
    out = list_reports_impl(db)
    assert "No reports found." in out


def test_list_reports_pagination_offset_and_limit():
    db = MagicMock()
    db.execute_query.side_effect = [[{"total": 0}], []]

    list_reports_impl(db, page=2, limit=10)

    # No LIMIT+1 — a real COUNT detects overflow, so the page asks for exactly
    # `limit` rows at the right offset.
    params = _params_of(db.execute_query.call_args_list[1])
    assert params["lim"] == 10
    assert params["off"] == 10


def test_get_report_renders_charts():
    db = MagicMock()
    rid = str(uuid4())
    db.execute_query.return_value = [{
        "id": rid,
        "title": "Quarterly Posture",
        "description": "All servers",
        "charts": [
            {"type": "bar", "title": "EDR Coverage", "query": "..."},
            {"type": "pie", "title": "By Category", "query": "..."},
        ],
        "created_by": "alice",
        "updated_at": "2026-04-20T10:00:00Z",
    }]

    out = get_report_impl(db, report_id=rid)
    assert "Quarterly Posture" in out
    assert "EDR Coverage" in out
    assert "By Category" in out
    assert "Charts (2)" in out


def test_get_report_not_found():
    db = MagicMock()
    db.execute_query.return_value = []
    out = get_report_impl(db, report_id=str(uuid4()))
    assert "not found" in out.lower()


def test_get_report_invalid_uuid():
    db = MagicMock()
    out = get_report_impl(db, report_id="not-a-uuid")
    assert "Invalid report ID" in out
    db.execute_query.assert_not_called()


def test_get_report_with_empty_charts():
    db = MagicMock()
    rid = str(uuid4())
    db.execute_query.return_value = [{
        "id": rid,
        "title": "Empty Report",
        "description": None,
        "charts": [],
        "created_by": None,
        "updated_at": "2026-04-20T10:00:00Z",
    }]
    out = get_report_impl(db, report_id=rid)
    assert "No charts defined" in out


def test_get_report_renders_chart_query_config_definition():
    # The whole point of get_report (vs run_report): show each chart's stored
    # queryConfig as a DEFINITION — type, fields, aggregate, filters — without
    # executing it. A chart with no queryConfig is flagged, not silently blank.
    db = MagicMock()
    rid = str(uuid4())
    db.execute_query.return_value = [{
        "id": rid,
        "title": "Coverage",
        "description": "All servers",
        "charts": [
            {"title": "EDR by Category", "type": "distribution",
             "queryConfig": {
                 "queryType": "distribution",
                 "select": {"dimension": "static:Category",
                            "metric": {"aggregate": "COUNT_DISTINCT",
                                       "field_id": "static:Hostname"}},
                 "options": {"dimensionLimit": 8},
                 "filters": [{"field_id": "static:OS Normalized",
                              "operator": "equals", "value": "Windows 11"}],
             }},
            {"title": "Legacy", "type": "bar"},  # no queryConfig
        ],
        "created_by": None,
        "author": "alice",
        "updated_at": "2026-04-20T10:00:00Z",
    }]
    out = get_report_impl(db, report_id=rid)

    # Definition fields surfaced (encoded ids decoded to display names).
    assert "type: distribution" in out
    assert "dimension: Category" in out
    assert "metric: COUNT_DISTINCT of Hostname" in out
    assert "dimensionLimit: 8" in out
    assert "filter: OS Normalized equals 'Windows 11'" in out
    # The chart that lacks a stored query is called out, not dropped silently.
    assert "no stored query" in out
    # Author resolved via the users join.
    assert "created by: alice" in out
