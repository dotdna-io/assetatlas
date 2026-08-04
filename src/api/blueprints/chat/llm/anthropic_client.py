# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Anthropic LLM client.

Thin wrapper over `anthropic.AsyncAnthropic` that streams message events and
translates them into generic `LLMChunk` / `StreamResult` shapes. Supports
ephemeral prompt caching on the system prompt and the last tool spec.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional

from anthropic import AsyncAnthropic

from .base import (_EMPTY_ASSISTANT_PLACEHOLDER, LLMChunk, LLMMessage,
                   StreamResult, ToolSpec, to_anthropic_spec)


class AnthropicClient:
    provider = "anthropic"

    def __init__(self, *, sdk: AsyncAnthropic, model: str, prompt_cache: bool = True):
        self._sdk = sdk
        self.model = model
        self._prompt_cache = prompt_cache

    async def stream(self, *, system_prompt: str, messages: List[LLMMessage],
                     tools: List[ToolSpec], max_output_tokens: int,
                     cache_stable_tool_prefix: Optional[int] = None,
                     ) -> "_AnthropicStream":
        system_param: List[Dict[str, Any]] = [{"type": "text", "text": system_prompt}]
        if self._prompt_cache:
            system_param[0]["cache_control"] = {"type": "ephemeral"}

        tool_param = to_anthropic_spec(tools)
        if self._prompt_cache and tool_param and cache_stable_tool_prefix != 0:
            # cache_control creates a prefix cache up to and including the
            # marked tool. cache_stable_tool_prefix is the count of LEADING
            # tools stable across turns/iterations: with routing ON the list is
            # [stable core tools][variable retrieved tail], so the marker goes
            # on the last stable tool (index N-1), keeping the cached prefix
            # identical even as the tail changes. None (routing OFF / legacy)
            # marks the last tool — today's behavior. A prefix of 0 (no stable
            # leading tool — degenerate registry) is excluded above: idx would be
            # -1 and mark the variable last tool, busting the cache every turn;
            # leave the tools unmarked (the system block is still cached).
            idx = (-1 if cache_stable_tool_prefix is None
                   else cache_stable_tool_prefix - 1)
            tool_param[idx]["cache_control"] = {"type": "ephemeral"}

        converted = _messages_to_anthropic(messages)
        return _AnthropicStream(
            sdk=self._sdk, model=self.model,
            system=system_param, messages=converted, tools=tool_param,
            max_output_tokens=max_output_tokens,
        )


def _messages_to_anthropic(messages: List[LLMMessage]) -> List[Dict[str, Any]]:
    # A single assistant turn can emit multiple tool_use blocks (parallel tool
    # use). Each result arrives as its own tool-role LLMMessage, but the
    # Messages API enforces user/assistant alternation and expects ALL
    # tool_result blocks for a turn in the ONE user message that follows it.
    # Emitting one user message per result produces consecutive user turns the
    # API rejects, so consecutive tool-role messages are coalesced (mirrors
    # messages_to_bedrock in base.py).
    out: List[Dict[str, Any]] = []
    pending_tool_results: List[Dict[str, Any]] = []

    def _flush_tool_results() -> None:
        if pending_tool_results:
            out.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for m in messages:
        if m.role == "tool":
            pending_tool_results.append({
                "type": "tool_result", "tool_use_id": m.tool_call_id or "",
                "content": m.content or "",
            })
            continue
        _flush_tool_results()
        if m.role == "user":
            out.append({"role": "user", "content": m.content or ""})
        elif m.role == "assistant":
            blocks: List[Dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for tc in (m.tool_calls or []):
                blocks.append({
                    "type": "tool_use", "id": tc["id"],
                    "name": tc["name"], "input": tc.get("arguments", {}),
                })
            if not blocks:
                blocks.append({
                    "type": "text", "text": _EMPTY_ASSISTANT_PLACEHOLDER,
                })
            out.append({"role": "assistant", "content": blocks})
    _flush_tool_results()
    return out


class _AnthropicStream:
    def __init__(self, *, sdk, model, system, messages, tools, max_output_tokens):
        self._sdk = sdk
        self._model = model
        self._system = system
        self._messages = messages
        self._tools = tools
        self._max_out = max_output_tokens
        self._ctx = None
        self._mgr = None
        self._result = StreamResult(stop_reason="end_turn")

    async def __aenter__(self) -> "_AnthropicStream":
        params: Dict[str, Any] = {
            "model": self._model,
            "system": self._system,
            "messages": self._messages,
            "max_tokens": self._max_out,
        }
        if self._tools:
            params["tools"] = self._tools
        self._mgr = self._sdk.messages.stream(**params)
        self._ctx = await self._mgr.__aenter__()
        return self

    async def __aexit__(self, *exc):
        if self._mgr is not None:
            await self._mgr.__aexit__(*exc)
            self._mgr = None
            self._ctx = None

    def __aiter__(self) -> AsyncIterator[LLMChunk]:
        return self._aiter()

    async def _aiter(self) -> AsyncIterator[LLMChunk]:
        current_tool_id: Optional[str] = None
        current_tool_name: Optional[str] = None
        tool_args_parts: List[str] = []
        text_parts: List[str] = []

        async for event in self._ctx:
            et = getattr(event, "type", None)
            if et == "message_start":
                usage = event.message.usage
                self._result.usage.input_tokens = getattr(usage, "input_tokens", 0) or 0
                self._result.usage.cached_tokens = (
                    getattr(usage, "cache_read_input_tokens", 0) or 0)
            elif et == "content_block_start":
                block = event.content_block
                if block.type == "tool_use":
                    current_tool_id = block.id
                    current_tool_name = block.name
                    tool_args_parts = []
                    yield LLMChunk(kind="tool_call_start",
                                   tool_call_id=current_tool_id,
                                   tool_name=current_tool_name)
            elif et == "content_block_delta":
                d = event.delta
                if getattr(d, "type", None) == "text_delta":
                    text_parts.append(d.text)
                    yield LLMChunk(kind="text", text=d.text)
                elif getattr(d, "type", None) == "input_json_delta":
                    tool_args_parts.append(d.partial_json)
                    yield LLMChunk(kind="tool_call_delta",
                                   tool_call_id=current_tool_id,
                                   tool_args_delta=d.partial_json)
            elif et == "content_block_stop":
                if current_tool_id is not None:
                    from .openai_client import _safe_parse_tool_args
                    args = _safe_parse_tool_args(
                        "".join(tool_args_parts),
                        tool_name=current_tool_name or "",
                    )
                    self._result.tool_calls.append({
                        "id": current_tool_id,
                        "name": current_tool_name,
                        "arguments": args,
                    })
                    yield LLMChunk(kind="tool_call_end",
                                   tool_call_id=current_tool_id,
                                   tool_name=current_tool_name)
                    current_tool_id = None
                    current_tool_name = None
                    tool_args_parts = []
            elif et == "message_delta":
                sr = event.delta.stop_reason
                if sr == "refusal":
                    # A refusal is a normal (HTTP 200) safety outcome, not a
                    # config error. Surface it as an error with a distinguishing
                    # detail so the user is told the model declined — rather
                    # than passing "refusal" through to the orchestrator's
                    # generic branch, which blames the provider configuration.
                    self._result.stop_reason = "error"
                    self._result.error = "Claude stopped with stop_reason=refusal"
                elif sr:
                    self._result.stop_reason = sr
                out = getattr(event.usage, "output_tokens", 0) or 0
                if out:
                    self._result.usage.output_tokens = out

        self._result.final_text = "".join(text_parts)

    @property
    def result(self) -> StreamResult:
        return self._result
