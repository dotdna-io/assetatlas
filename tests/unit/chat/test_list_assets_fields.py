# SPDX-License-Identifier: AGPL-3.0-or-later
"""#742: the chat list_assets result must fetch the fields its table + text
actually display, instead of relying on the repo default
(DEFAULT_LIST_FIELDS_ENCODED), which omits IP / Owner / Data Source / MAC /
Serial / Location — leaving those table columns blank and hiding those values
from the model.
"""

import asyncio
from unittest.mock import MagicMock, patch

from src.api.blueprints.chat.artifacts import _LIST_ASSET_COLUMNS
from src.mcp.tools.assets import _ENCODED_TO_SNAKE

# Snake keys the LLM-text formatter (mcp.tools.assets._format_asset_row) renders
# "if present", beyond hostname/category which it always prints. Mirrors that
# function's field list — kept here so a drift in either side trips the
# structural test below.
_TEXT_FORMATTER_KEYS = {
    "owner", "location", "os_normalized", "ip", "mac", "sn",
    "manufacturer", "model", "confidence",
}


def test_list_assets_binding_requests_explicit_display_fields():
    """The binding must pass an explicit ``fields`` set to list_assets_query,
    not rely on the repo default that drops IP/Owner/Data Source/MAC/Serial/
    Location (the values the chat table + text most often need)."""
    from src.api.blueprints.chat.tool_bindings import build_registry

    r = build_registry(db=MagicMock(), suggestion_service=MagicMock())
    # list_assets is now a shim onto query_assets, which calls list_assets_query
    # from its own module namespace — patch it there.
    with patch(
        "src.mcp.tools.query_assets.list_assets_query"
    ) as mock_q:
        mock_q.return_value = ([], 0)
        asyncio.run(r.invoke("list_assets", {}, user_role="user"))

    fields_arg = mock_q.call_args.kwargs.get("fields")
    assert fields_arg, (
        "list_assets binding must request explicit display fields; relying on "
        "the repo default leaves IP/Owner/Data Source blank (#742)"
    )
    for enc in (
        "static:IP%20Address", "static:Owner", "static:Data%20Source",
        "static:MAC%20Address", "static:Serial%20Number", "static:Location",
    ):
        assert enc in fields_arg, f"chat list fields missing {enc}"


def test_chat_list_field_set_covers_every_consumed_column():
    """Structural guard against drift: the chat field set must cover every snake
    key the table artifact (_LIST_ASSET_COLUMNS) and the text formatter consume,
    so adding a column to either without adding its source field fails here
    rather than silently rendering blank."""
    from src.api.blueprints.chat.tool_bindings import _CHAT_LIST_FIELDS_ENCODED

    requested = {
        _ENCODED_TO_SNAKE[enc]
        for enc in _CHAT_LIST_FIELDS_ENCODED.split(",")
    }
    consumed = {k for k, _label in _LIST_ASSET_COLUMNS} | _TEXT_FORMATTER_KEYS
    missing = consumed - requested
    assert not missing, f"chat list field set omits consumed columns: {missing}"


def test_chat_list_and_csv_export_share_one_field_set():
    """The chat list preview and the CSV export MUST fetch the SAME field set —
    a download has to match what the user saw. Single-sourced in _query.py; this
    fails if either call site re-introduces a divergent literal."""
    from src.mcp.tools._query import CHAT_LIST_FIELDS_ENCODED
    from src.api.blueprints.chat.tool_bindings import _CHAT_LIST_FIELDS_ENCODED
    from src.api.blueprints.assets import _EXPORT_FIELDS_ENCODED

    assert _CHAT_LIST_FIELDS_ENCODED is CHAT_LIST_FIELDS_ENCODED
    assert _EXPORT_FIELDS_ENCODED is CHAT_LIST_FIELDS_ENCODED
