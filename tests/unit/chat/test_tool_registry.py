# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for the chat ToolRegistry.

Covers role-based filtering of admin-only tools and the invoke() contract:
ok / denied / error status branches plus the per-tool timeout path.
"""

import asyncio

import pytest

from src.api.blueprints.chat.tool_registry import ToolRegistry


def _fn(name):
    async def handler(**kwargs):
        return f"called {name} with {kwargs}"
    return handler


def test_registry_filters_admin_tools_for_users():
    r = ToolRegistry()
    r.register("list_assets", _fn("list_assets"), "List", {}, admin_required=False)
    r.register("get_audit_trail", _fn("audit"), "Audit", {}, admin_required=True)
    user_tools = r.for_role("user")
    admin_tools = r.for_role("admin")
    assert [t.name for t in user_tools] == ["list_assets"]
    assert sorted(t.name for t in admin_tools) == ["get_audit_trail", "list_assets"]


def test_registry_super_admin_sees_admin_tools():
    r = ToolRegistry()
    r.register("get_audit_trail", _fn("a"), "", {}, admin_required=True)
    assert len(r.for_role("super_admin")) == 1


@pytest.mark.asyncio
async def test_hidden_tool_is_not_advertised_but_still_invocable():
    """A hidden alias (advertise=False) must drop out of for_role for EVERY role
    — so its schema never costs input tokens — yet stay invocable, so a model
    that calls the old name out of habit gets a real result instead of an error
    (which would burn an extra retry iteration). This is the whole point of
    hiding deprecated aliases rather than deleting them."""
    r = ToolRegistry()
    r.register("query_assets", _fn("query_assets"), "primary", {})
    r.register("list_assets", _fn("list_assets"), "alias", {})
    r.hide("list_assets")

    for role in ("user", "admin", "super_admin"):
        advertised = {t.name for t in r.for_role(role)}
        assert "query_assets" in advertised
        assert "list_assets" not in advertised, f"hidden alias advertised to {role}"

    assert r.is_registered("list_assets")
    inv = await r.invoke("list_assets", {"page": 1}, user_role="user")
    assert inv.status == "ok"
    assert "called list_assets" in inv.result


def test_hide_ignores_unknown_names():
    """hide() tolerates names that were never registered (a shim whose backing
    service was absent), so build_registry can hide() unconditionally."""
    r = ToolRegistry()
    r.register("query_assets", _fn("query_assets"), "primary", {})
    r.hide("get_reliability", "query_assets")  # first is absent — must not raise
    assert not r.is_registered("get_reliability")
    assert "query_assets" not in {t.name for t in r.for_role("admin")}


@pytest.mark.asyncio
async def test_registry_invoke_ok():
    r = ToolRegistry()
    r.register("list_assets", _fn("list_assets"), "", {}, admin_required=False)
    inv = await r.invoke("list_assets", {"page": 1}, user_role="user")
    assert inv.status == "ok"
    assert "called list_assets" in inv.result


@pytest.mark.asyncio
async def test_registry_invoke_denied_for_admin_tool_as_user():
    r = ToolRegistry()
    r.register("get_audit_trail", _fn("a"), "", {}, admin_required=True)
    inv = await r.invoke("get_audit_trail", {}, user_role="user")
    assert inv.status == "denied"


@pytest.mark.asyncio
async def test_registry_invoke_unknown_tool():
    r = ToolRegistry()
    inv = await r.invoke("nope", {}, user_role="user")
    assert inv.status == "error"


@pytest.mark.asyncio
async def test_registry_invoke_timeout():
    r = ToolRegistry(per_tool_timeout=0.05)

    async def slow_handler(**kwargs):
        await asyncio.sleep(0.5)
        return "should not reach"

    r.register("slow_tool", slow_handler, "", {}, admin_required=False)
    inv = await r.invoke("slow_tool", {}, user_role="user")
    assert inv.status == "timeout"
    assert inv.error == "timeout"
    assert "timed out" in inv.result.lower()
    assert "slow_tool" in inv.result
    assert "should not reach" not in inv.result


@pytest.mark.asyncio
async def test_registry_invoke_handler_exception_does_not_leak_message():
    r = ToolRegistry()

    async def crashing_handler(**kwargs):
        raise RuntimeError("boom — secret detail leaked")

    r.register("crashing_tool", crashing_handler, "", {}, admin_required=False)
    inv = await r.invoke("crashing_tool", {}, user_role="user")
    assert inv.status == "error"
    assert inv.error == "boom — secret detail leaked"
    assert "RuntimeError" in inv.result
    assert "boom" not in inv.result


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_type", [ValueError, KeyError, LookupError])
async def test_registry_invoke_surfaces_validation_error_message(exc_type):
    """Validation-style errors are allowlisted to reach the model so it can
    correct its next call instead of retrying with guesses."""
    r = ToolRegistry()

    async def bad_handler(**kwargs):
        raise exc_type("Unknown field 'confidence'. Closest: 'Confidence Score'")

    r.register("t", bad_handler, "", {}, admin_required=False)
    inv = await r.invoke("t", {}, user_role="user")
    assert inv.status == "error"
    assert "Unknown field" in inv.result
    assert "Confidence Score" in inv.result


@pytest.mark.asyncio
async def test_registry_invoke_caps_validation_error_message_length():
    """A pathological message can't blow the context window."""
    r = ToolRegistry()

    async def verbose(**kwargs):
        raise ValueError("x" * 5000)

    r.register("t", verbose, "", {}, admin_required=False)
    inv = await r.invoke("t", {}, user_role="user")
    assert len(inv.result) < 1000


