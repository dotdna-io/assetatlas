# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for OpenAIClient streaming + tool-call accumulation.

Mocks ``AsyncOpenAI.chat.completions.create`` at the object level — the
streaming shape (chunks → ``.choices[0].delta``) is well documented and
stable enough to assert on directly rather than going through the HTTP
transport layer. Shared fakes live in :mod:`_fakes`.
"""

from __future__ import annotations

import pytest

from src.api.blueprints.chat.llm.base import LLMMessage, ToolSpec
from src.api.blueprints.chat.llm.openai_client import OpenAIClient

from ._fakes import FakeSDK, chunk as _chunk, final_usage as _final_usage, tool_call as _tool_call


# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plain_text_response():
    events = [
        _chunk(content="Hello "),
        _chunk(content="world"),
        _chunk(finish_reason="stop"),
        _final_usage(prompt=7, completion=2),
    ]
    client = OpenAIClient(sdk=FakeSDK(events), model="gpt-4o-mini")

    chunks = []
    async with await client.stream(
        system_prompt="sys",
        messages=[LLMMessage(role="user", content="hi")],
        tools=[], max_output_tokens=100,
    ) as s:
        async for c in s:
            chunks.append(c)
        res = s.result

    assert [c.text for c in chunks if c.kind == "text"] == ["Hello ", "world"]
    assert res.stop_reason == "end_turn"
    assert res.final_text == "Hello world"
    assert res.usage.input_tokens == 7
    assert res.usage.output_tokens == 2


@pytest.mark.asyncio
async def test_uses_max_completion_tokens_not_legacy_max_tokens():
    # Reasoning models (o1/o3/o4/gpt-5) reject the legacy ``max_tokens`` param
    # on chat.completions and require ``max_completion_tokens``; the newer name
    # is accepted by every current OpenAI chat model. Hosted OpenAI — and the
    # Foundry openai_chat path, which reuses this client — must send the new
    # name so a valid reasoning-model config doesn't 400 on every request.
    sdk = FakeSDK([_chunk(finish_reason="stop"), _final_usage()])
    client = OpenAIClient(sdk=sdk, model="o3")
    async with await client.stream(
        system_prompt="sys",
        messages=[LLMMessage(role="user", content="hi")],
        tools=[], max_output_tokens=512,
    ) as s:
        async for _ in s:
            pass

    assert sdk.last_call["max_completion_tokens"] == 512
    assert "max_tokens" not in sdk.last_call


@pytest.mark.asyncio
async def test_single_tool_call_accumulated_across_chunks():
    """OpenAI streams tool call args as JSON fragments — the client must
    wait for finish_reason=tool_calls before declaring the call complete."""
    events = [
        # First fragment carries id + name
        _chunk(tool_calls=[_tool_call(index=0, id="call_1",
                                       name="list_assets", args='{"pa')]),
        # Subsequent fragments only carry args deltas
        _chunk(tool_calls=[_tool_call(index=0, args='ge":1}')]),
        _chunk(finish_reason="tool_calls"),
        _final_usage(prompt=5, completion=3),
    ]
    client = OpenAIClient(sdk=FakeSDK(events), model="gpt-4o-mini")
    tools = [ToolSpec(name="list_assets", description="",
                      input_schema={"type": "object", "properties": {}})]

    kinds = []
    async with await client.stream(
        system_prompt="sys",
        messages=[LLMMessage(role="user", content="list")],
        tools=tools, max_output_tokens=100,
    ) as s:
        async for c in s:
            kinds.append(c.kind)
        res = s.result

    assert "tool_call_start" in kinds
    assert kinds.count("tool_call_delta") == 2
    assert "tool_call_end" in kinds
    assert res.stop_reason == "tool_use"
    assert len(res.tool_calls) == 1
    assert res.tool_calls[0]["id"] == "call_1"
    assert res.tool_calls[0]["name"] == "list_assets"
    assert res.tool_calls[0]["arguments"] == {"page": 1}


@pytest.mark.asyncio
async def test_messages_to_openai_pairs_tool_result_with_call_id():
    """Tool replies need tool_call_id so the server can pair them with the
    preceding tool_use block — otherwise OpenAI rejects the request."""
    events = [_chunk(finish_reason="stop"), _final_usage()]
    sdk = FakeSDK(events)
    client = OpenAIClient(sdk=sdk, model="gpt-4o-mini")

    history = [
        LLMMessage(role="user", content="go"),
        LLMMessage(role="assistant", content=None,
                   tool_calls=[{"id": "call_1", "name": "list_assets",
                                "arguments": {"page": 1}}]),
        LLMMessage(role="tool", tool_call_id="call_1",
                   tool_name="list_assets", content="ok"),
    ]
    async with await client.stream(
        system_prompt="sys", messages=history, tools=[], max_output_tokens=16,
    ) as s:
        async for _ in s:
            pass

    sent = sdk.last_call["messages"]
    # system + user + assistant + tool
    assert len(sent) == 4
    assert sent[0]["role"] == "system"
    assert sent[2]["role"] == "assistant"
    assert sent[2]["tool_calls"][0]["id"] == "call_1"
    assert sent[3]["role"] == "tool"
    assert sent[3]["tool_call_id"] == "call_1"
    assert sent[3]["content"] == "ok"


@pytest.mark.asyncio
async def test_finish_length_maps_to_max_tokens_stop_reason():
    events = [_chunk(content="partial"), _chunk(finish_reason="length"),
              _final_usage()]
    client = OpenAIClient(sdk=FakeSDK(events), model="gpt-4o-mini")
    async with await client.stream(
        system_prompt="", messages=[LLMMessage(role="user", content="hi")],
        tools=[], max_output_tokens=2,
    ) as s:
        async for _ in s:
            pass
        assert s.result.stop_reason == "max_tokens"


@pytest.mark.asyncio
async def test_cache_stable_tool_prefix_accepted_and_ignored():
    """OpenAI does server-side prefix caching automatically; the client accepts
    cache_stable_tool_prefix for a uniform LLMClient signature but ignores it —
    no client-side cache markers, request shape unchanged."""
    events = [_chunk(content="ok"), _chunk(finish_reason="stop"),
              _final_usage()]
    sdk = FakeSDK(events)
    client = OpenAIClient(sdk=sdk, model="gpt-4o-mini")
    tools = [ToolSpec(name="t0", description="d",
                      input_schema={"type": "object", "properties": {}}),
             ToolSpec(name="t1", description="d",
                      input_schema={"type": "object", "properties": {}})]
    async with await client.stream(
        system_prompt="sys",
        messages=[LLMMessage(role="user", content="hi")],
        tools=tools, max_output_tokens=16,
        cache_stable_tool_prefix=1,
    ) as s:
        async for _ in s:
            pass
    sent = sdk.last_call
    assert "cache_stable_tool_prefix" not in sent
    assert [t["function"]["name"] for t in sent["tools"]] == ["t0", "t1"]
    assert all("cache_control" not in t for t in sent["tools"])


@pytest.mark.asyncio
async def test_cached_tokens_from_prompt_details():
    events = [_chunk(content="ok"), _chunk(finish_reason="stop"),
              _final_usage(prompt=10, completion=1, cached=6)]
    client = OpenAIClient(sdk=FakeSDK(events), model="gpt-4o-mini")
    async with await client.stream(
        system_prompt="", messages=[LLMMessage(role="user", content="hi")],
        tools=[], max_output_tokens=16,
    ) as s:
        async for _ in s:
            pass
        assert s.result.usage.cached_tokens == 6
