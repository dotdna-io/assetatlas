# SPDX-License-Identifier: AGPL-3.0-or-later
"""
OpenAI LLM client.

Thin wrapper over ``openai.AsyncOpenAI`` that streams chat.completions and
translates the chunks into generic ``LLMChunk`` / ``StreamResult`` shapes
so the orchestration service can treat Anthropic and OpenAI interchangeably.

Tool-call arguments stream as JSON fragments over multiple chunks; we
accumulate per tool_call index and finalise at ``finish_reason == "tool_calls"``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI

from .base import (LLMChunk, LLMMessage, StreamResult, ToolSpec,
                   to_openai_spec)

logger = logging.getLogger(__name__)


def _safe_parse_tool_args(raw: str, *, tool_name: str) -> Dict[str, Any]:
    """Parse streamed tool-call arguments, recovering from model mistakes.

    The model occasionally emits text that is either not valid JSON (e.g.
    a single quoted key, an unterminated string, leaked ``<tool_call>`` XML)
    or JSON whose top level is not an object (a bare string, a list of
    filters, etc.). Raising from here kills the entire SSE stream, which
    looks to the user like the chatbot is dead. Instead we log the raw
    payload and return an object the downstream schema validator can
    reject with a clear "unknown parameter" / "missing required" message,
    letting the tool loop retry on the next iteration.
    """
    text = raw or "{}"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(
            "Tool %r emitted malformed JSON arguments (%s); substituting "
            "empty args. Raw (first 500 chars): %r",
            tool_name, e, text[:500],
        )
        return {}
    if not isinstance(parsed, dict):
        logger.warning(
            "Tool %r emitted non-object JSON arguments (%s); substituting "
            "empty args. Raw (first 500 chars): %r",
            tool_name, type(parsed).__name__, text[:500],
        )
        return {}
    return parsed


@dataclass
class _PendingToolCall:
    """Accumulator for one streaming tool call (keyed by OpenAI's ``index``).

    ``announced`` tracks whether we've already emitted the one-time
    ``tool_call_start`` chunk for this call, so we don't duplicate it on
    every subsequent args fragment.
    """
    id: str = ""
    name: str = ""
    args_parts: List[str] = field(default_factory=list)
    announced: bool = False


class OpenAIClient:
    provider = "openai"

    # chat.completions token-limit param name. Hosted OpenAI (and the Azure
    # Foundry openai_chat path, which reuses this client) require
    # `max_completion_tokens`: reasoning models (o1/o3/o4/gpt-5) reject the
    # legacy `max_tokens` outright, and the new name is accepted by every
    # current OpenAI chat model. OpenAICompatClient overrides this back to
    # `max_tokens` for self-hosted servers (vLLM/Ollama/LiteLLM).
    token_limit_param = "max_completion_tokens"

    def __init__(self, *, sdk: AsyncOpenAI, model: str,
                 prompt_cache: bool = False,
                 extra_body: Optional[Dict[str, Any]] = None):
        # prompt_cache is a no-op for OpenAI — their caching is automatic on
        # the server side (2024+), driven by prompt-prefix match. Kept as a
        # kwarg so the factory can pass a uniform signature across providers.
        #
        # extra_body is an escape hatch for server-specific knobs the
        # OpenAI-compatible SDK wraps transparently — subclasses (e.g.
        # OpenAICompatClient) inject provider extensions like
        # chat_template_kwargs.enable_thinking here.
        self._sdk = sdk
        self.model = model
        self._prompt_cache = prompt_cache
        self._extra_body = extra_body

    async def stream(self, *, system_prompt: str,
                     messages: List[LLMMessage], tools: List[ToolSpec],
                     max_output_tokens: int,
                     cache_stable_tool_prefix: Optional[int] = None,
                     _extra_body_override: Optional[Dict[str, Any]] = None,
                     ) -> "_OpenAIStream":
        # cache_stable_tool_prefix is a no-op here, like prompt_cache: OpenAI's
        # prefix caching is automatic and server-side. Accepted only to keep a
        # uniform LLMClient.stream signature across providers.
        converted = _messages_to_openai(system_prompt, messages)
        tool_param = to_openai_spec(tools) if tools else None
        # Per-call escape hatch (shallow-merged over the client default) so a
        # single request can flip a server-specific knob — e.g. the title call
        # disabling chat_template_kwargs.enable_thinking — without rebuilding
        # the client or affecting the main chat.
        body = self._extra_body
        if _extra_body_override:
            body = {**(self._extra_body or {}), **_extra_body_override}
        return _OpenAIStream(
            sdk=self._sdk, model=self.model,
            messages=converted, tools=tool_param,
            max_output_tokens=max_output_tokens,
            extra_body=body,
            token_limit_param=self.token_limit_param,
        )


def _messages_to_openai(system_prompt: str,
                        messages: List[LLMMessage]) -> List[Dict[str, Any]]:
    """Project generic LLMMessage list into OpenAI chat.completions shape.

    System prompt is always first. Tool results map to ``role=tool`` with a
    ``tool_call_id`` key (OpenAI's requirement for response pairing).
    """
    out: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for m in messages:
        if m.role == "user":
            out.append({"role": "user", "content": m.content or ""})
        elif m.role == "assistant":
            entry: Dict[str, Any] = {"role": "assistant",
                                     "content": m.content or ""}
            if m.tool_calls:
                entry["tool_calls"] = [{
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc.get("arguments") or {}),
                    },
                } for tc in m.tool_calls]
            out.append(entry)
        elif m.role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": m.tool_call_id or "",
                "content": m.content or "",
            })
    return out


class _OpenAIStream:
    def __init__(self, *, sdk: AsyncOpenAI, model: str,
                 messages: List[Dict[str, Any]],
                 tools: Optional[List[Dict[str, Any]]],
                 max_output_tokens: int,
                 extra_body: Optional[Dict[str, Any]] = None,
                 token_limit_param: str = "max_completion_tokens") -> None:
        self._sdk = sdk
        self._model = model
        self._messages = messages
        self._tools = tools
        self._max_out = max_output_tokens
        self._extra_body = extra_body
        self._token_limit_param = token_limit_param
        self._stream: Any = None
        self._result = StreamResult(stop_reason="end_turn")

    async def __aenter__(self) -> "_OpenAIStream":
        params: Dict[str, Any] = {
            "model": self._model,
            "messages": self._messages,
            self._token_limit_param: self._max_out,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self._tools:
            params["tools"] = self._tools
        if self._extra_body:
            # openai-python forwards extra_body into the request JSON.
            # Covers vLLM/Ollama-specific knobs without polluting the core
            # signature.
            params["extra_body"] = self._extra_body
        self._stream = await self._sdk.chat.completions.create(**params)
        return self

    async def __aexit__(self, *exc) -> None:
        # AsyncStream exposes aclose() on recent openai-python versions.
        close = getattr(self._stream, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:
                pass
        self._stream = None

    def __aiter__(self) -> AsyncIterator[LLMChunk]:
        return self._aiter()

    async def _aiter(self) -> AsyncIterator[LLMChunk]:
        # Pending tool-call accumulators keyed by index (OpenAI's stream
        # sends each tool_call's fragments referenced by index). Once a
        # ``finish_reason=="tool_calls"`` fires we snapshot them into
        # ``result.tool_calls`` in index order.
        tool_calls_by_index: Dict[int, _PendingToolCall] = {}
        text_parts: List[str] = []

        async for event in self._stream:
            if not event.choices:
                # Final usage-only chunk arrives with an empty choices[] when
                # ``stream_options={"include_usage": True}`` is set.
                usage = getattr(event, "usage", None)
                if usage:
                    self._result.usage.input_tokens = (
                        getattr(usage, "prompt_tokens", 0) or 0)
                    self._result.usage.output_tokens = (
                        getattr(usage, "completion_tokens", 0) or 0)
                    self._result.usage.cached_tokens = _cached_tokens(usage)
                continue

            choice = event.choices[0]
            delta = getattr(choice, "delta", None)

            if delta is not None:
                text = getattr(delta, "content", None)
                if text:
                    text_parts.append(text)
                    yield LLMChunk(kind="text", text=text)

                for tc in (getattr(delta, "tool_calls", None) or []):
                    idx = getattr(tc, "index", None)
                    if idx is None:
                        continue
                    acc = tool_calls_by_index.setdefault(idx, _PendingToolCall())
                    if getattr(tc, "id", None):
                        acc.id = tc.id
                    fn = getattr(tc, "function", None)
                    if fn is not None:
                        name = getattr(fn, "name", None)
                        if name:
                            acc.name = name
                        args_delta = getattr(fn, "arguments", None)
                        if args_delta:
                            acc.args_parts.append(args_delta)
                            yield LLMChunk(
                                kind="tool_call_delta",
                                tool_call_id=acc.id or None,
                                tool_args_delta=args_delta,
                            )
                    # Emit the "start" marker once per tool_call as soon as
                    # we have both id + name — the orchestrator uses this
                    # to show a chip in the UI before args finish streaming.
                    if not acc.announced and acc.id and acc.name:
                        acc.announced = True
                        yield LLMChunk(
                            kind="tool_call_start",
                            tool_call_id=acc.id,
                            tool_name=acc.name,
                        )

            finish = getattr(choice, "finish_reason", None)
            if finish == "tool_calls":
                # Snapshot accumulated tool_calls in index order.
                for idx in sorted(tool_calls_by_index):
                    acc = tool_calls_by_index[idx]
                    args = _safe_parse_tool_args(
                        "".join(acc.args_parts), tool_name=acc.name,
                    )
                    self._result.tool_calls.append({
                        "id": acc.id,
                        "name": acc.name,
                        "arguments": args,
                    })
                    yield LLMChunk(
                        kind="tool_call_end",
                        tool_call_id=acc.id,
                        tool_name=acc.name,
                    )
                self._result.stop_reason = "tool_use"
            elif finish == "stop":
                self._result.stop_reason = "end_turn"
            elif finish == "length":
                self._result.stop_reason = "max_tokens"

        self._result.final_text = "".join(text_parts)

    @property
    def result(self) -> StreamResult:
        return self._result


def _cached_tokens(usage: Any) -> int:
    """OpenAI's ``prompt_tokens_details.cached_tokens`` when available."""
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None:
        return 0
    return int(getattr(details, "cached_tokens", 0) or 0)
