# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for the chat system prompt builder.

Verifies stable block composition, role-based variation, and determinism so
the LLM layer can rely on prompt caching for the same (role, provider) pair.
"""

from src.api.blueprints.chat.prompts import build_system_prompt, REFUSAL_LINKS


def test_system_prompt_mentions_readonly():
    out = build_system_prompt(user_role="user")
    assert "read-only" in out.lower()


def test_system_prompt_mentions_role_for_admin():
    out = build_system_prompt(user_role="admin")
    assert "admin" in out.lower()


def test_system_prompt_has_tool_use_discipline():
    out = build_system_prompt(user_role="user")
    assert "semantic_suggest" in out
    assert "filters" in out


def test_refusal_links_cover_main_surfaces():
    for key in (
        "alerts",
        "tag_rules",
        "posture",
        "integrations",
        "explorer",
        "settings",
    ):
        assert key in REFUSAL_LINKS


def test_system_prompt_is_deterministic_for_same_inputs():
    a = build_system_prompt(user_role="user")
    b = build_system_prompt(user_role="user")
    assert a == b


def test_system_prompt_differs_between_user_and_admin():
    user_prompt = build_system_prompt(user_role="user")
    admin_prompt = build_system_prompt(user_role="admin")
    assert user_prompt != admin_prompt


def test_super_admin_treated_as_admin():
    admin = build_system_prompt(user_role="admin")
    super_admin = build_system_prompt(user_role="super_admin")
    assert admin == super_admin


def test_formatting_block_warns_against_duplicating_artifact_tables():
    """query_assets / export_assets / get_asset emit inline artifacts; the LLM
    must not also produce Markdown tables for them or the same data is shown
    twice (see screenshot bug 2026-04). The block names the canonical
    artifact-emitting tools (the deprecated list_assets / search_assets /
    aggregate_assets aliases are no longer advertised)."""
    out = build_system_prompt(user_role="user")
    assert "query_assets" in out
    assert "Do NOT repeat" in out


def test_formatting_block_discourages_emphasis_on_category_words():
    """Bolding/italicizing a category token next to a Badge renders the
    word twice ("workstationworkstation"). The prompt warns the LLM."""
    out = build_system_prompt(user_role="user")
    assert "**bold**" in out or "*italics*" in out


def test_tool_discipline_teaches_query_assets_and_modes():
    """query_assets is the primary asset tool; the prompt must name it and its
    three modes so the model picks the right one (list vs aggregate vs
    field_values) instead of mis-routing. export_assets must be taught as the
    download path (so whole-set pulls don't land in the conversation)."""
    out = build_system_prompt(user_role="user")
    assert "query_assets" in out
    for mode in ("list", "aggregate", "field_values"):
        assert mode in out
    assert "export_assets" in out
    # the COUNT-quoting rule must survive the rewrite (relied on by evals)
    assert "COUNT: <N>" in out


def test_tool_discipline_teaches_run_chart_routing():
    """The query_assets-aggregate vs run_chart seam is the most mis-callable
    surface (two COUNT-capable grammars). The prompt must route so the model
    picks the right one: plain COUNT / top-N by 1-2 fields → query_assets
    aggregate; AVG / percent-of-base / multi-bucket trend / correlation →
    run_chart. Without this guidance the model mis-selects the engine."""
    out = build_system_prompt(user_role="user")
    assert "run_chart" in out
    low = out.lower()
    # the discriminators the routing hinges on are named
    assert "trend" in low and "correlation" in low
    # describe_catalog is the discovery backbone (operator_specs per field)
    assert "describe_catalog" in out


def test_tool_discipline_teaches_find_tools_recovery():
    # find_tools is only advertised when routing is on, so the recovery nudge
    # is gated on tool_routing — pointing the model at a tool it can't see
    # would be wrong with routing off (the flag-off default).
    assert "find_tools" in build_system_prompt(
        user_role="user", tool_routing=True)
    assert "find_tools" not in build_system_prompt(user_role="user")
