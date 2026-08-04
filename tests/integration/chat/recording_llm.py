# SPDX-License-Identifier: AGPL-3.0-or-later
"""Recording LLM client — wrap a real ``LLMClient`` to capture its streamed
events to disk, then replay them deterministically in later test runs.

A golden-question test makes N sequential calls to ``llm.stream(...)`` (one
per tool-iteration turn). We key each call by a SHA-256 over
``(system_prompt, messages, tools)`` serialized canonically. The recording
for a scenario is a JSON file listing those entries; replay looks up the
matching entry by key and replays its chunks plus a ``StreamResult``.

Mode is chosen by the ``CHAT_LLM_REPLAY`` env var:

- ``CHAT_LLM_REPLAY=1`` (default): replay from ``fixtures_dir/<scenario>.json``.
  Raises if no recording or no matching key.
- ``CHAT_LLM_REPLAY=0``: record. Wraps an injected real client, proxies its
  chunks to the caller while appending them plus the final ``StreamResult``
  to the scenario file.

The plan allows both modes behind ``pytest -k record`` with a real API key;
replay is the normal CI/dev path so tests don't need network or keys.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from src.api.blueprints.chat._canonical import stable_json
from src.api.blueprints.chat.llm.base import (
    LLMChunk, LLMMessage, StreamResult, ToolSpec, Usage,
)


class RecordingError(RuntimeError):
    """Raised when record/replay cannot proceed (missing fixture, no wrapped
    client for record mode, malformed recording, etc.)."""


def _canonical_messages(messages: List[LLMMessage]) -> List[Dict[str, Any]]:
    """Stable JSON shape for LLMMessage used both for keying and file dumps."""
    return [{
        "role": m.role,
        "content": m.content,
        "tool_calls": m.tool_calls,
        "tool_call_id": m.tool_call_id,
        "tool_name": m.tool_name,
    } for m in messages]


def _canonical_tools(tools: List[ToolSpec]) -> List[Dict[str, Any]]:
    """Stable JSON shape for ToolSpec used for keying and file dumps.

    ``admin_required`` is intentionally excluded — it doesn't affect the
    LLM-facing spec and can drift between record/replay without invalidating
    the recorded answer.
    """
    return [{
        "name": t.name,
        "description": t.description,
        "input_schema": t.input_schema,
    } for t in tools]


def _compute_key(system_prompt: str, messages: List[LLMMessage],
                 tools: List[ToolSpec]) -> str:
    payload = stable_json({
        "system_prompt": system_prompt,
        "messages": _canonical_messages(messages),
        "tools": _canonical_tools(tools),
    })
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _chunk_to_dict(c: LLMChunk) -> Dict[str, Any]:
    # asdict drops None fields to keep fixtures compact
    return {k: v for k, v in asdict(c).items() if v is not None}


def _dict_to_chunk(d: Dict[str, Any]) -> LLMChunk:
    return LLMChunk(
        kind=d["kind"],
        text=d.get("text"),
        tool_call_id=d.get("tool_call_id"),
        tool_name=d.get("tool_name"),
        tool_args_delta=d.get("tool_args_delta"),
    )


def _result_to_dict(r: StreamResult) -> Dict[str, Any]:
    return {
        "stop_reason": r.stop_reason,
        "final_text": r.final_text,
        "tool_calls": r.tool_calls,
        "usage": asdict(r.usage),
        "error": r.error,
    }


def _dict_to_result(d: Dict[str, Any]) -> StreamResult:
    u = d.get("usage") or {}
    return StreamResult(
        stop_reason=d.get("stop_reason", "end_turn"),
        final_text=d.get("final_text", "") or "",
        tool_calls=list(d.get("tool_calls") or []),
        usage=Usage(
            input_tokens=int(u.get("input_tokens", 0) or 0),
            output_tokens=int(u.get("output_tokens", 0) or 0),
            cached_tokens=int(u.get("cached_tokens", 0) or 0),
        ),
        error=d.get("error"),
    )


class RecordingLLMClient:
    """LLM client facade that records or replays LLM turns for one scenario.

    One instance binds to one scenario; construct a fresh client per test.
    Thread-safety is not a concern — tests are single-threaded, and
    concurrency at the recording level would mangle the event order anyway.
    """

    def __init__(
        self,
        *,
        scenario: str,
        fixtures_dir: Path,
        wrapped: Optional[Any] = None,
        mode: Optional[str] = None,
    ) -> None:
        self.provider = getattr(wrapped, "provider", "replay")
        self.model = getattr(wrapped, "model", "replay")
        self._scenario = scenario
        self._fixtures_dir = Path(fixtures_dir)
        self._wrapped = wrapped
        self._mode = mode or self._resolve_mode()

        self._path = self._fixtures_dir / f"{scenario}.json"
        if self._mode == "replay":
            self._entries = self._load_entries()
        else:
            self._entries = []
            self._fixtures_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _resolve_mode() -> str:
        # Default to replay so CI does not need an API key. Recording is
        # explicit (CHAT_LLM_REPLAY=0) and requires a wrapped real client.
        return "record" if os.getenv("CHAT_LLM_REPLAY") == "0" else "replay"

    def _load_entries(self) -> List[Dict[str, Any]]:
        if not self._path.exists():
            raise RecordingError(
                f"No recording at {self._path}. Re-run with "
                f"CHAT_LLM_REPLAY=0 and a real CHAT_LLM_API_KEY to record."
            )
        with self._path.open("r", encoding="utf-8") as f:
            doc = json.load(f)
        return list(doc.get("entries") or [])

    def _write_entries(self) -> None:
        doc = {"scenario": self._scenario,
               "provider": self.provider,
               "model": self.model,
               "entries": self._entries}
        with self._path.open("w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, sort_keys=False)
            f.write("\n")

    async def stream(self, *, system_prompt: str,
                     messages: List[LLMMessage],
                     tools: List[ToolSpec],
                     max_output_tokens: int,
                     cache_stable_tool_prefix: Optional[int] = None) -> Any:
        # cache_stable_tool_prefix is a prompt-cache placement hint that doesn't
        # affect the recorded/replayed turn; accepted for signature parity with
        # the real LLMClient.stream (the service passes it on every call).
        key = _compute_key(system_prompt, messages, tools)
        if self._mode == "replay":
            entry = self._find_entry(key)
            return _ReplayStream(
                chunks=[_dict_to_chunk(c) for c in entry.get("chunks") or []],
                result=_dict_to_result(entry.get("result") or {}),
            )

        if self._wrapped is None:
            raise RecordingError(
                "Record mode requires a wrapped real LLM client "
                "(pass `wrapped=AnthropicClient(...)`)."
            )
        real_stream = await self._wrapped.stream(
            system_prompt=system_prompt, messages=messages, tools=tools,
            max_output_tokens=max_output_tokens,
        )
        return _RecordStream(
            inner=real_stream, on_complete=lambda chunks, result:
            self._record_entry(key, system_prompt, messages, tools,
                               chunks, result),
        )

    def _find_entry(self, key: str) -> Dict[str, Any]:
        for e in self._entries:
            if e.get("key_hash") == key:
                return e
        raise RecordingError(
            f"No recorded entry for key {key[:12]}... in scenario "
            f"{self._scenario!r}. Re-record the scenario if the prompt "
            f"or tool registry changed."
        )

    def _record_entry(self, key: str, system_prompt: str,
                      messages: List[LLMMessage], tools: List[ToolSpec],
                      chunks: List[LLMChunk],
                      result: StreamResult) -> None:
        self._entries.append({
            "key_hash": key,
            "key_preview": {
                # truncated previews so fixtures are inspectable without
                # drowning the diff on large conversations
                "system_prompt": system_prompt[:200],
                "messages": _canonical_messages(messages)[-3:],
                "tool_count": len(tools),
            },
            "chunks": [_chunk_to_dict(c) for c in chunks],
            "result": _result_to_dict(result),
        })
        self._write_entries()


class _ReplayStream:
    def __init__(self, *, chunks: List[LLMChunk], result: StreamResult) -> None:
        self._chunks = chunks
        self._result = result

    async def __aenter__(self) -> "_ReplayStream":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[LLMChunk]:
        return self._aiter()

    async def _aiter(self) -> AsyncIterator[LLMChunk]:
        for c in self._chunks:
            yield c

    @property
    def result(self) -> StreamResult:
        return self._result


class _RecordStream:
    """Proxy the wrapped stream, cache its chunks + result, persist on close."""

    def __init__(self, *, inner: Any, on_complete) -> None:
        self._inner = inner
        self._on_complete = on_complete
        self._chunks: List[LLMChunk] = []

    async def __aenter__(self) -> "_RecordStream":
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        await self._inner.__aexit__(*exc)
        # Persist only after a clean close so partial records don't
        # pollute fixtures on test-driven aborts.
        if exc[0] is None:
            self._on_complete(self._chunks, self._inner.result)

    def __aiter__(self) -> AsyncIterator[LLMChunk]:
        return self._aiter()

    async def _aiter(self) -> AsyncIterator[LLMChunk]:
        async for c in self._inner:
            self._chunks.append(c)
            yield c

    @property
    def result(self) -> StreamResult:
        return self._inner.result
