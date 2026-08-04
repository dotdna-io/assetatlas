# SPDX-License-Identifier: AGPL-3.0-or-later
"""P3 (#735): the list_assets tool description tells the model to raise the
limit / paginate for 'list all' intents so results aren't silently truncated."""

from unittest.mock import MagicMock

from src.api.blueprints.chat.tool_bindings import build_registry


def test_list_assets_description_mentions_list_all_pagination():
    """Registered list_assets description must guide list-all pagination behavior."""
    r = build_registry(db=MagicMock(), suggestion_service=MagicMock())
    # Retrieve the registered ToolSpec directly from the registry internals.
    _, spec = r._tools["list_assets"]
    desc = spec.description.lower()
    assert "list all" in desc, "description must mention 'list all' intent"
    assert "paginate" in desc or "raise" in desc, (
        "description must instruct to raise limit or paginate"
    )


def test_system_prompt_mentions_list_all_pagination():
    """System prompt _TOOL_DISCIPLINE block must include list-all/pagination rule."""
    from src.api.blueprints.chat import prompts

    # Check the private block directly — it's what gets composed into the prompt.
    discipline = prompts._TOOL_DISCIPLINE.lower()
    assert "list all" in discipline, (
        "_TOOL_DISCIPLINE must mention 'list all' intent"
    )
    assert "paginate" in discipline or "raise" in discipline, (
        "_TOOL_DISCIPLINE must instruct to raise limit or paginate"
    )
