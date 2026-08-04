# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for OpenAICompatClient's tool-support probe + prompt-fallback."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List

import pytest

from src.api.blueprints.chat.llm.base import LLMMessage, ToolSpec
from src.api.blueprints.chat.llm.openai_compat_client import (
    OpenAICompatClient, _augment_system_prompt,
)

from ._fakes import (
    FakeSDK, ScriptedAsyncStream, chunk as _chunk, final_usage as _final_usage,
)


@pytest.mark.asyncio
async def test_compat_keeps_legacy_max_tokens_param():
    # vLLM/Ollama/LiteLLM speak the legacy `max_tokens`, not OpenAI's newer
    # `max_completion_tokens`; the compat client must keep sending the old name
    # even though its OpenAIClient parent now defaults to the new one.
    sdk = FakeSDK([_chunk(finish_reason="stop"), _final_usage()])
    client = OpenAICompatClient(sdk=sdk, model="qwen3", base_url="http://x/v1")
    async with await client.stream(
        system_prompt="sys",
        messages=[LLMMessage(role="user", content="hi")],
        tools=[], max_output_tokens=256,
    ) as s:
        async for _ in s:
            pass

    assert sdk.last_call["max_tokens"] == 256
    assert "max_completion_tokens" not in sdk.last_call


def _status_error(status_code: int = 400):
    """Build a real openai.APIStatusError with the given HTTP status, so the
    probe's error-classification (400/422 = unsupported vs everything else =
    transient) can be exercised the way it runs against a live server."""
    import httpx
    from openai import APIStatusError
    request = httpx.Request("POST", "http://x/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return APIStatusError("probe rejected", response=response, body=None)


class _ProbingSDK:
    """Custom SDK that tracks whether the probe path is taken and controls
    whether it succeeds or errors out. Differs from the vanilla FakeSDK
    because the probe path needs separate accounting from the streaming path.

    ``probe_error`` overrides what the probe raises on failure; it defaults to
    a definitive HTTP 400 (server rejected the ``tools`` param)."""

    def __init__(self, scripted_stream_events: List[Any],
                 *, probe_succeeds: bool, probe_error: Any = None) -> None:
        self._events = scripted_stream_events
        self._probe_succeeds = probe_succeeds
        self._probe_error = probe_error
        self.probe_count = 0
        self.stream_calls = 0
        self.last_stream_call: Any = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(
            create=self._create,
        ))

    async def _create(self, **kwargs: Any) -> Any:
        if kwargs.get("stream", False) is False:
            self.probe_count += 1
            if not self._probe_succeeds:
                raise self._probe_error or _status_error(400)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="ok"))])
        self.stream_calls += 1
        self.last_stream_call = kwargs
        return ScriptedAsyncStream(self._events)


@pytest.mark.asyncio
async def test_transient_probe_error_not_cached_uses_native_tools():
    # A network/timeout/5xx blip during the probe must NOT be recorded as
    # "tools unsupported" — that would permanently degrade a tools-capable
    # server to prompt-based dispatch. The client uses native tools this turn
    # and leaves the verdict unset so the next turn re-probes.
    sdk = _ProbingSDK([
        _chunk(content="ok"), _chunk(finish_reason="stop"), _final_usage(),
    ], probe_succeeds=False, probe_error=RuntimeError("connection reset"))
    client = OpenAICompatClient(sdk=sdk, model="m", base_url="http://x/v1")
    tools = [ToolSpec(name="t", description="",
                      input_schema={"type": "object"})]

    async with await client.stream(
        system_prompt="s", messages=[LLMMessage(role="user", content="go")],
        tools=tools, max_output_tokens=16,
    ) as s:
        async for _ in s:
            pass

    assert "tools" in sdk.last_stream_call       # native tools despite the blip
    assert client._supports_tools is None        # verdict was NOT cached
    assert sdk.probe_count == 1