# --- schema-violation pre-checks ------------------------------------------

@pytest.mark.asyncio
async def test_registry_rejects_unknown_kwargs_with_valid_param_list():
    """When the LLM hallucinates a parameter name, we fail fast with a message
    that lists the real parameters — turning a raw TypeError into guidance."""
    r = ToolRegistry()
    schema = {"type": "object",
              "properties": {"row_field": {"type": "string"},
                             "col_field": {"type": "string"}},
              "required": ["row_field"]}
    r.register("aggregate_assets", _fn("aggregate_assets"), "", schema)
    inv = await r.invoke("aggregate_assets",
                         {"rowparameter=row_field": "OS Normalized"},
                         user_role="user")
    assert inv.status == "error"
    assert inv.error == "schema_violation"
    assert "rowparameter=row_field" in inv.result
    assert "row_field" in inv.result           # the valid name is listed
    assert "col_field" in inv.result


@pytest.mark.asyncio
async def test_registry_rejects_missing_required_params():
    r = ToolRegistry()
    schema = {"type": "object",
              "properties": {"asset_id": {"type": "string"}},
              "required": ["asset_id"]}
    r.register("get_asset", _fn("get_asset"), "", schema)
    inv = await r.invoke("get_asset", {}, user_role="user")
    assert inv.status == "error"
    assert inv.error == "schema_violation"
    assert "asset_id" in inv.result


@pytest.mark.asyncio
async def test_registry_allows_extras_when_schema_declares_no_properties():
    """Back-compat for handlers registered without a properties block.
    We can't judge 'unknown' without a declared vocabulary."""
    r = ToolRegistry()
    r.register("t", _fn("t"), "", {})  # no properties → no schema to check
    inv = await r.invoke("t", {"anything": 1, "whatever": 2}, user_role="user")
    assert inv.status == "ok"


@pytest.mark.asyncio
async def test_registry_rejects_non_dict_args_cleanly():
    r = ToolRegistry()
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    r.register("t", _fn("t"), "", schema)
    inv = await r.invoke("t", "not a dict", user_role="user")  # type: ignore[arg-type]
    assert inv.status == "error"
    assert inv.error == "schema_violation"


@pytest.mark.asyncio
async def test_registry_rejects_enum_violations_with_valid_values():
    """When a schema declares an enum, an off-list value must fail fast.
    Silent fallback to a default (e.g. time_range='90d' silently coerced to
    '7d' inside the impl) turns 'last 90 days' into 'last 7 days' with no
    signal the question wasn't actually answered."""
    r = ToolRegistry()
    schema = {
        "type": "object",
        "properties": {
            "time_range": {"type": "string",
                           "enum": ["24h", "7d", "30d"]},
        },
    }
    r.register("get_alerts", _fn("get_alerts"), "", schema)
    inv = await r.invoke("get_alerts", {"time_range": "90d"}, user_role="user")
    assert inv.status == "error"
    assert inv.error == "schema_violation"
    assert "90d" in inv.result
    assert "24h" in inv.result and "30d" in inv.result   # valid values listed


@pytest.mark.asyncio
async def test_registry_allows_enum_value_that_matches():
    r = ToolRegistry()
    schema = {"type": "object",
              "properties": {"sort_dir": {"type": "string",
                                          "enum": ["asc", "desc"]}}}
    r.register("t", _fn("t"), "", schema)
    inv = await r.invoke("t", {"sort_dir": "asc"}, user_role="user")
    assert inv.status == "ok"


