# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for AnthropicClient streaming behavior.

Uses httpx.MockTransport injected into AsyncAnthropic so no network calls happen.
"""

import json
from typing import List

import httpx
import pytest
from anthropic import AsyncAnthropic

from src.api.blueprints.chat.llm.anthropic_client import (
    AnthropicClient, _messages_to_anthropic,
)
from src.api.blueprints.chat.llm.base import (
    LLMMessage, ToolSpec, _EMPTY_ASSISTANT_PLACEHOLDER,
)


def _sse_line(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def _fake_transport(events: List[bytes]) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        body = b"".join(events)
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_refusal_stop_reason_maps_to_error_with_detail():
    # stop_reason="refusal" is a normal (HTTP 200) safety outcome on Claude 4,
    # not a misconfiguration. The client must surface it as an error carrying a
    # "refusal" detail — not pass "refusal" straight through to service.py's
    # generic branch, which blames the admin's provider configuration.
    events = [
        _sse_line("message_start", {"type": "message_start",
                                    "message": {"id": "m1", "type": "message",
                                                "role": "assistant",
                                                "model": "claude-sonnet-4-6",
                                                "content": [], "stop_reason": None,
                                                "stop_sequence": None,
                                                "usage": {"input_tokens": 5,
                                                          "output_tokens": 0}}}),
        _sse_line("content_block_start", {"type": "content_block_start", "index": 0,
                                          "content_block": {"type": "text", "text": ""}}),
        _sse_line("content_block_delta", {"type": "content_block_delta", "index": 0,
                                          "delta": {"type": "text_delta", "text": "I can"}}),
        _sse_line("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _sse_line("message_delta", {"type": "message_delta",
                                    "delta": {"stop_reason": "refusal",
                                              "stop_sequence": None},
                                    "usage": {"output_tokens": 3}}),
        _sse_line("message_stop", {"type": "message_stop"}),
    ]
    sdk = AsyncAnthropic(api_key="k",
                         http_client=httpx.AsyncClient(transport=_fake_transport(events)))
    client = AnthropicClient(sdk=sdk, model="claude-sonnet-4-6", prompt_cache=False)
    async with await client.stream(system_prompt="sys",
                                   messages=[LLMMessage(role="user", content="hi")],
                                   tools=[], max_output_tokens=100) as stream:
        async for _ in stream:
            pass
        res = stream.result
    assert res.stop_reason == "error"
    assert "refusal" in (res.error or "").lower()


@pytest.mark.asyncio
async def test_plain_text_response():
    events = [
        _sse_line("message_start", {"type": "message_start",
                                    "message": {"id": "m1", "type": "message",
                                                "role": "assistant", "model": "claude-sonnet-4-6",
                                                "content": [], "stop_reason": None,
                                                "stop_sequence": None,
                                                "usage": {"input_tokens": 5,
                                                          "output_tokens": 0}}}),
        _sse_line("content_block_start", {"type": "content_block_start", "index": 0,
                                          "content_block": {"type": "text", "text": ""}}),
        _sse_line("content_block_delta", {"type": "content_block_delta", "index": 0,
                                          "delta": {"type": "text_delta", "text": "hello"}}),
        _sse_line("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _sse_line("message_delta", {"type": "message_delta",
                                    "delta": {"stop_reason": "end_turn",
                                              "stop_sequence": None},
                                    "usage": {"output_tokens": 2}}),
        _sse_line("message_stop", {"type": "message_stop"}),
    ]
    sdk = AsyncAnthropic(api_key="k",
                         http_client=httpx.AsyncClient(transport=_fake_transport(events)))
    client = AnthropicClient(sdk=sdk, model="claude-sonnet-4-6", prompt_cache=False)
    chunks = []
    async with await client.stream(system_prompt="sys",
                                   messages=[LLMMessage(role="user", content="hi")],
                                   tools=[], max_output_tokens=100) as stream:
        async for c in stream:
            chunks.append(c)
        res = stream.result
    assert any(c.kind == "text" and c.text == "hello" for c in chunks)
    assert res.stop_reason == "end_turn"
    assert res.final_text == "hello"
    assert res.usage.input_tokens == 5
    assert res.usage.output_tokens == 2


@pytest.mark.asyncio
async def test_tool_use_response():
    events = [
        _sse_line("message_start", {"type": "message_start",
                                    "message": {"id": "m2", "type": "message",
                                                "role": "assistant", "model": "claude-sonnet-4-6",
                                                "content": [], "stop_reason": None,
                                                "stop_sequence": None,
                                                "usage": {"input_tokens": 4,
                                                          "output_tokens": 0}}}),
        _sse_line("content_block_start", {"type": "content_block_start", "index": 0,
                                          "content_block": {"type": "tool_use",
                                                            "id": "toolu_1",
                                                            "name": "list_assets",
                                                            "input": {}}}),
        _sse_line("content_block_delta", {"type": "content_block_delta", "index": 0,
                                          "delta": {"type": "input_json_delta",
                                                    "partial_json": '{"page":1}'}}),
        _sse_line("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _sse_line("message_delta", {"type": "message_delta",
                                    "delta": {"stop_reason": "tool_use",
                                              "stop_sequence": None},
                                    "usage": {"output_tokens": 3}}),
        _sse_line("message_stop", {"type": "message_stop"}),
    ]
    sdk = AsyncAnthropic(api_key="k",
                         http_client=httpx.AsyncClient(transport=_fake_transport(events)))
    client = AnthropicClient(sdk=sdk, model="claude-sonnet-4-6", prompt_cache=False)
    tools = [ToolSpec(name="list_assets", description="List",
                      input_schema={"type": "object", "properties": {}})]
    async with await client.stream(system_prompt="sys",
                                   messages=[LLMMessage(role="user", content="list")],
                                   tools=tools, max_output_tokens=100) as stream:
        async for _ in stream:
            pass
        res = stream.result
    assert res.stop_reason == "tool_use"
    assert len(res.tool_calls) == 1
    assert res.tool_calls[0]["name"] == "list_assets"
    assert res.tool_calls[0]["arguments"] == {"page": 1}


@pytest.mark.asyncio
async def test_stream_closes_sdk_context_manager():
    """_AnthropicStream.__aexit__ must release the SDK MessageStreamManager."""
    events = [
        _sse_line("message_start", {"type": "message_start",
                                    "message": {"id": "m3", "type": "message",
                                                "role": "assistant", "model": "claude-sonnet-4-6",
                                                "content": [], "stop_reason": None,
                                                "stop_sequence": None,
                                                "usage": {"input_tokens": 1,
                                                          "output_tokens": 0}}}),
        _sse_line("message_delta", {"type": "message_delta",
                                    "delta": {"stop_reason": "end_turn",
                                              "stop_sequence": None},
                                    "usage": {"output_tokens": 1}}),
        _sse_line("message_stop", {"type": "message_stop"}),
    ]
    sdk = AsyncAnthropic(api_key="k",
                         http_client=httpx.AsyncClient(transport=_fake_transport(events)))

    original = sdk.messages.stream
    aexit_calls: List[int] = []

    def _spy(**kw):
        mgr = original(**kw)
        real_aexit = mgr.__aexit__

        async def _recorded_aexit(*exc):
            aexit_calls.append(1)
            return await real_aexit(*exc)

        mgr.__aexit__ = _recorded_aexit   # type: ignore[method-assign]
        return mgr

    sdk.messages.stream = _spy   # type: ignore[assignment]

    client = AnthropicClient(sdk=sdk, model="claude-sonnet-4-6", prompt_cache=False)
    async with await client.stream(system_prompt="sys",
                                   messages=[LLMMessage(role="user", content="hi")],
                                   tools=[], max_output_tokens=10) as stream:
        async for _ in stream:
            pass

    assert aexit_calls == [1]


def _capturing_transport(captured: dict, events: List[bytes]) -> httpx.MockTransport:
    """Like _fake_transport but records the outgoing request body so tests can
    assert the exact tools shape Anthropic receives (cache_control placement)."""
    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, content=b"".join(events),
                              headers={"content-type": "text/event-stream"})
    return httpx.MockTransport(handler)


def _plain_text_events() -> List[bytes]:
    return [
        _sse_line("message_start", {"type": "message_start",
                  "message": {"id": "m", "type": "message", "role": "assistant",
                              "model": "claude-sonnet-4-6", "content": [],
                              "stop_reason": None, "stop_sequence": None,
                              "usage": {"input_tokens": 1, "output_tokens": 0}}}),
        _sse_line("content_block_start", {"type": "content_block_start",
                  "index": 0, "content_block": {"type": "text", "text": ""}}),
        _sse_line("content_block_delta", {"type": "content_block_delta",
                  "index": 0, "delta": {"type": "text_delta", "text": "ok"}}),
        _sse_line("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _sse_line("message_delta", {"type": "message_delta",
                  "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                  "usage": {"output_tokens": 1}}),
        _sse_line("message_stop", {"type": "message_stop"}),
    ]


def _n_tools(n: int) -> List[ToolSpec]:
    return [ToolSpec(name=f"t{i}", description="d",
                     input_schema={"type": "object", "properties": {}})
            for i in range(n)]


@pytest.mark.asyncio
async def test_cache_control_on_stable_prefix_boundary_tool():
    """Routing ON: cache_control is set on the tool at index N-1 (the last
    stable tool), not on the last tool overall. The cached prefix (system +
    stable core tools) is then identical across turns even though the variable
    retrieved tail after it changes per turn — marking the final tool would bust
    the prefix cache every turn (the bug this fixes)."""
    captured: dict = {}
    sdk = AsyncAnthropic(api_key="k", http_client=httpx.AsyncClient(
        transport=_capturing_transport(captured, _plain_text_events())))
    client = AnthropicClient(sdk=sdk, model="claude-sonnet-4-6",
                             prompt_cache=True)
    async with await client.stream(
        system_prompt="sys",
        messages=[LLMMessage(role="user", content="hi")],
        tools=_n_tools(5), max_output_tokens=50,
        cache_stable_tool_prefix=2,
    ) as stream:
        async for _ in stream:
            pass
    tools = captured["body"]["tools"]
    # cache_control on the last STABLE tool (index 1 == N-1), and only there.
    assert tools[1].get("cache_control") == {"type": "ephemeral"}
    assert [i for i, t in enumerate(tools) if "cache_control" in t] == [1]


@pytest.mark.asyncio
async def test_cache_control_prefix_none_marks_last_tool():
    """cache_stable_tool_prefix=None (routing OFF / legacy callers) keeps
    cache_control on the LAST tool — today's behavior, unchanged."""
    captured: dict = {}
    sdk = AsyncAnthropic(api_key="k", http_client=httpx.AsyncClient(
        transport=_capturing_transport(captured, _plain_text_events())))
    client = AnthropicClient(sdk=sdk, model="claude-sonnet-4-6",
                             prompt_cache=True)
    async with await client.stream(
        system_prompt="sys",
        messages=[LLMMessage(role="user", content="hi")],
        tools=_n_tools(3), max_output_tokens=50,
        cache_stable_tool_prefix=None,
    ) as stream:
        async for _ in stream:
            pass
    tools = captured["body"]["tools"]
    assert tools[-1].get("cache_control") == {"type": "ephemeral"}
    assert [i for i, t in enumerate(tools) if "cache_control" in t] == [len(tools) - 1]


