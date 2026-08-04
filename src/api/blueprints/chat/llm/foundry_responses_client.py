# SPDX-License-Identifier: AGPL-3.0-or-later
"""LLM client for the Azure OpenAI Responses API surfaced by Microsoft Foundry.

Wraps ``openai.AsyncOpenAI.responses.create(stream=True)``. The Responses API
uses a different input/output shape than chat.completions:

* ``input`` is a heterogeneous list mixing role-based message dicts and
  flat items (``function_call``, ``function_call_output``).
* Streamed events have ``type`` strings like ``response.output_text.delta``
  rather than the chat-completions ``choices[].delta.content`` shape.
* Tool calls accumulate via ``response.function_call_arguments.delta`` events
  keyed by ``item_id`` and are closed by ``response.output_item.done``.

The client translates these into the same generic ``LLMChunk`` / ``StreamResult``
shape the orchestration service consumes, so it's interchangeable with the
existing Anthropic, OpenAI chat.completions, and Bedrock clients.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI

from .base import (
    LLMChunk, LLMMessage, StreamResult, ToolSpec, to_responses_spec,
)
from .openai_client import _safe_parse_tool_args

logger = logging.getLogger(__name__)


@dataclass
class _PendingResponsesToolCall:
    """Accumulator for one streaming function_call item (keyed by ``item_id``)."""

    item_id: str = ""
    call_id: str = ""
    name: str = ""
    args_parts: List[str] = field(default_factory=list)
    announced: bool = False


class FoundryResponsesClient:
    provider = "foundry_responses"

    def __init__(self, *, sdk: AsyncOpenAI, model: str) -> None:
        self._sdk = sdk
        self.model = model

    async def stream(self, *, system_prompt: str,
                     messages: List[LLMMessage], tools: List[ToolSpec],
                     max_output_tokens: int,
                     cache_stable_tool_prefix: Optional[int] = None,
                     ) -> "_FoundryResponsesStream":
        # cache_stable_tool_prefix is a no-op on the Responses path (no
        # client-side prompt caching); accepted only to keep a uniform
        # LLMClient.stream signature across providers.
        converted = _messages_to_responses(system_prompt, messages)
        tool_param = to_responses_spec(tools) if tools else None
        return _FoundryResponsesStream(
            sdk=self._sdk, model=self.model,
            input_items=converted, tools=tool_param,
            max_output_tokens=max_output_tokens,
        )


def _messages_to_responses(
    system_prompt: str, messages: List[LLMMessage],
) -> List[Dict[str, Any]]:
    """Project the generic LLMMessage list into the Responses API ``input``
    shape.

    Output is a heterogeneous list: role-based message dicts for user/assistant
    text turns, flat ``function_call`` items for assistant tool calls, and
    flat ``function_call_output`` items for tool results. The Responses API
    accepts the system prompt as a leading ``role="system"`` item rather
    than a top-level ``instructions`` field — easier to keep the conversion
    self-contained.
    """
    out: List[Dict[str, Any]] = [
        {"role": "system",
         "content": [{"type": "input_text", "text": system_prompt}]}
    ]
    for m in messages:
        if m.role == "user":
            out.append({
                "role": "user",
                "content": [{"type": "input_text", "text": m.content or ""}],
            })
        elif m.role == "assistant":
            if m.content:
                # Bare string, not an output_text content part: the Responses
                # "easy message" input only accepts input_* parts, and
                # output_text is valid solely inside a full message item
                # (id/status/type). Wrapping it produced a shape the API
                # rejects with a 400, breaking every turn past the first.
                out.append({"role": "assistant", "content": m.content})
            for tc in (m.tool_calls or []):
                out.append({
                    "type": "function_call",
                    "call_id": tc["id"],
                    "name": tc["name"],
                    "arguments": json.dumps(tc.get("arguments") or {}),
                })
        elif m.role == "tool":
            out.append({
                "type": "function_call_output",
                "call_id": m.tool_call_id or "",
                "output": m.content or "",
            })
    return out


class _FoundryResponsesStream:
    def __init__(self, *, sdk: AsyncOpenAI, model: str,
                 input_items: List[Dict[str, Any]],
                 tools: Optional[List[Dict[str, Any]]],
                 max_output_tokens: int) -> None:
        self._sdk = sdk
        self._model = model
        self._input = input_items
        self._tools = tools
        self._max_out = max_output_tokens
        self._stream: Any = None
        self._result = StreamResult(stop_reason="end_turn")

    async def __aenter__(self) -> "_FoundryResponsesStream":
        params: Dict[str, Any] = {
            "model": self._model,
            "input": self._input,
            "max_output_tokens": self._max_out,
            "stream": True,
        }
        if self._tools:
            params["tools"] = self._tools
        self._stream = await self._sdk.responses.create(**params)
        return self

    async def __aexit__(self, *exc) -> None:
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
        pending: Dict[str, _PendingResponsesToolCall] = {}
        text_parts: List[str] = []

        async for event in self._stream:
            et = getattr(event, "type", None)

            if et == "response.output_text.delta":
                delta = getattr(event, "delta", "") or ""
                if delta:
                    text_parts.append(delta)
                    yield LLMChunk(kind="text", text=delta)

            elif et == "response.output_item.added":
                item = getattr(event, "item", None)
                if item is not None and getattr(item, "type", "") == "function_call":
                    item_id = getattr(item, "id", "") or ""
                    acc = pending.setdefault(item_id, _PendingResponsesToolCall(
                        item_id=item_id,
                        call_id=getattr(item, "call_id", "") or "",
                        name=getattr(item, "name", "") or "",
                    ))
                    if not acc.announced and acc.call_id and acc.name:
                        acc.announced = True
                        yield LLMChunk(
                            kind="tool_call_start",
                            tool_call_id=acc.call_id,
                            tool_name=acc.name,
                        )

            elif et == "response.function_call_arguments.delta":
                item_id = getattr(event, "item_id", "") or ""
                acc = pending.get(item_id)
                if acc is None:
                    # Defensive: shouldn't happen, but don't crash the stream.
                    continue
                delta = getattr(event, "delta", "") or ""
                if delta:
                    acc.args_parts.append(delta)
                    yield LLMChunk(
                        kind="tool_call_delta",
                        tool_call_id=acc.call_id or None,
                        tool_args_delta=delta,
                    )

            elif et == "response.output_item.done":
                item = getattr(event, "item", None)
                if item is not None and getattr(item, "type", "") == "function_call":
                    item_id = getattr(item, "id", "") or ""
                    acc = pending.get(item_id)
                    if acc is None:
                        continue
                    raw = "".join(acc.args_parts) or (
                        getattr(item, "arguments", "") or ""
                    )
                    args = _safe_parse_tool_args(raw, tool_name=acc.name)
                    self._result.tool_calls.append({
                        "id": acc.call_id,
                        "name": acc.name,
                        "arguments": args,
                    })
                    yield LLMChunk(
                        kind="tool_call_end",
                        tool_call_id=acc.call_id,
                        tool_name=acc.name,
                    )

            elif et == "response.completed":
                response = getattr(event, "response", None)
                usage = getattr(response, "usage", None) if response else None
                if usage is not None:
                    self._result.usage.input_tokens = int(
                        getattr(usage, "input_tokens", 0) or 0
                    )
                    self._result.usage.output_tokens = int(
                        getattr(usage, "output_tokens", 0) or 0
                    )
                    details = getattr(usage, "input_tokens_details", None)
                    self._result.usage.cached_tokens = int(
                        getattr(details, "cached_tokens", 0) or 0
                    )

            elif et == "response.incomplete":
                response = getattr(event, "response", None)
                usage = getattr(response, "usage", None) if response else None
                if usage is not None:
                    self._result.usage.input_tokens = int(
                        getattr(usage, "input_tokens", 0) or 0
                    )
                    self._result.usage.output_tokens = int(
                        getattr(usage, "output_tokens", 0) or 0
                    )
                    details = getattr(usage, "input_tokens_details", None)
                    self._result.usage.cached_tokens = int(
                        getattr(details, "cached_tokens", 0) or 0
                    )
                # The Responses API reports the truncation cause on the
                # response object itself; the SDK exposes it as
                # ``response.incomplete_details.reason``. We map
                # ``max_output_tokens`` to the orchestrator's "max_tokens"
                # variant and treat anything else (e.g. ``content_filter``)
                # as an error so the orchestrator can surface it.
                details = getattr(response, "incomplete_details", None)
                reason = getattr(details, "reason", "") if details else ""
                if reason == "max_output_tokens":
                    self._result.stop_reason = "max_tokens"
                else:
                    self._result.stop_reason = "error"
                    self._result.error = (
                        f"Foundry Responses stream incomplete: {reason or 'unknown'}"
                    )

            elif et == "response.failed":
                response = getattr(event, "response", None)
                err = getattr(response, "error", None) if response else None
                code = getattr(err, "code", "") if err else ""
                message = getattr(err, "message", "") if err else ""
                self._result.stop_reason = "error"
                self._result.error = (
                    f"Foundry Responses stream failed: "
                    f"{code or 'unknown'}: {message}"
                )

        # If a terminal event already set a definitive stop_reason
        # (max_tokens / error from incomplete or failed events), respect it.
        # Otherwise infer from accumulated tool_calls: tool_use if present,
        # else end_turn (the initial value).
        if self._result.stop_reason == "end_turn" and self._result.tool_calls:
            self._result.stop_reason = "tool_use"
        self._result.final_text = "".join(text_parts)

    @property
    def result(self) -> StreamResult:
        return self._result
