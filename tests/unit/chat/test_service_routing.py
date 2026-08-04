# SPDX-License-Identifier: AGPL-3.0-or-later
# tests/unit/chat/test_service_routing.py — focused on set-selection, not the LLM
from src.api.blueprints.chat.service import _select_turn_tools


def test_select_turn_tools_flag_off_returns_all(monkeypatch):
    all_specs = ["A", "B", "C"]
    out = _select_turn_tools(routing_enabled=False, router=None,
                             all_role_tools=all_specs,
                             user_text="x", role="user", loaded=set())
    assert out == all_specs


def test_select_turn_tools_flag_on_uses_router():
    class _R:
        def select(self, user_text, role, loaded, top_k=None):
            return ["core1", "core2"]
    out = _select_turn_tools(routing_enabled=True, router=_R(),
                             all_role_tools=["A", "B", "C"],
                             user_text="x", role="user", loaded=set())
    assert out == ["core1", "core2"]


def test_select_turn_tools_no_router_returns_all():
    """Flag ON but no router still falls back to all_role_tools."""
    all_specs = ["A", "B", "C"]
    out = _select_turn_tools(routing_enabled=True, router=None,
                             all_role_tools=all_specs,
                             user_text="x", role="user", loaded=set())
    assert out == all_specs


def test_router_built_with_core_and_registry():
    from unittest.mock import MagicMock
    from src.api.blueprints.chat.tool_router import ToolRouter, CORE_TOOL_NAMES
    from src.api.blueprints.chat.tool_bindings import build_registry
    reg = build_registry(db=MagicMock(), suggestion_service=MagicMock())
    router = ToolRouter(reg, embedder=None, top_k=5)
    names = [s.name for s in router.select("how many servers", "admin", set())]
    for c in CORE_TOOL_NAMES:
        assert c in names
    assert "find_tools" in names