@pytest.mark.asyncio
async def test_definitive_400_probe_rejection_is_cached_and_falls_back():
    # A genuine 400 (server rejects the tools param) is cached, so subsequent
    # turns skip the probe and use prompt-based dispatch.
    sdk = _ProbingSDK([
        _chunk(content="plain"), _chunk(finish_reason="stop"), _final_usage(),
    ], probe_succeeds=False, probe_error=_status_error(400))
    client = OpenAICompatClient(sdk=sdk, model="m", base_url="http://x/v1")
    tools = [ToolSpec(name="t", description="",
                      input_schema={"type": "object"})]

    async with await client.stream(
        system_prompt="s", messages=[LLMMessage(role="user", content="go")],
        tools=tools, max_output_tokens=16,
    ) as s:
        async for _ in s:
            pass

    assert client._supports_tools is False        # definitive verdict cached
    assert "tools" not in sdk.last_stream_call     # prompt fallback used


# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_tools_skips_probe_and_delegates():
    """With tools=[] there is nothing to probe — go straight through."""
    sdk = _ProbingSDK([
        _chunk(content="hi"), _chunk(finish_reason="stop"), _final_usage(),
    ], probe_succeeds=True)
    client = OpenAICompatClient(sdk=sdk, model="m",
                                 base_url="http://x/v1")

    async with await client.stream(
        system_prompt="s",
        messages=[LLMMessage(role="user", content="hi")],
        tools=[], max_output_tokens=16,
    ) as s:
        async for _ in s:
            pass

    assert sdk.probe_count == 0
    assert sdk.stream_calls == 1


@pytest.mark.asyncio
async def test_probe_success_uses_native_tools_path():
    """Probe succeeds → subsequent requests include the ``tools`` param."""
    sdk = _ProbingSDK([
        _chunk(content="done"), _chunk(finish_reason="stop"), _final_usage(),
    ], probe_succeeds=True)
    client = OpenAICompatClient(sdk=sdk, model="m",
                                 base_url="http://x/v1")
    tools = [ToolSpec(name="list_assets", description="",
                      input_schema={"type": "object"})]

    async with await client.stream(
        system_prompt="s",
        messages=[LLMMessage(role="user", content="go")],
        tools=tools, max_output_tokens=16,
    ) as s:
        async for _ in s:
            pass

    assert sdk.probe_count == 1
    # The streaming call must have carried the tools param through.
    assert "tools" in sdk.last_stream_call
    assert sdk.last_stream_call["tools"][0]["function"]["name"] == "list_assets"


@pytest.mark.asyncio
async def test_probe_failure_falls_back_to_prompt_protocol():
    """Probe errors → inject tool spec into system prompt + parse <tool_call>."""
    # The streamed reply is a plain text with a single <tool_call> block.
    reply_text = 'Sure. <tool_call>{"name":"list_assets","arguments":{"page":1}}</tool_call>'
    sdk = _ProbingSDK([
        _chunk(content=reply_text), _chunk(finish_reason="stop"),
        _final_usage(prompt=10, completion=5),
    ], probe_succeeds=False)
    client = OpenAICompatClient(sdk=sdk, model="m",
                                 base_url="http://x/v1")
    tools = [ToolSpec(name="list_assets", description="List",
                      input_schema={"type": "object"})]

    chunks = []
    async with await client.stream(
        system_prompt="base",
        messages=[LLMMessage(role="user", content="go")],
        tools=tools, max_output_tokens=64,
    ) as s:
        async for c in s:
            chunks.append(c)
        res = s.result

    # Probe counted once, stream call counted once. Next invocation should
    # skip the probe (cached).
    assert sdk.probe_count == 1
    assert sdk.stream_calls == 1

    # The stream call received an augmented system prompt (no tools param).
    sent = sdk.last_stream_call
    assert "tools" not in sent
    assert "Tool use (prompt protocol)" in sent["messages"][0]["content"]

    # Result reinterpreted as a tool_use.
    assert res.stop_reason == "tool_use"
    assert len(res.tool_calls) == 1
    assert res.tool_calls[0]["name"] == "list_assets"
    assert res.tool_calls[0]["arguments"] == {"page": 1}
    # Usage passes through unmodified.
    assert res.usage.input_tokens == 10
    assert res.usage.output_tokens == 5


