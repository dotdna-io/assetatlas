# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the EOL MCP tool list_eol_assets (§2.G).

Pin the parity changes: within_days OR start/end window, breakdown paging +
real total-distinct, and the per-bucket drilldown artifact whose filters
re-run "show me those N" through query_assets.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.mcp.tools.eol import list_eol_assets_impl as f


def _db(total=42, distinct=9, breakdown=None):
    rows = breakdown if breakdown is not None else [
        {"label": "Windows 10", "n": 20, "min_eol": "2025-10-14",
         "max_eol": "2025-10-14"},
        {"label": "Ubuntu 18.04", "n": 8, "min_eol": "2023-05-31",
         "max_eol": "2023-05-31"},
    ]
    db = MagicMock()
    db.execute_query.side_effect = [
        [{"n": total}],                                       # total
        [{"os_eol_status": "end_of_life", "n": 30}],          # status
        [{"n": distinct}],                                    # total_distinct
        rows,                                                 # breakdown page
        [{"overdue": 18, "next_30": 4, "next_90": 6, "next_365": 10,
          "beyond_365": 4, "no_eol_date": 0}],               # horizon
    ]
    return db


def test_str_path_reports_total_distinct_and_overflow():
    out = f(_db(distinct=9), scope="all")
    assert isinstance(out, str)
    assert "rows 1-2 of 9 distinct" in out
    # 9 distinct, 2 shown → 7 omitted, surfaced not hidden (§4).
    assert "7 more os_normalized value(s) not shown" in out


def test_unknown_scope_is_loud():
    assert "Unknown scope" in f(MagicMock(), scope="nope")


def test_unknown_breakdown_is_loud():
    assert "Unknown breakdown_by" in f(MagicMock(), breakdown_by="serial")


def test_bad_within_days_is_loud():
    assert "within_days must be between" in f(MagicMock(), within_days=99999)


def test_invalid_start_date_is_loud():
    assert "Invalid start date" in f(MagicMock(), start="soon")


def test_start_after_end_is_loud():
    out = f(MagicMock(), start="2026-12-01", end="2026-01-01")
    assert "is after end" in out


def test_breakdown_is_paged_not_silently_capped():
    # The old code hardcoded LIMIT 15; paging makes the whole set reachable.
    db = _db()
    f(db, scope="all", offset=15, limit=50)
    breakdown_sql = db.execute_query.call_args_list[3].args[0]
    assert "OFFSET 15 LIMIT 50" in breakdown_sql


def test_artifact_path_returns_tuple_with_table():
    text, art = f(_db(), scope="upcoming", within_days=90, with_artifact=True)
    assert isinstance(text, str)
    assert art["type"] == "table"
    assert art["meta"]["total_distinct"] == 9
    assert art["meta"]["breakdown_by"] == "os_normalized"


def test_breakdown_row_drilldown_reproduces_scope_and_value():
    # "show me those N" must reproduce the cell: scope EOL predicate + the
    # breakdown value, as a query_assets-runnable filter (§3.6).
    _, art = f(_db(), scope="upcoming", within_days=90, with_artifact=True)
    dd = art["series"][0]["drilldown"]["filters"]
    fields = {x["field"] for x in dd}
    assert "OS EOL Status" in fields           # scope predicate carried
    assert "OS Normalized" in fields           # the breakdown value
    value_filter = [x for x in dd if x["field"] == "OS Normalized"][0]
    assert value_filter == {"field": "OS Normalized", "op": "eq",
                            "value": "Windows 10"}


def test_urgency_buckets_carry_date_drilldowns():
    _, art = f(_db(), scope="all", with_artifact=True)
    urgency = {u["label"]: u for u in art["meta"]["urgency"]}
    assert urgency["overdue"]["count"] == 18
    overdue_filter = urgency["overdue"]["drilldown"]["filters"][0]
    assert overdue_filter["field"] == "OS EOL Date"
    assert overdue_filter["op"] == "lt"


def test_scope_all_flags_flat_logic_drilldown_limitation():
    # past ∪ upcoming is an OR flat filters can't encode — say so, don't
    # pretend the breakdown filter reproduces it exactly.
    _, art = f(_db(), scope="all", with_artifact=True)
    assert "drilldown_note" in art["meta"]


def test_owner_breakdown_uses_array_any_drilldown():
    # owner is TEXT[] — an `eq` wouldn't match the array; the drilldown must
    # use `any` so it actually re-runs.
    db = _db(breakdown=[{"label": "alice", "n": 5, "min_eol": None,
                         "max_eol": None}])
    _, art = f(db, scope="past", breakdown_by="owner", with_artifact=True)
    value_filter = [x for x in art["series"][0]["drilldown"]["filters"]
                    if x["field"] == "Owner"][0]
    assert value_filter["op"] == "any"
    assert value_filter["value"] == ["alice"]
