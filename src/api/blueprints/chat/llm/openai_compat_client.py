# SPDX-License-Identifier: AGPL-3.0-or-later
"""OpenAI-compatible provider client (vLLM, LiteLLM, Ollama, etc.).

Subclasses :class:`OpenAIClient` with a custom ``base_url``. Not all
OpenAI-compatible servers implement the ``tools`` parameter — some accept
it and ignore it, others return 400. When tools are unsupported, we fall
back to prompt-based tool dispatch: tool descriptors are injected into the
system prompt with instructions to emit a single
``<tool_call>{"name": ..., "arguments": ...}</tool_call>`` block, and the
reply text is scanned for that block in :meth:`_parse_tool_calls_from_text`.

Feature detection is deferred to a one-time sync probe the first time
``stream()`` is called with non-empty tools, so the common "server
supports tools" path has no startup penalty and the cold-start probe
runs once per process.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI

from .base import LLMChunk, LLMMessage, StreamResult, ToolSpec
from .openai_client import OpenAIClient

logger = logging.getLogger(__name__)

_TOOL_CALL_BLOCK = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL,
)

_TOOL_CALL_OPEN = "<tool_call>"


def _emittable_len(buf: str, marker: str) -> int:
    """How much of ``buf`` is safe to stream now.

    Everything except a trailing run that could still grow into ``marker`` on
    the next chunk (e.g. a buffer ending in ``"<tool"`` must be held back until
    we know whether ``"_call>"`` follows). Prevents leaking a partial marker.
    """
    for k in range(min(len(buf), len(marker) - 1), 0, -1):
        if buf.endswith(marker[:k]):
            return len(buf) - k
    return len(buf)


class OpenAICompatClient(OpenAIClient):
    provider = "openai_compat"

    # Self-hosted OpenAI-compatible servers (vLLM/Ollama/LiteLLM) speak the
    # legacy `max_tokens`; they generally don't understand OpenAI's newer
    # `max_completion_tokens`. Override the parent's default back.
    token_limit_param = "max_tokens"

    def __init__(self, *, sdk: AsyncOpenAI, model: str, base_url: str,
                 prompt_cache: bool = False,
                 thinking_enabled: bool = True) -> None:
        # Qwen3 / DeepSeek-R1 on vLLM expose a per-request switch through
        # chat_template_kwargs.enable_thinking. When the operator disables
        # thinking we forward the kwarg on every request; when it's enabled
        # we send nothing (the chat template defaults to thinking-on for
        # these models). Servers that don't understand the kwarg ignore it.
        extra_body: Optional[Dict[str, Any]] = None
        if not thinking_enabled:
            extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
        super().__init__(sdk=sdk, model=model, prompt_cache=prompt_cache,
                         extra_body=extra_body)
        self._base_url = base_url
        # None = untested; True/False after the first probe.
        self._supports_tools: Optional[bool] = None

    async def stream(self, *, system_prompt: str,
                     messages: List[LLMMessage], tools: List[ToolSpec],
                     max_output_tokens: int,
                     cache_stable_tool_prefix: Optional[int] = None,
                     disable_thinking: bool = False):
        # cache_stable_tool_prefix is a no-op on this path (no client-side
        # prompt caching); accepted only for a uniform LLMClient.stream
        # signature. Not forwarded — OpenAIClient.stream ignores it too.
        # Per-request thinking switch (independent of the operator-level
        # thinking_enabled set at construction). The auto-title call uses it so
        # qwen3-class reasoning models don't spend the whole wall-clock budget
        # in a <think> block before emitting a 3-word title — the main chat
        # keeps reasoning. Same chat_template_kwargs mechanism as the global
        # toggle; servers that don't understand it ignore it.
        override = ({"chat_template_kwargs": {"enable_thinking": False}}
                    if disable_thinking else None)
        if not tools:
            return await super().stream(
                system_prompt=system_prompt, messages=messages, tools=tools,
                max_output_tokens=max_output_tokens,
                _extra_body_override=override,
            )

        if self._supports_tools is None:
            probed = await self._probe_tools()
            # Cache only a definitive verdict. A transient probe failure
            # (network/timeout/429/5xx) returns None and stays uncached, so the
            # next turn re-probes instead of permanently degrading a
            # tools-capable server to prompt-based dispatch for the whole
            # process. Undetermined → optimistically use native tools now.
            if probed is not None:
                self._supports_tools = probed
            use_native = probed is not False
        else:
            use_native = self._supports_tools

        if use_native:
            return await super().stream(
                system_prompt=system_prompt, messages=messages, tools=tools,
                max_output_tokens=max_output_tokens,
                _extra_body_override=override,
            )

        # Tool-free mode: inject descriptors into the system prompt and parse
        # the reply text for a single <tool_call>{...}</tool_call> block.
        augmented = _augment_system_prompt(system_prompt, tools)
        real_stream = await super().stream(
            system_prompt=augmented, messages=messages, tools=[],
            max_output_tokens=max_output_tokens,
            _extra_body_override=override,
        )
        return _ToolInPromptStream(inner=real_stream, tools=tools)

    async def _probe_tools(self) -> Optional[bool]:
        """Probe whether the server accepts the ``tools`` param.

        Returns True when the probe succeeds, False only when the server
        *definitively* rejects the parameter (HTTP 400/422 — unknown/unsupported
        param; some servers 200 and silently drop tools, which we can't detect
        and so treat as supported), and None when the probe couldn't reach a
        verdict (connection error, timeout, 401/403/429/5xx). A None result is
        deliberately not cached by the caller, so a transient blip during a
        cold start doesn't permanently downgrade a tools-capable server to
        prompt-based dispatch.
        """
        from openai import APIStatusError
        try:
            await self._sdk.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                stream=False,
                tools=[{
                    "type": "function",
                    "function": {
                        "name": "_probe",
                        "description": "probe",
                        "parameters": {"type": "object",
                                       "properties": {}},
                    },
                }],
            )
            # Presence of a response is enough — shape not inspected.
            return True
        except APIStatusError as e:
            if e.status_code in (400, 422):
                logger.warning(
                    "OpenAI-compat server at %s rejected the tools param "
                    "(HTTP %s); using prompt-based tool dispatch.",
                    self._base_url, e.status_code,
                )
                return False
            logger.warning(
                "OpenAI-compat tools probe to %s got HTTP %s; undetermined, "
                "re-probing next turn.", self._base_url, e.status_code,
            )
            return None
        except Exception as e:
            logger.warning(
                "OpenAI-compat tools probe to %s failed (%s); undetermined, "
                "re-probing next turn.", self._base_url, e.__class__.__name__,
            )
            return None


def _augment_system_prompt(system_prompt: str, tools: List[ToolSpec]) -> str:
    """Inline tool specs into the system prompt with protocol instructions."""
    tool_lines = [
        f"- {t.name}: {t.description} — input schema: "
        f"{json.dumps(t.input_schema, separators=(',', ':'))}"
        for t in tools
    ]
    block = (
        "\n\n## Tool use (prompt protocol)\n"
        "You may request ONE tool per reply by emitting exactly this block:\n"
        '<tool_call>{"name": "<tool_name>", "arguments": {...}}</tool_call>\n'
        "Available tools:\n" + "\n".join(tool_lines) + "\n"
        "After the block, stop. Do not emit additional prose, explanations, "
        "or multiple blocks. When no tool is needed, reply with the final "
        "answer directly."
    )
    return system_prompt + block


class _ToolInPromptStream:
    """Proxy an inner ``_OpenAIStream``, scan its text for a ``<tool_call>``
    block, and rewrite the :class:`StreamResult` accordingly.

    Text chunks outside the tool block are re-yielded unchanged so the UI
    sees real streaming. A trailing buffer holds the last few chunks in
    case the block is split across them.
    """

    def __init__(self, *, inner: Any, tools: List[ToolSpec]) -> None:
        self._inner = inner
        self._tools = {t.name for t in tools}
        self._result = StreamResult(stop_reason="end_turn")

    async def __aenter__(self):
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc):
        await self._inner.__aexit__(*exc)

    def __aiter__(self) -> AsyncIterator[LLMChunk]:
        return self._aiter()

    async def _aiter(self) -> AsyncIterator[LLMChunk]:
        # Stream text through, but never leak the <tool_call> protocol block to
        # the user: hold back a trailing partial that might be the marker, and
        # once the marker appears suppress everything after it (the model is
        # told to stop after the block). The inner stream still accumulates the
        # full text, which we parse for the actual call below.
        buf = ""
        suppressing = False
        async for chunk in self._inner:
            if chunk.kind != "text":
                yield chunk
                continue
            if suppressing:
                continue
            buf += chunk.text
            cut = buf.find(_TOOL_CALL_OPEN)
            if cut != -1:
                if cut > 0:
                    yield LLMChunk(kind="text", text=buf[:cut])
                buf = ""
                suppressing = True
                continue
            emit = _emittable_len(buf, _TOOL_CALL_OPEN)
            if emit:
                yield LLMChunk(kind="text", text=buf[:emit])
                buf = buf[emit:]
        if buf and not suppressing:
            yield LLMChunk(kind="text", text=buf)

        # Inner stream has already accumulated the full reply. Keep the
        # persisted/emitted final_text consistent with what streamed — i.e.
        # only the prose before the block, never the raw markup.
        inner_res = self._inner.result
        self._result.usage = inner_res.usage
        full = inner_res.final_text
        open_at = full.find(_TOOL_CALL_OPEN)
        self._result.final_text = (
            full[:open_at].rstrip() if open_at != -1 else full
        )

        match = _TOOL_CALL_BLOCK.search(full)
        if match:
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and payload.get("name") in self._tools:
                self._result.stop_reason = "tool_use"
                self._result.tool_calls.append({
                    # compat servers don't issue tool_use ids — synthesise
                    # a unique one so downstream pairing (chat_tool_calls
                    # persistence + Anthropic-style rebuilds) stays uniform
                    # and identical user questions don't collide.
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "name": payload["name"],
                    "arguments": payload.get("arguments") or {},
                })
                return
        self._result.stop_reason = inner_res.stop_reason or "end_turn"

    @property
    def result(self) -> StreamResult:
        return self._result
