# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for FoundryResponsesClient.

Wraps openai-python's responses.create(..., stream=True). We script the
event stream and assert on LLMChunk translation, tool-call accumulation,
usage extraction, and message conversion (especially tool-result round-trip).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List

import pytest

from src.api.blueprints.chat.llm.base import LLMMessage, ToolSpec
from src.api.blueprints.chat.llm.foundry_responses_client import (
    FoundryResponsesClient,
    _messages_to_responses,
)


# --- fakes ----------------------------------------------------------------

class _ScriptedStream:
    def __init__(self, events: List[Any]) -> None:
        self._events = list(events)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)

    async def aclose(self):
        pass


class _FakeSDK:
    def __init__(self, events: List[Any]) -> None:
        self._events = events
        self.last_call: Any = None
        self.responses = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.last_call = kwargs
        return _ScriptedStream(self._events)


def _ev(type_: str, **fields):
    return SimpleNamespace(type=type_, **fields)


# --- tests ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_plain_text_stream_yields_text_chunks_and_usage():
    events = [
        _ev("response.output_text.delta", delta="Hello "),
        _ev("response.output_text.delta", delta="world"),
        _ev("response.completed", response=SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=7, output_tokens=2,
                input_tokens_details=SimpleNamespace(cached_tokens=0),
            ),
        )),
    ]
    sdk = _FakeSDK(events)
    client = FoundryResponsesClient(sdk=sdk, model="gpt-5.4-mini")

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
    assert res.final_text == "Hello world"
    assert res.usage.input_tokens == 7
    assert res.usage.output_tokens == 2
    assert res.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_tool_call_accumulates_and_appears_in_result():
    events = [
        _ev("response.output_item.added", output_index=0, item=SimpleNamespace(
            type="function_call", id="fc_1", call_id="call_1",
            name="list_assets", arguments="",
        )),
        _ev("response.function_call_arguments.delta",
            item_id="fc_1", delta='{"pa'),
        _ev("response.function_call_arguments.delta",
            item_id="fc_1", delta='ge":1}'),
        _ev("response.output_item.done", output_index=0, item=SimpleNamespace(
            type="function_call", id="fc_1", call_id="call_1",
            name="list_assets", arguments='{"page":1}',
        )),
        _ev("response.completed", response=SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=5, output_tokens=3,
                input_tokens_details=SimpleNamespace(cached_tokens=0),
            ),
        )),
    ]
    sdk = _FakeSDK(events)
    client = FoundryResponsesClient(sdk=sdk, model="gpt-5.4-mini")

    chunks = []
    async with await client.stream(
        system_prompt="sys",
        messages=[LLMMessage(role="user", content="hi")],
        tools=[ToolSpec("list_assets", "x", {"type": "object"})],
        max_output_tokens=100,
    ) as s:
        async for c in s:
            chunks.append(c)
        res = s.result

    starts = [c for c in chunks if c.kind == "tool_call_start"]
    ends = [c for c in chunks if c.kind == "tool_call_end"]
    deltas = [c for c in chunks if c.kind == "tool_call_delta"]
    assert len(starts) == 1
    assert starts[0].tool_name == "list_assets"
    assert "".join(d.tool_args_delta for d in deltas) == '{"page":1}'
    assert len(ends) == 1
    assert res.stop_reason == "tool_use"
    assert res.tool_calls == [{
        "id": "call_1",
        "name": "list_assets",
        "arguments": {"page": 1},
    }]


@pytest.mark.asyncio
async def test_cache_stable_tool_prefix_accepted_and_ignored():
    """FoundryResponsesClient accepts cache_stable_tool_prefix for a uniform
    LLMClient signature but ignores it (no prompt-cache markers on this path)."""
    events = [
        _ev("response.output_text.delta", delta="ok"),
        _ev("response.completed", response=SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=1, output_tokens=1,
                input_tokens_details=SimpleNamespace(cached_tokens=0),
            ),
        )),
    ]
    sdk = _FakeSDK(events)
    client = FoundryResponsesClient(sdk=sdk, model="gpt-5.4-mini")
    async with await client.stream(
        system_prompt="sys",
        messages=[LLMMessage(role="user", content="hi")],
        tools=[ToolSpec("t0", "d", {"type": "object"})],
        max_output_tokens=16, cache_stable_tool_prefix=1,
    ) as s:
        async for _ in s:
            pass
    assert "cache_stable_tool_prefix" not in sdk.last_call


def test_messages_to_responses_round_trip():
    msgs = [
        LLMMessage(role="user", content="hi"),
        LLMMessage(role="assistant", content=None, tool_calls=[{
            "id": "call_1", "name": "list_assets", "arguments": {"page": 1},
        }]),
        LLMMessage(role="tool", tool_call_id="call_1", content="[]"),
    ]
    out = _messages_to_responses("sys", msgs)
    # First entry is the system instructions injected as a system-role item.
    assert out[0] == {"role": "system",
                      "content": [{"type": "input_text", "text": "sys"}]}
    # User content uses input_text.
    assert out[1]["role"] == "user"
    assert out[1]["content"][0]["type"] == "input_text"
    # Assistant tool call uses a flat function_call item, NOT a role entry.
    assert out[2] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "list_assets",
        "arguments": '{"page": 1}',
    }
    # Tool result uses function_call_output, NOT role="tool".
    assert out[3] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "[]",
    }