@pytest.mark.asyncio
async def test_registry_allows_none_for_optional_enum_param():
    """Optional params default to None; enum enforcement must not reject that."""
    r = ToolRegistry()
    schema = {"type": "object",
              "properties": {"severity": {"type": "string",
                                          "enum": ["critical", "high"]}}}
    r.register("t", _fn("t"), "", schema)
    inv = await r.invoke("t", {"severity": None}, user_role="user")
    assert inv.status == "ok"


# --- artifact payload handling --------------------------------------------

_VALID_ARTIFACT = {
    "type": "donut",
    "title": "Assets by Category",
    "series": [{"label": "server", "value": 5}],
}


@pytest.mark.asyncio
async def test_registry_invoke_carries_artifact_from_tuple_return():
    r = ToolRegistry()

    async def handler(**kwargs):
        return "COUNT: 5", _VALID_ARTIFACT

    r.register("aggregate_assets", handler, "", {})
    inv = await r.invoke("aggregate_assets", {}, user_role="user")
    assert inv.status == "ok"
    assert inv.result == "COUNT: 5"
    assert inv.artifact == _VALID_ARTIFACT


@pytest.mark.asyncio
async def test_registry_invoke_string_return_leaves_artifact_none():
    r = ToolRegistry()
    r.register("t", _fn("t"), "", {})
    inv = await r.invoke("t", {}, user_role="user")
    assert inv.status == "ok"
    assert inv.artifact is None


@pytest.mark.asyncio
async def test_registry_invoke_tuple_with_none_artifact_keeps_text():
    r = ToolRegistry()

    async def handler(**kwargs):
        return "plain text", None

    r.register("t", handler, "", {})
    inv = await r.invoke("t", {}, user_role="user")
    assert inv.status == "ok"
    assert inv.result == "plain text"
    assert inv.artifact is None


@pytest.mark.asyncio
async def test_registry_invoke_drops_malformed_artifact_but_tool_still_succeeds():
    r = ToolRegistry()

    async def handler(**kwargs):
        # Missing required "series" key → validator rejects.
        return "summary", {"type": "donut", "title": "x"}

    r.register("t", handler, "", {})
    inv = await r.invoke("t", {}, user_role="user")
    assert inv.status == "ok"
    assert inv.result == "summary"
    assert inv.artifact is None


@pytest.mark.asyncio
async def test_registry_invoke_drops_non_dict_artifact():
    r = ToolRegistry()

    async def handler(**kwargs):
        return "summary", ["not", "a", "dict"]

    r.register("t", handler, "", {})
    inv = await r.invoke("t", {}, user_role="user")
    assert inv.status == "ok"
    assert inv.artifact is None


@pytest.mark.asyncio
async def test_registry_invoke_drops_artifact_with_non_list_series():
    r = ToolRegistry()

    async def handler(**kwargs):
        return "summary", {
            "type": "donut", "title": "x", "series": "not a list",
        }

    r.register("t", handler, "", {})
    inv = await r.invoke("t", {}, user_role="user")
    assert inv.status == "ok"
    assert inv.artifact is None


@pytest.mark.asyncio
async def test_registry_truncation_applies_to_text_only():
    """Artifact payloads don't count against the result_bytes cap — only
    the LLM-facing string does. This keeps a large legitimate chart
    (many slices) from being silently dropped by the text truncator."""
    r = ToolRegistry(max_result_bytes=32)

    big_artifact = {
        "type": "donut", "title": "x",
        "series": [{"label": "L" * 500, "value": 1}],
    }

    async def handler(**kwargs):
        return "A" * 200, big_artifact

    r.register("t", handler, "", {})
    inv = await r.invoke("t", {}, user_role="user")
    assert inv.status == "ok"
    # Text was truncated (original had 200 "A"s); artifact is unaffected.
    assert inv.result.count("A") < 200
    assert inv.artifact == big_artifact


@pytest.mark.asyncio
async def test_registry_invoke_handles_malformed_tuple_length():
    r = ToolRegistry()

    async def handler(**kwargs):
        return ("a", "b", "c")  # type: ignore[return-value]

    r.register("t", handler, "", {})
    inv = await r.invoke("t", {}, user_role="user")
    assert inv.status == "ok"
    assert inv.result == "a"
    assert inv.artifact is None
