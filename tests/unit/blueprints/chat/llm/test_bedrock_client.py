# SPDX-License-Identifier: AGPL-3.0-or-later
"""BedrockClient unit tests.

The boto3 ``bedrock-runtime`` client is mocked end-to-end — no live AWS.
Tests focus on the event-translation in _BedrockStream._aiter and on
``stream`` setup (system prompt, message conversion, tool config).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from src.api.blueprints.chat.llm.base import LLMMessage, ToolSpec
from src.api.blueprints.chat.llm.bedrock_client import BedrockClient


def _events_iter(*events):
    """Build the iterator boto3's converse_stream returns under ``stream``."""
    return iter(events)


def _converse_response(events):
    """Mimic boto3's response: ``{"stream": <iterator>}``."""
    return {"stream": _events_iter(*events)}


def _run(coro):
    return asyncio.run(coro)


async def _drain(client, messages=None, tools=None):
    msgs = messages or [LLMMessage(role="user", content="hi")]
    tlist = tools or []
    async with await client.stream(
        system_prompt="be terse", messages=msgs, tools=tlist,
        max_output_tokens=128,
    ) as s:
        chunks = [c async for c in s]
    return chunks, s.result


class TestBedrockTextStream:
    def test_text_delta_yields_text_chunk_and_accumulates_final_text(self):
        runtime = MagicMock(name="bedrock-runtime")
        runtime.converse_stream.return_value = _converse_response([
            {"messageStart": {"role": "assistant"}},
            {"contentBlockDelta": {
                "delta": {"text": "hello "}, "contentBlockIndex": 0,
            }},
            {"contentBlockDelta": {
                "delta": {"text": "world"}, "contentBlockIndex": 0,
            }},
            {"messageStop": {"stopReason": "end_turn"}},
            {"metadata": {"usage": {
                "inputTokens": 7, "outputTokens": 2, "totalTokens": 9,
            }}},
        ])

        client = BedrockClient(
            runtime=runtime, model="anthropic.claude-3-5-sonnet-20241022-v2:0",
            prompt_cache=False, thinking_enabled=False,
        )
        chunks, result = _run(_drain(client))

        kinds = [c.kind for c in chunks]
        assert kinds == ["text", "text"]
        assert "".join(c.text or "" for c in chunks) == "hello world"
        assert result.final_text == "hello world"
        assert result.stop_reason == "end_turn"
        assert result.usage.input_tokens == 7
        assert result.usage.output_tokens == 2


class TestBedrockToolUseStream:
    def test_tool_use_emits_start_delta_end_and_populates_result(self):
        runtime = MagicMock(name="bedrock-runtime")
        runtime.converse_stream.return_value = _converse_response([
            {"messageStart": {"role": "assistant"}},
            {"contentBlockStart": {
                "start": {"toolUse": {"toolUseId": "tu_1",
                                      "name": "search_assets"}},
                "contentBlockIndex": 0,
            }},
            {"contentBlockDelta": {
                "delta": {"toolUse": {"input": '{"q":'}},
                "contentBlockIndex": 0,
            }},
            {"contentBlockDelta": {
                "delta": {"toolUse": {"input": '"foo"}'}},
                "contentBlockIndex": 0,
            }},
            {"contentBlockStop": {"contentBlockIndex": 0}},
            {"messageStop": {"stopReason": "tool_use"}},
            {"metadata": {"usage": {"inputTokens": 12, "outputTokens": 4}}},
        ])

        client = BedrockClient(
            runtime=runtime, model="anthropic.claude-3-5-sonnet-20241022-v2:0",
            prompt_cache=False, thinking_enabled=False,
        )
        tools = [ToolSpec(name="search_assets",
                          description="Find assets",
                          input_schema={"type": "object",
                                        "properties": {"q": {"type": "string"}}})]
        chunks, result = _run(_drain(client, tools=tools))

        kinds = [c.kind for c in chunks]
        assert kinds == [
            "tool_call_start",
            "tool_call_delta", "tool_call_delta",
            "tool_call_end",
        ]
        assert result.stop_reason == "tool_use"
        assert result.tool_calls == [{
            "id": "tu_1", "name": "search_assets",
            "arguments": {"q": "foo"},
        }]