def test_assistant_text_history_serializes_as_bare_string():
    # The Responses API "easy message" input allows only input_* content parts;
    # output_text is valid only inside a full message item (id/status/type), so
    # assistant text replayed on turn 2+ must serialize as a bare string. The
    # old {"content": [{"type": "output_text", ...}]} shape is rejected with a
    # 400 by responses.create — and the single-turn _ping test never hits it.
    out = _messages_to_responses("sys", [
        LLMMessage(role="user", content="hi"),
        LLMMessage(role="assistant", content="hello there"),
        LLMMessage(role="user", content="again"),
    ])
    asst = [it for it in out if it.get("role") == "assistant"]
    assert len(asst) == 1
    assert asst[0]["content"] == "hello there"


@pytest.mark.asyncio
async def test_response_incomplete_with_max_tokens_sets_max_tokens_stop_reason():
    events = [
        _ev("response.output_text.delta", delta="Partial "),
        _ev(
            "response.incomplete",
            response=SimpleNamespace(
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                usage=SimpleNamespace(
                    input_tokens=4, output_tokens=1,
                    input_tokens_details=SimpleNamespace(cached_tokens=0),
                ),
            ),
        ),
    ]
    sdk = _FakeSDK(events)
    client = FoundryResponsesClient(sdk=sdk, model="gpt-5.4-mini")
    async with await client.stream(
        system_prompt="sys",
        messages=[LLMMessage(role="user", content="hi")],
        tools=[], max_output_tokens=1,
    ) as s:
        async for _ in s:
            pass
        res = s.result
    assert res.stop_reason == "max_tokens"
    assert res.final_text == "Partial "
    assert res.usage.output_tokens == 1


@pytest.mark.asyncio
async def test_response_incomplete_with_content_filter_sets_error():
    events = [
        _ev(
            "response.incomplete",
            response=SimpleNamespace(
                incomplete_details=SimpleNamespace(reason="content_filter"),
                usage=SimpleNamespace(
                    input_tokens=4, output_tokens=0,
                    input_tokens_details=SimpleNamespace(cached_tokens=0),
                ),
            ),
        ),
    ]
    sdk = _FakeSDK(events)
    client = FoundryResponsesClient(sdk=sdk, model="gpt-5.4-mini")
    async with await client.stream(
        system_prompt="sys",
        messages=[LLMMessage(role="user", content="hi")],
        tools=[], max_output_tokens=100,
    ) as s:
        async for _ in s:
            pass
        res = s.result
    assert res.stop_reason == "error"
    assert "content_filter" in (res.error or "")


@pytest.mark.asyncio
async def test_response_failed_sets_error_with_code_and_message():
    events = [
        _ev(
            "response.failed",
            response=SimpleNamespace(
                error=SimpleNamespace(
                    code="server_error", message="upstream timeout",
                ),
            ),
        ),
    ]
    sdk = _FakeSDK(events)
    client = FoundryResponsesClient(sdk=sdk, model="gpt-5.4-mini")
    async with await client.stream(
        system_prompt="sys",
        messages=[LLMMessage(role="user", content="hi")],
        tools=[], max_output_tokens=100,
    ) as s:
        async for _ in s:
            pass
        res = s.result
    assert res.stop_reason == "error"
    assert "server_error" in (res.error or "")
    assert "upstream timeout" in (res.error or "")


@pytest.mark.asyncio
async def test_orphan_args_delta_is_ignored_without_crashing():
    """Defensive guard: if `function_call_arguments.delta` arrives without
    a preceding `output_item.added` to register the item_id, the client must
    drop the event rather than KeyError on the accumulator lookup."""
    events = [
        _ev("response.function_call_arguments.delta",
            item_id="never_announced", delta='{"oops": true}'),
        _ev("response.completed", response=SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=1, output_tokens=0,
                input_tokens_details=SimpleNamespace(cached_tokens=0),
            ),
        )),
    ]
    sdk = _FakeSDK(events)
    client = FoundryResponsesClient(sdk=sdk, model="gpt-5.4-mini")

    chunks = []
    async with await client.stream(
        system_prompt="sys",
        messages=[LLMMessage(role="user", content="hi")],
        tools=[], max_output_tokens=100,
    ) as s:
        async for c in s:
            chunks.append(c)
        res = s.result

    # No tool_call chunks should have been emitted for the orphan delta.
    assert all(c.kind != "tool_call_delta" for c in chunks)
    assert res.tool_calls == []
    assert res.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_response_failed_after_partial_text_populates_both_text_and_error():
    """When the stream emits partial text and then `response.failed`, the
    result should carry both: the partial output in final_text AND the
    failure detail in result.error. Lets the orchestrator surface either or
    both to the user."""
    events = [
        _ev("response.output_text.delta", delta="Partial "),
        _ev("response.output_text.delta", delta="output"),
        _ev(
            "response.failed",
            response=SimpleNamespace(
                error=SimpleNamespace(
                    code="server_error", message="upstream timeout",
                ),
            ),
        ),
    ]
    sdk = _FakeSDK(events)
    client = FoundryResponsesClient(sdk=sdk, model="gpt-5.4-mini")

    async with await client.stream(
        system_prompt="sys",
        messages=[LLMMessage(role="user", content="hi")],
        tools=[], max_output_tokens=100,
    ) as s:
        async for _ in s:
            pass
        res = s.result

    assert res.stop_reason == "error"
    assert res.final_text == "Partial output"
    assert "server_error" in (res.error or "")
    assert "upstream timeout" in (res.error or "")