@pytest.mark.asyncio
async def test_fallback_does_not_leak_tool_call_markup_to_user():
    # The <tool_call> protocol block is internal; it must never stream to the
    # user or land in the persisted final_text. The old impl forwarded every
    # chunk raw and parsed only afterward, leaking the XML on the fallback path.
    reply_text = 'Sure. <tool_call>{"name":"list_assets","arguments":{"page":1}}</tool_call>'
    sdk = _ProbingSDK([
        _chunk(content=reply_text), _chunk(finish_reason="stop"),
        _final_usage(prompt=10, completion=5),
    ], probe_succeeds=False)
    client = OpenAICompatClient(sdk=sdk, model="m", base_url="http://x/v1")
    tools = [ToolSpec(name="list_assets", description="List",
                      input_schema={"type": "object"})]

    texts = []
    async with await client.stream(
        system_prompt="base",
        messages=[LLMMessage(role="user", content="go")],
        tools=tools, max_output_tokens=64,
    ) as s:
        async for c in s:
            if c.kind == "text":
                texts.append(c.text)
        res = s.result

    streamed = "".join(texts)
    assert "<tool_call>" not in streamed
    assert "list_assets" not in streamed       # the JSON payload must not leak
    assert streamed.strip() == "Sure."          # only the prose before the block
    assert "<tool_call>" not in res.final_text
    assert res.final_text == "Sure."
    # The call is still extracted correctly.
    assert res.stop_reason == "tool_use"
    assert res.tool_calls[0]["name"] == "list_assets"


@pytest.mark.asyncio
async def test_fallback_suppresses_tool_call_block_split_across_chunks():
    # The <tool_call> marker arriving in fragments must still be fully
    # suppressed — the trailing-buffer behaviour the docstring promises.
    frags = ["Look ", "<tool", "_call>", '{"name":"t",',
             '"arguments":{}}', "</tool_call>"]
    sdk = _ProbingSDK(
        [_chunk(content=f) for f in frags]
        + [_chunk(finish_reason="stop"), _final_usage()],
        probe_succeeds=False,
    )
    client = OpenAICompatClient(sdk=sdk, model="m", base_url="http://x/v1")
    tools = [ToolSpec(name="t", description="",
                      input_schema={"type": "object"})]

    texts = []
    async with await client.stream(
        system_prompt="s",
        messages=[LLMMessage(role="user", content="hi")],
        tools=tools, max_output_tokens=64,
    ) as s:
        async for c in s:
            if c.kind == "text":
                texts.append(c.text)
        res = s.result

    streamed = "".join(texts)
    assert "<tool" not in streamed
    assert "tool_call" not in streamed
    assert streamed.strip() == "Look"
    assert res.stop_reason == "tool_use"
    assert res.tool_calls[0]["name"] == "t"


@pytest.mark.asyncio
async def test_fallback_reply_without_tool_block_stays_end_turn():
    sdk = _ProbingSDK([
        _chunk(content="just a plain answer"),
        _chunk(finish_reason="stop"), _final_usage(),
    ], probe_succeeds=False)
    client = OpenAICompatClient(sdk=sdk, model="m",
                                 base_url="http://x/v1")
    tools = [ToolSpec(name="t", description="", input_schema={})]

    async with await client.stream(
        system_prompt="s",
        messages=[LLMMessage(role="user", content="hi")],
        tools=tools, max_output_tokens=16,
    ) as s:
        async for _ in s:
            pass
        res = s.result

    assert res.stop_reason == "end_turn"
    assert res.tool_calls == []
    assert res.final_text == "just a plain answer"