class TestBedrockStopReasonMapping:
    @pytest.mark.parametrize("aws_reason,expected", [
        ("end_turn", "end_turn"),
        ("tool_use", "tool_use"),
        ("max_tokens", "max_tokens"),
        ("stop_sequence", "end_turn"),
        ("content_filtered", "error"),
    ])
    def test_stop_reasons_map_to_internal_enum(self, aws_reason, expected):
        runtime = MagicMock()
        runtime.converse_stream.return_value = _converse_response([
            {"messageStart": {"role": "assistant"}},
            {"messageStop": {"stopReason": aws_reason}},
        ])
        client = BedrockClient(
            runtime=runtime, model="x",
            prompt_cache=False, thinking_enabled=False,
        )
        _, result = _run(_drain(client))
        assert result.stop_reason == expected


class TestBedrockUsageWithPromptCache:
    def test_cache_read_tokens_populate_cached_tokens(self):
        runtime = MagicMock()
        runtime.converse_stream.return_value = _converse_response([
            {"messageStart": {"role": "assistant"}},
            {"contentBlockDelta": {"delta": {"text": "ok"},
                                   "contentBlockIndex": 0}},
            {"messageStop": {"stopReason": "end_turn"}},
            {"metadata": {"usage": {
                "inputTokens": 20, "outputTokens": 1,
                "cacheReadInputTokens": 18,
            }}},
        ])
        client = BedrockClient(
            runtime=runtime, model="x",
            prompt_cache=True, thinking_enabled=False,
        )
        _, result = _run(_drain(client))
        assert result.usage.cached_tokens == 18


class TestBedrockStreamSetup:
    def test_stream_passes_system_messages_tools_and_max_tokens(self):
        runtime = MagicMock()
        runtime.converse_stream.return_value = _converse_response([
            {"messageStart": {"role": "assistant"}},
            {"messageStop": {"stopReason": "end_turn"}},
        ])
        client = BedrockClient(
            runtime=runtime,
            model="anthropic.claude-3-5-sonnet-20241022-v2:0",
            prompt_cache=False, thinking_enabled=False,
        )
        tools = [ToolSpec(name="t", description="d", input_schema={})]
        _run(_drain(client, tools=tools))

        kwargs = runtime.converse_stream.call_args.kwargs
        assert kwargs["modelId"] == "anthropic.claude-3-5-sonnet-20241022-v2:0"
        assert kwargs["system"] == [{"text": "be terse"}]
        assert kwargs["messages"] == [
            {"role": "user", "content": [{"text": "hi"}]},
        ]
        assert kwargs["toolConfig"] == {
            "tools": [{"toolSpec": {
                "name": "t", "description": "d",
                "inputSchema": {"json": {}},
            }}],
        }
        assert kwargs["inferenceConfig"] == {"maxTokens": 128}


class TestBedrockEventStreamWrapping:
    """boto3's converse_stream returns an EventStream (iterable, not an
    iterator). _BedrockStream must wrap it with iter() before next() is used.
    Regression for the production crash 'EventStream object is not an iterator'."""

    def test_non_iterator_iterable_is_wrapped(self):
        # An object that is iterable but NOT itself an iterator (no __next__).
        # Plain list satisfies this: list.__iter__ exists, list.__next__ does not.
        events = [
            {"messageStart": {"role": "assistant"}},
            {"contentBlockDelta": {"delta": {"text": "ok"},
                                   "contentBlockIndex": 0}},
            {"messageStop": {"stopReason": "end_turn"}},
        ]

        # Custom iterable that's not an iterator — exactly like EventStream.
        class _Iterable:
            def __init__(self, items): self._items = items
            def __iter__(self): return iter(self._items)
            # Deliberately no __next__.

        runtime = MagicMock()
        runtime.converse_stream.return_value = {"stream": _Iterable(events)}

        client = BedrockClient(
            runtime=runtime, model="x",
            prompt_cache=False, thinking_enabled=False,
        )
        chunks, result = _run(_drain(client))

        # If iter() wasn't applied, _aiter would raise TypeError. Reaching
        # here with text output proves the wrapping is in place.
        assert result.final_text == "ok"
        assert any(c.kind == "text" for c in chunks)


