# SPDX-License-Identifier: AGPL-3.0-or-later
"""RBAC tests — admin tools hidden from user role; denied on direct invocation."""

import pytest

from src.api.blueprints.chat.tool_registry import ToolRegistry


@pytest.fixture
def registry_with_admin_tool():
    r = ToolRegistry()

    async def _public_tool() -> str:
        return "ok"

    async def _admin_tool() -> str:
        return "secret"

    r.register("public_tool", _public_tool, "public", {"type": "object"},
               admin_required=False)
    r.register("get_audit_trail", _admin_tool, "admin",
               {"type": "object"}, admin_required=True)
    return r


def test_admin_tool_hidden_from_user(registry_with_admin_tool):
    user_names = {t.name for t in registry_with_admin_tool.for_role("user")}
    admin_names = {t.name for t in registry_with_admin_tool.for_role("admin")}
    assert "get_audit_trail" not in user_names
    assert "public_tool" in user_names
    assert "get_audit_trail" in admin_names


def test_admin_tool_visible_to_super_admin(registry_with_admin_tool):
    names = {t.name for t in registry_with_admin_tool.for_role("super_admin")}
    assert "get_audit_trail" in names


@pytest.mark.asyncio
async def test_user_cannot_invoke_admin_tool_by_name(registry_with_admin_tool):
    inv = await registry_with_admin_tool.invoke(
        "get_audit_trail", {}, user_role="user",
    )
    assert inv.status == "denied"
    assert "admin" in inv.result.lower()


@pytest.mark.asyncio
async def test_admin_can_invoke_admin_tool(registry_with_admin_tool):
    inv = await registry_with_admin_tool.invoke(
        "get_audit_trail", {}, user_role="admin",
    )
    assert inv.status == "ok"
    assert inv.result == "secret"