@pytest.mark.asyncio
async def test_thinking_disabled_forwards_extra_body():
    """When the operator sets thinking_enabled=False, the client must send
    chat_template_kwargs.enable_thinking=false on every request. Covers the
    vLLM/Qwen3 path."""
    sdk = _ProbingSDK([
        _chunk(content="ok"), _chunk(finish_reason="stop"), _final_usage(),
    ], probe_succeeds=True)
    client = OpenAICompatClient(sdk=sdk, model="m",
                                 base_url="http://x/v1",
                                 thinking_enabled=False)

    async with await client.stream(
        system_prompt="s",
        messages=[LLMMessage(role="user", content="hi")],
        tools=[], max_output_tokens=16,
    ) as s:
        async for _ in s:
            pass

    assert sdk.last_stream_call["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False},
    }


@pytest.mark.asyncio
async def test_thinking_enabled_sends_no_extra_body():
    """Default (thinking on) matches the server's default — no kwarg sent —
    so we don't accidentally override a server-side template preference."""
    sdk = _ProbingSDK([
        _chunk(content="ok"), _chunk(finish_reason="stop"), _final_usage(),
    ], probe_succeeds=True)
    client = OpenAICompatClient(sdk=sdk, model="m",
                                 base_url="http://x/v1",
                                 thinking_enabled=True)

    async with await client.stream(
        system_prompt="s",
        messages=[LLMMessage(role="user", content="hi")],
        tools=[], max_output_tokens=16,
    ) as s:
        async for _ in s:
            pass

    assert "extra_body" not in sdk.last_stream_call


@pytest.mark.asyncio
async def test_disable_thinking_per_call_forwards_extra_body():
    """The auto-title path passes disable_thinking=True per request; the client
    must forward chat_template_kwargs.enable_thinking=false for that call even
    though the operator left thinking enabled at construction."""
    sdk = _ProbingSDK([
        _chunk(content="ok"), _chunk(finish_reason="stop"), _final_usage(),
    ], probe_succeeds=True)
    client = OpenAICompatClient(sdk=sdk, model="m",
                                base_url="http://x/v1",
                                thinking_enabled=True)

    async with await client.stream(
        system_prompt="s",
        messages=[LLMMessage(role="user", content="hi")],
        tools=[], max_output_tokens=16, disable_thinking=True,
    ) as s:
        async for _ in s:
            pass

    assert sdk.last_stream_call["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False},
    }


@pytest.mark.asyncio
async def test_cache_stable_tool_prefix_accepted_and_ignored():
    """The OpenAI-compat client accepts cache_stable_tool_prefix for a uniform
    LLMClient signature but ignores it — caching (where present) is server-side
    and the request carries no cache marker."""
    sdk = _ProbingSDK([
        _chunk(content="ok"), _chunk(finish_reason="stop"), _final_usage(),
    ], probe_succeeds=True)
    client = OpenAICompatClient(sdk=sdk, model="m", base_url="http://x/v1")
    async with await client.stream(
        system_prompt="s",
        messages=[LLMMessage(role="user", content="hi")],
        tools=[], max_output_tokens=16,
        cache_stable_tool_prefix=3,
    ) as s:
        async for _ in s:
            pass
    assert "cache_stable_tool_prefix" not in sdk.last_stream_call


def test_augment_system_prompt_includes_tool_names_and_schemas():
    text = _augment_system_prompt(
        "base prompt",
        [ToolSpec(name="list_assets", description="List them",
                  input_schema={"type": "object", "properties": {}})],
    )
    assert "base prompt" in text
    assert "list_assets: List them" in text
    assert "<tool_call>" in text


@pytest.mark.asyncio
async def test_probe_runs_once_even_across_many_streams():
    sdk = _ProbingSDK([
        _chunk(content="x"), _chunk(finish_reason="stop"), _final_usage(),
    ] * 3, probe_succeeds=True)
    client = OpenAICompatClient(sdk=sdk, model="m",
                                 base_url="http://x/v1")
    tools = [ToolSpec(name="t", description="", input_schema={})]

    for _ in range(3):
        async with await client.stream(
            system_prompt="s",
            messages=[LLMMessage(role="user", content="hi")],
            tools=tools, max_output_tokens=16,
        ) as s:
            async for _ in s:
                pass

    assert sdk.probe_count == 1
    assert sdk.stream_calls == 3