class TestBedrockSystemMessageFallback:
    """Some models reject the `system` Converse parameter; the client should
    retry once with the system prompt inlined into the first user message."""

    def _make_validation_error(self, message: str):
        from botocore.exceptions import ClientError
        return ClientError(
            error_response={"Error": {"Code": "ValidationException",
                                      "Message": message}},
            operation_name="ConverseStream",
        )

    def test_retries_inlining_system_on_validation_error(self):
        runtime = MagicMock(name="bedrock-runtime")
        # First call raises the AWS error; second call returns a working stream.
        runtime.converse_stream.side_effect = [
            self._make_validation_error(
                "This model doesn't support system messages."),
            {"stream": iter([
                {"messageStart": {"role": "assistant"}},
                {"contentBlockDelta": {"delta": {"text": "OK"},
                                       "contentBlockIndex": 0}},
                {"messageStop": {"stopReason": "end_turn"}},
            ])},
        ]

        client = BedrockClient(
            runtime=runtime, model="mistral.mixtral-8x7b-instruct-v0:1",
            prompt_cache=False, thinking_enabled=False,
        )
        chunks, result = _run(_drain(client))

        assert result.final_text == "OK"
        assert runtime.converse_stream.call_count == 2
        # Second call must NOT contain the dedicated system arg, AND the
        # first user message must lead with the system prompt text.
        retry_kwargs = runtime.converse_stream.call_args_list[1].kwargs
        assert "system" not in retry_kwargs
        first_user_blocks = retry_kwargs["messages"][0]["content"]
        assert first_user_blocks[0]["text"].startswith("be terse")
        # Original user content survives after the inlined system text.
        assert any(block.get("text", "").strip().endswith("hi")
                   or block.get("text") == "hi" for block in first_user_blocks)

    def test_unrelated_validation_error_propagates(self):
        runtime = MagicMock(name="bedrock-runtime")
        runtime.converse_stream.side_effect = self._make_validation_error(
            "Model capacity exceeded."
        )

        client = BedrockClient(
            runtime=runtime, model="x",
            prompt_cache=False, thinking_enabled=False,
        )
        with pytest.raises(Exception):  # ClientError is fine to bubble
            _run(_drain(client))
        # Only the first call happened — no retry on unrelated errors.
        assert runtime.converse_stream.call_count == 1


