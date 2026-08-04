# SPDX-License-Identifier: AGPL-3.0-or-later
"""
AWS Bedrock LLM client (Converse API).

Wraps ``boto3 bedrock-runtime.converse_stream`` and translates Converse
stream events into the generic ``LLMChunk`` / ``StreamResult`` shapes the
chat orchestrator already consumes for Anthropic / OpenAI / OpenAI-compat.

boto3 is sync; we run the underlying call via ``asyncio.to_thread`` so the
async-iteration contract from ``LLMClient`` still holds. Each event
arrives as a plain dict keyed by the event-type name — see
https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ConverseStream.html
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Literal, Optional, cast

from botocore.exceptions import ClientError

from .base import (LLMChunk, LLMMessage, StreamResult, ToolSpec,
                   messages_to_bedrock, to_bedrock_spec)
from .openai_client import _safe_parse_tool_args


# Map Bedrock Converse stopReason → internal stop_reason enum on
# StreamResult. Unknown / unhandled reasons collapse to "end_turn" so the
# session loop doesn't get stuck — explicit content filtering and guardrail
# events surface as "error".
_StopReason = Literal["end_turn", "tool_use", "error", "max_tokens"]

_STOP_REASON_MAP: Dict[str, str] = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "stop_sequence": "end_turn",
    "content_filtered": "error",
    "guardrail_intervened": "error",
}


# Bedrock prompt caching is model-specific: only the Claude 3.5 / 3.7 / 4.x
# lines and the Amazon Nova family accept Converse ``cachePoint`` blocks.
# Sending one to any other model (Mistral, Llama, Titan, Cohere, or the OLDER
# Claude 3.0 line, which predates caching) raises a ValidationException — so the
# markers are version-qualified (``claude-3-5``, not a bare ``claude``) to keep
# ``claude-3-sonnet`` / ``claude-3-haiku`` / ``claude-3-opus`` excluded.
# Extend this tuple when AWS adds caching to new model families.
_CACHE_CAPABLE_MODEL_MARKERS = (
    "claude-3-5", "claude-3-7", "claude-sonnet-4", "claude-opus-4",
    "claude-haiku-4", "nova-micro", "nova-lite", "nova-pro",
)


def _model_supports_prompt_caching(model_id: str) -> bool:
    """True if ``model_id`` accepts Converse ``cachePoint`` blocks. Matches the
    region/inference-profile prefixes too (``us.anthropic.claude-3-5-…``) via a
    plain substring test on the lowercased id."""
    m = (model_id or "").lower()
    return any(marker in m for marker in _CACHE_CAPABLE_MODEL_MARKERS)


class BedrockClient:
    provider = "aws_bedrock"

    def __init__(self, *, runtime: Any, model: str,
                 prompt_cache: bool, thinking_enabled: bool) -> None:
        # ``runtime`` is an authenticated boto3 ``bedrock-runtime`` client.
        # The factory (make_llm_client) handles credential resolution and
        # optional STS assume-role hops; this class stays unaware of auth.
        self._runtime = runtime
        self.model = model
        self._prompt_cache = prompt_cache
        self._thinking_enabled = thinking_enabled

    async def stream(self, *, system_prompt: str,
                     messages: List[LLMMessage], tools: List[ToolSpec],
                     max_output_tokens: int,
                     cache_stable_tool_prefix: Optional[int] = None,
                     ) -> "_BedrockStream":
        return _BedrockStream(
            runtime=self._runtime, model=self.model,
            system_prompt=system_prompt,
            messages=messages_to_bedrock(messages),
            tools=to_bedrock_spec(tools) if tools else None,
            max_output_tokens=max_output_tokens,
            prompt_cache=self._prompt_cache,
            cache_stable_tool_prefix=cache_stable_tool_prefix,
        )


class _BedrockStream:
    def __init__(self, *, runtime: Any, model: str,
                 system_prompt: str, messages: List[Dict[str, Any]],
                 tools: Optional[Dict[str, Any]], max_output_tokens: int,
                 prompt_cache: bool = False,
                 cache_stable_tool_prefix: Optional[int] = None) -> None:
        self._runtime = runtime
        self._model = model
        self._system_prompt = system_prompt
        self._messages = messages
        self._tools = tools
        self._max_out = max_output_tokens
        self._prompt_cache = prompt_cache
        self._cache_stable_tool_prefix = cache_stable_tool_prefix
        self._stream_iter: Optional[Any] = None
        self._result = StreamResult(stop_reason="end_turn")

    async def __aenter__(self) -> "_BedrockStream":
        kwargs: Dict[str, Any] = {
            "modelId": self._model,
            "messages": self._messages,
            "inferenceConfig": {"maxTokens": self._max_out},
        }
        # Cache the system + tools prefix — the ~14k-token block re-sent on EVERY
        # agentic-loop iteration. A cachePoint marks the cacheable prefix
        # boundary; iterations 2..N (and later turns within the ~5-min TTL) then
        # read it at ~0.1x instead of full price, and Bedrock reports those as
        # cacheReadInputTokens — which also drops the per-turn inputTokens. Gated
        # on model support so a provider row with prompt_cache=True but a
        # non-capable model (e.g. Mistral) isn't sent an invalid block.
        #
        # cache_stable_tool_prefix is the count of LEADING tools that are stable
        # across turns/iterations. With tool routing ON the advertised list is
        # [stable core tools][variable retrieved tail], so the cachePoint goes
        # right AFTER the stable prefix — a cachePoint at the end of the whole
        # list would be busted every turn as the tail changes. None (routing
        # OFF / legacy callers) places it after the whole list, unchanged.
        cache = self._prompt_cache and _model_supports_prompt_caching(self._model)
        if self._system_prompt:
            system_blocks: List[Dict[str, Any]] = [{"text": self._system_prompt}]
            if cache:
                system_blocks.append({"cachePoint": {"type": "default"}})
            kwargs["system"] = system_blocks
        if self._tools:
            tools_list = self._tools["tools"]
            n = self._cache_stable_tool_prefix
            if n is None:
                n = len(tools_list)
            # n == 0 (no stable leading tool — degenerate registry) would put the
            # cachePoint before every tool, caching nothing in the tool block and
            # shifting per turn as the list changes; skip it (system cachePoint
            # still applies) so the marker only lands after a real stable prefix.
            if cache and n > 0:
                kwargs["toolConfig"] = {
                    "tools": [*tools_list[:n],
                              {"cachePoint": {"type": "default"}},
                              *tools_list[n:]],
                }
            else:
                kwargs["toolConfig"] = self._tools

        # converse_stream is sync; offload to a worker thread so we don't
        # block the event loop while boto3 does its handshake.
        try:
            response = await asyncio.to_thread(
                self._runtime.converse_stream, **kwargs,
            )
        except ClientError as exc:
            # Mistral 7B/Mixtral and a handful of other Bedrock models reject
            # the dedicated `system` parameter with a ValidationException.
            # Retry once with the system prompt inlined into the first user
            # message — preserves Anthropic prompt-caching on models that DO
            # support `system` while still working for the others.
            err = exc.response.get("Error", {}) if hasattr(exc, "response") else {}
            code = err.get("Code", "")
            message = (err.get("Message") or "").lower()
            if (
                code == "ValidationException"
                and "system" in kwargs
                and "system" in message
            ):
                kwargs.pop("system", None)
                kwargs["messages"] = _inline_system(
                    self._system_prompt, kwargs["messages"],
                )
                response = await asyncio.to_thread(
                    self._runtime.converse_stream, **kwargs,
                )
            else:
                raise

        # botocore returns an EventStream — iterable but not an iterator.
        # Wrap with iter() so the downstream next(...) calls in _aiter work.
        self._stream_iter = iter(response["stream"])
        return self

    async def __aexit__(self, *exc) -> None:
        # boto3 stream iterators have no .close(); dropping the reference
        # is enough.
        self._stream_iter = None

    def __aiter__(self) -> AsyncIterator[LLMChunk]:
        return self._aiter()

    async def _aiter(self) -> AsyncIterator[LLMChunk]:
        if self._stream_iter is None:
            return

        current_tool_id: Optional[str] = None
        current_tool_name: Optional[str] = None
        tool_args_parts: List[str] = []
        text_parts: List[str] = []

        # Sentinel for end-of-stream. ``asyncio.to_thread(next, it)`` cannot
        # propagate StopIteration out of a Future (PEP 479) so we wrap it.
        _SENTINEL = object()

        def _next_or_sentinel(it: Any) -> Any:
            try:
                return next(it)
            except StopIteration:
                return _SENTINEL

        loop_done = False
        while not loop_done:
            event = await asyncio.to_thread(_next_or_sentinel, self._stream_iter)
            if event is _SENTINEL:
                break

            if "messageStart" in event:
                continue

            if "contentBlockStart" in event:
                start = event["contentBlockStart"].get("start") or {}
                tu = start.get("toolUse")
                if tu:
                    current_tool_id = tu.get("toolUseId") or ""
                    current_tool_name = tu.get("name") or ""
                    tool_args_parts = []
                    yield LLMChunk(
                        kind="tool_call_start",
                        tool_call_id=current_tool_id,
                        tool_name=current_tool_name,
                    )
                continue

            if "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta") or {}
                text = delta.get("text")
                if text:
                    text_parts.append(text)
                    yield LLMChunk(kind="text", text=text)
                tu_delta = delta.get("toolUse")
                if tu_delta and "input" in tu_delta:
                    args_part = tu_delta["input"] or ""
                    tool_args_parts.append(args_part)
                    yield LLMChunk(
                        kind="tool_call_delta",
                        tool_call_id=current_tool_id,
                        tool_args_delta=args_part,
                    )
                continue

            if "contentBlockStop" in event:
                if current_tool_id is not None:
                    args = _safe_parse_tool_args(
                        "".join(tool_args_parts),
                        tool_name=current_tool_name or "",
                    )
                    self._result.tool_calls.append({
                        "id": current_tool_id,
                        "name": current_tool_name,
                        "arguments": args,
                    })
                    yield LLMChunk(
                        kind="tool_call_end",
                        tool_call_id=current_tool_id,
                        tool_name=current_tool_name,
                    )
                    current_tool_id = None
                    current_tool_name = None
                    tool_args_parts = []
                continue

            if "messageStop" in event:
                aws_reason = event["messageStop"].get("stopReason") or "end_turn"
                self._result.stop_reason = cast(
                    _StopReason, _STOP_REASON_MAP.get(aws_reason, "end_turn"),
                )
                continue

            if "metadata" in event:
                usage = event["metadata"].get("usage") or {}
                self._result.usage.input_tokens = int(
                    usage.get("inputTokens", 0) or 0,
                )
                self._result.usage.output_tokens = int(
                    usage.get("outputTokens", 0) or 0,
                )
                self._result.usage.cached_tokens = int(
                    usage.get("cacheReadInputTokens", 0) or 0,
                )
                continue

            # internalServerException / modelStreamErrorException etc.
            # Surface as a hard error so the test ping reports it.
            for err_key in (
                "internalServerException", "modelStreamErrorException",
                "validationException", "throttlingException",
                "serviceUnavailableException",
            ):
                if err_key in event:
                    err = event[err_key].get("message") or err_key
                    self._result.stop_reason = cast(_StopReason, "error")
                    self._result.error = f"{err_key}: {err}"
                    loop_done = True
                    break

        self._result.final_text = "".join(text_parts)

    @property
    def result(self) -> StreamResult:
        return self._result


def _inline_system(
    system_prompt: str, messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Fold the system prompt into the first user message.

    Used as a one-shot fallback when a Bedrock model rejects the dedicated
    `system` parameter (e.g. mistral.mistral-7b-instruct-v0:2). The first
    user message gets a leading text block carrying the system content;
    later messages are untouched. If the conversation starts with a non-user
    turn (rare), a synthetic user message is prepended."""
    if not system_prompt:
        return messages
    if not messages:
        return [{"role": "user", "content": [{"text": system_prompt}]}]
    first = messages[0]
    if first.get("role") == "user":
        merged_content: List[Dict[str, Any]] = [{"text": f"{system_prompt}\n\n"}]
        merged_content.extend(first.get("content", []) or [])
        new_first = {**first, "content": merged_content}
        return [new_first, *messages[1:]]
    # Conversation starts with assistant/tool — insert a synthetic user turn.
    return [
        {"role": "user", "content": [{"text": system_prompt}]},
        *messages,
    ]
