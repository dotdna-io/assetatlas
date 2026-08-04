# SPDX-License-Identifier: AGPL-3.0-or-later
from src.api.blueprints.chat.artifacts import (
    DEFAULT_MAX_TABLE_ROWS,
    build_table_from_assets,
)


def test_display_cap_is_100():
    assert DEFAULT_MAX_TABLE_ROWS == 100


def test_table_carries_query_when_provided():
    q = {"filters": [{"field": "OS", "op": "eq", "value": "Ubuntu"}],
         "search": None, "sort_by": None, "sort_dir": "asc"}
    art = build_table_from_assets(
        [{"hostname": "h1"}], total=1, source_tool="list_assets", query=q,
    )
    assert art["meta"]["query"] == q


def test_table_omits_query_when_absent():
    art = build_table_from_assets(
        [{"hostname": "h1"}], total=1, source_tool="search_assets",
    )
    assert "query" not in art["meta"]


def test_truncated_flag_at_cap_boundary():
    rows_at_cap = [{"hostname": f"h{i}"} for i in range(DEFAULT_MAX_TABLE_ROWS)]
    rows_over_cap = [{"hostname": f"h{i}"} for i in range(DEFAULT_MAX_TABLE_ROWS + 1)]
    assert build_table_from_assets(
        rows_at_cap, total=DEFAULT_MAX_TABLE_ROWS, source_tool="list_assets")["meta"]["truncated"] is False
    art = build_table_from_assets(rows_over_cap, total=DEFAULT_MAX_TABLE_ROWS + 1, source_tool="list_assets")
    assert art["meta"]["truncated"] is True
    assert len(art["series"]) == DEFAULT_MAX_TABLE_ROWS


def test_chat_list_constants():
    from src.api.blueprints.chat.tool_bindings import (
        _CHAT_LIST_DEFAULT_LIMIT, _CHAT_LIST_TEXT_ROWS,
    )
    assert _CHAT_LIST_DEFAULT_LIMIT == 100
    assert _CHAT_LIST_TEXT_ROWS == 25
