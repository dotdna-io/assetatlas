# SPDX-License-Identifier: AGPL-3.0-or-later
from src.api.blueprints.chat.prompts import _TOOL_DISCIPLINE


def test_tool_discipline_mentions_csv_download():
    text = _TOOL_DISCIPLINE.lower()
    assert "csv" in text
    assert "download" in text