class TestBedrockPromptCachePoints:
    """prompt_cache=True injects Converse ``cachePoint`` markers on the
    system + tools prefix so the ~14k-token block re-sent on every agentic-loop
    iteration is read from cache (~0.1x) on iterations 2..N instead of being
    re-billed — but ONLY for caching-capable models (a cachePoint on e.g.
    Mistral is a ValidationException)."""

    def _converse_kwargs(self, model, prompt_cache, with_tools=True):
        runtime = MagicMock(name="bedrock-runtime")
        runtime.converse_stream.return_value = _converse_response([
            {"messageStart": {"role": "assistant"}},
            {"contentBlockDelta": {"delta": {"text": "ok"},
                                   "contentBlockIndex": 0}},
            {"messageStop": {"stopReason": "end_turn"}},
        ])
        client = BedrockClient(runtime=runtime, model=model,
                               prompt_cache=prompt_cache, thinking_enabled=False)
        tools = ([ToolSpec(name="t", description="d", input_schema={})]
                 if with_tools else [])
        _run(_drain(client, tools=tools))
        return runtime.converse_stream.call_args.kwargs

    def test_capable_model_adds_cachepoints_to_system_and_tools(self):
        kwargs = self._converse_kwargs(
            "anthropic.claude-3-5-sonnet-20241022-v2:0", prompt_cache=True)
        # cachePoint trails the system text...
        assert kwargs["system"] == [
            {"text": "be terse"}, {"cachePoint": {"type": "default"}},
        ]
        # ...and the tool specs (the real spec still precedes it).
        assert kwargs["toolConfig"]["tools"][0]["toolSpec"]["name"] == "t"
        assert kwargs["toolConfig"]["tools"][-1] == {
            "cachePoint": {"type": "default"}}

    def test_cache_disabled_emits_no_cachepoints(self):
        kwargs = self._converse_kwargs(
            "anthropic.claude-3-5-sonnet-20241022-v2:0", prompt_cache=False)
        assert kwargs["system"] == [{"text": "be terse"}]
        assert all("cachePoint" not in t for t in kwargs["toolConfig"]["tools"])

    def test_non_capable_model_never_gets_cachepoints_even_when_enabled(self):
        # Mistral-large carries prompt_cache=True in the dev provider row but
        # does NOT support caching — emitting a cachePoint would 400.
        kwargs = self._converse_kwargs(
            "mistral.mistral-large-2402-v1:0", prompt_cache=True)
        assert kwargs["system"] == [{"text": "be terse"}]
        assert all("cachePoint" not in t for t in kwargs["toolConfig"]["tools"])

    def _converse_kwargs_with_prefix(self, model, tools,
                                     cache_stable_tool_prefix):
        """Like _converse_kwargs but with a multi-tool list and an explicit
        stable-prefix length, so we can assert WHERE the cachePoint lands."""
        runtime = MagicMock(name="bedrock-runtime")
        runtime.converse_stream.return_value = _converse_response([
            {"messageStart": {"role": "assistant"}},
            {"contentBlockDelta": {"delta": {"text": "ok"},
                                   "contentBlockIndex": 0}},
            {"messageStop": {"stopReason": "end_turn"}},
        ])
        client = BedrockClient(runtime=runtime, model=model,
                               prompt_cache=True, thinking_enabled=False)

        async def _go():
            async with await client.stream(
                system_prompt="be terse",
                messages=[LLMMessage(role="user", content="hi")],
                tools=tools, max_output_tokens=128,
                cache_stable_tool_prefix=cache_stable_tool_prefix,
            ) as s:
                async for _ in s:
                    pass
        _run(_go())
        return runtime.converse_stream.call_args.kwargs

    def test_cachepoint_lands_after_stable_prefix_not_at_end(self):
        """Routing ON: with a stable prefix of N leading tools the cachePoint is
        inserted right after the Nth tool. The cached system+core block is then
        byte-identical across turns/iterations even though the variable
        retrieved tail that follows changes per turn — which would bust a
        cachePoint placed at the very end (the bug this fixes)."""
        tools = [ToolSpec(name=f"t{i}", description="d", input_schema={})
                 for i in range(5)]
        kwargs = self._converse_kwargs_with_prefix(
            "anthropic.claude-3-5-sonnet-20241022-v2:0", tools,
            cache_stable_tool_prefix=2)
        out = kwargs["toolConfig"]["tools"]
        # 5 real tool specs + exactly one cachePoint marker, inserted at idx 2.
        assert sum("cachePoint" in b for b in out) == 1
        assert out[0]["toolSpec"]["name"] == "t0"
        assert out[1]["toolSpec"]["name"] == "t1"
        assert out[2] == {"cachePoint": {"type": "default"}}
        assert out[3]["toolSpec"]["name"] == "t2"
        assert out[-1]["toolSpec"]["name"] == "t4"

    def test_cachepoint_prefix_none_keeps_trailing_position(self):
        """cache_stable_tool_prefix=None (routing OFF / legacy callers) keeps
        the cachePoint at the END of the tools list — today's behavior."""
        tools = [ToolSpec(name=f"t{i}", description="d", input_schema={})
                 for i in range(3)]
        kwargs = self._converse_kwargs_with_prefix(
            "anthropic.claude-3-5-sonnet-20241022-v2:0", tools,
            cache_stable_tool_prefix=None)
        out = kwargs["toolConfig"]["tools"]
        assert out[-1] == {"cachePoint": {"type": "default"}}
        assert [b["toolSpec"]["name"] for b in out if "toolSpec" in b] == \
            ["t0", "t1", "t2"]

    def test_cachepoint_prefix_equal_to_len_is_trailing(self):
        """A stable prefix equal to the whole list (routing OFF passes
        len(tools)) places the cachePoint at the end — identical to None, so
        the flag-OFF request shape is unchanged whichever value the service
        sends."""
        tools = [ToolSpec(name=f"t{i}", description="d", input_schema={})
                 for i in range(3)]
        kwargs = self._converse_kwargs_with_prefix(
            "anthropic.claude-3-5-sonnet-20241022-v2:0", tools,
            cache_stable_tool_prefix=3)
        out = kwargs["toolConfig"]["tools"]
        assert sum("cachePoint" in b for b in out) == 1
        assert out[-1] == {"cachePoint": {"type": "default"}}

    @pytest.mark.parametrize("model,supported", [
        ("anthropic.claude-3-5-sonnet-20241022-v2:0", True),
        ("anthropic.claude-3-5-haiku-20241022-v1:0", True),
        ("anthropic.claude-3-7-sonnet-20250219-v1:0", True),
        ("us.anthropic.claude-sonnet-4-20250514-v1:0", True),
        ("anthropic.claude-opus-4-8", True),
        ("amazon.nova-pro-v1:0", True),
        # Claude 3.0 predates Bedrock prompt caching — must be excluded.
        ("anthropic.claude-3-sonnet-20240229-v1:0", False),
        ("anthropic.claude-3-haiku-20240307-v1:0", False),
        ("anthropic.claude-3-opus-20240229-v1:0", False),
        ("mistral.mistral-large-2402-v1:0", False),
        ("", False),
    ])
    def test_model_capability_gate(self, model, supported):
        from src.api.blueprints.chat.llm.bedrock_client import (
            _model_supports_prompt_caching)
        assert _model_supports_prompt_caching(model) is supported