def test_messages_to_anthropic_empty_assistant_gets_placeholder():
    """The Anthropic Messages API rejects a message with empty content. A turn
    that ends with no text and no tool calls is persisted with content="" and
    replayed on the next turn, so the serializer must emit a non-empty
    placeholder block instead of content=[]."""
    msgs = [LLMMessage(role="assistant", content="", tool_calls=None)]
    assert _messages_to_anthropic(msgs) == [{
        "role": "assistant",
        "content": [{"type": "text", "text": _EMPTY_ASSISTANT_PLACEHOLDER}],
    }]


def test_messages_to_anthropic_coalesces_parallel_tool_results():
    """The Anthropic Messages API enforces user/assistant alternation: when an
    assistant turn emits multiple tool_use blocks (parallel tool use), ALL
    matching tool_result blocks must sit in the single user message that
    follows. Each result is a separate tool-role LLMMessage, so emitting one
    user message per result produces consecutive user turns the API rejects
    (same class of bug as the Bedrock Converse path). Consecutive tool-role
    messages must be coalesced into one user message."""
    msgs = [
        LLMMessage(role="user", content="status?"),
        LLMMessage(
            role="assistant", content="checking",
            tool_calls=[
                {"id": "tu_1", "name": "a", "arguments": {}},
                {"id": "tu_2", "name": "b", "arguments": {"k": "v"}},
            ],
        ),
        LLMMessage(role="tool", tool_call_id="tu_1", content="res 1"),
        LLMMessage(role="tool", tool_call_id="tu_2", content="res 2"),
    ]
    assert _messages_to_anthropic(msgs) == [
        {"role": "user", "content": "status?"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "id": "tu_1", "name": "a", "input": {}},
            {"type": "tool_use", "id": "tu_2", "name": "b",
             "input": {"k": "v"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "res 1"},
            {"type": "tool_result", "tool_use_id": "tu_2", "content": "res 2"},
        ]},
    ]
