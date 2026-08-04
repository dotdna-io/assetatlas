# SPDX-License-Identifier: AGPL-3.0-or-later
"""
LLM Provider Abstraction — Base Types

Dataclasses and Protocols shared by all LLM provider implementations.
Adapters convert generic ToolSpec into provider-specific function/tool
definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Literal, Optional, Protocol


@dataclass
class ToolSpec:
    """Generic tool descriptor exposed to the LLM."""
    name: str
    description: str
    input_schema: Dict[str, Any]
    admin_required: bool = False
    # False = invocable but NOT advertised to the LLM. Used for deprecated
    # aliases: a model that still calls the old name keeps working, but the
    # schema no longer costs input tokens on every turn.
    advertise: bool = True


@dataclass
class LLMMessage:
    """One message in the running LLM conversation (in-flight, not persisted).

    Providers receive these and translate them to their own message shapes.
    Named LLMMessage rather than ChatMessage to avoid collision with the
    persisted Pydantic record in src/api/blueprints/chat/models.py.
    """
    role: Literal["user", "assistant", "tool"]
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None


@dataclass
class LLMChunk:
    """One streaming event emitted by an LLMStream."""
    kind: Literal["text", "tool_call_start", "tool_call_delta", "tool_call_end"]
    text: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_args_delta: Optional[str] = None                 # partial JSON


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


@dataclass
class StreamResult:
    """Populated after a stream completes."""
    stop_reason: Literal["end_turn", "tool_use", "error", "max_tokens"]
    final_text: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)   # [{id, name, arguments}]
    usage: Usage = field(default_factory=Usage)
    error: Optional[str] = None


class LLMClient(Protocol):
    """Structural type for LLM provider clients."""
    provider: str
    model: str

    async def stream(
        self,
        *,
        system_prompt: str,
        messages: List[LLMMessage],
        tools: List[ToolSpec],
        max_output_tokens: int,
        cache_stable_tool_prefix: Optional[int] = None,
    ) -> "LLMStream":
        """Open a streaming turn.

        Returns an LLMStream context manager — use as
        ``async with await client.stream(...) as stream:`` (the ``await``
        resolves the coroutine; ``async with`` enters the resulting context).

        ``cache_stable_tool_prefix`` is the number of LEADING tools that are
        stable across turns/iterations — the cacheable prompt-cache boundary.
        Providers with prompt caching (Bedrock, Anthropic) place the tools
        cache breakpoint after this many tools so the cached system+core prefix
        is identical across turns even when tool routing appends a variable
        retrieved tail. ``None`` = breakpoint after the whole list (current
        behavior). Providers without client-side caching ignore it.
        """
        ...


class LLMStream(Protocol):
    """Async context manager + async iterator of LLMChunks."""
    async def __aenter__(self) -> "LLMStream": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...
    def __aiter__(self) -> AsyncIterator[LLMChunk]: ...
    @property
    def result(self) -> StreamResult: ...                 # populated after iteration


# ---------- tool-spec adapters ----------

def to_anthropic_spec(tools: List[ToolSpec]) -> List[Dict[str, Any]]:
    """Convert ToolSpecs into Anthropic Messages API `tools` parameter shape."""
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]


def to_openai_spec(tools: List[ToolSpec]) -> List[Dict[str, Any]]:
    """Convert ToolSpecs into OpenAI Chat Completions `tools` parameter shape."""
    return [
        {"type": "function",
         "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}}
        for t in tools
    ]


def to_responses_spec(tools: List[ToolSpec]) -> List[Dict[str, Any]]:
    """Convert ToolSpecs into OpenAI Responses API `tools` parameter shape.

    The Responses API uses a flatter shape than chat.completions: no nested
    ``function:`` key, ``name``/``description``/``parameters`` sit directly
    on the tool object alongside ``type=='function'``.
    """
    return [
        {
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": t.input_schema,
        }
        for t in tools
    ]


def to_bedrock_spec(tools: List[ToolSpec]) -> Dict[str, Any]:
    """Convert ToolSpecs into Bedrock Converse ``toolConfig`` shape.

    Bedrock's Converse API expects::

        toolConfig = {"tools": [{"toolSpec": {"name", "description",
                                              "inputSchema": {"json": <schema>}}}]}

    Returns the full ``toolConfig`` dict (caller passes it directly into
    ``converse_stream(toolConfig=...)``)."""
    return {
        "tools": [
            {"toolSpec": {
                "name": t.name,
                "description": t.description,
                "inputSchema": {"json": t.input_schema},
            }}
            for t in tools
        ],
    }


# Stand-in text for an assistant turn that produced no text and no tool calls
# (e.g. a reasoning model that ended its turn after spending the whole budget
# on a hidden think block). Such a turn is persisted with content="" and
# replayed on the next turn; Bedrock Converse and the Anthropic Messages API
# both reject a message with an empty content list, so the serializers emit
# this placeholder block instead. Shared so both serializers stay in sync.
_EMPTY_ASSISTANT_PLACEHOLDER = "(no response)"


def messages_to_bedrock(messages: List["LLMMessage"]) -> List[Dict[str, Any]]:
    """Project generic LLMMessage list into Bedrock Converse messages shape.

    Bedrock Converse uses the same user/assistant alternation as Anthropic,
    with content blocks (``text``, ``toolUse``, ``toolResult``). Tool-result
    messages are sent under ``role="user"`` (matches Anthropic's convention)
    so the loop stays a clean user/assistant alternation.

    A single assistant turn can emit multiple ``toolUse`` blocks (parallel
    tool use). Each result arrives as its own tool-role LLMMessage, but
    Converse requires ALL matching ``toolResult`` blocks in the ONE user
    message immediately following the assistant turn — emitting one user
    message per result splits them across consecutive user turns, breaking
    alternation, and Converse rejects with ``Expected toolResult blocks at
    messages.N.content for the following Ids``. So consecutive tool-role
    messages are coalesced into a single user message."""
    out: List[Dict[str, Any]] = []
    pending_tool_results: List[Dict[str, Any]] = []

    def _flush_tool_results() -> None:
        if pending_tool_results:
            out.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for m in messages:
        if m.role == "tool":
            pending_tool_results.append({
                "toolResult": {
                    "toolUseId": m.tool_call_id or "",
                    "content": [{"text": m.content or ""}],
                },
            })
            continue
        _flush_tool_results()
        if m.role == "user":
            out.append({"role": "user",
                        "content": [{"text": m.content or ""}]})
        elif m.role == "assistant":
            blocks: List[Dict[str, Any]] = []
            if m.content:
                blocks.append({"text": m.content})
            for tc in (m.tool_calls or []):
                blocks.append({"toolUse": {
                    "toolUseId": tc["id"],
                    "name": tc["name"],
                    "input": tc.get("arguments", {}),
                }})
            if not blocks:
                blocks.append({"text": _EMPTY_ASSISTANT_PLACEHOLDER})
            out.append({"role": "assistant", "content": blocks})
    _flush_tool_results()
    return out
