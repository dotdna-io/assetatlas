# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for ``RecordingLLMClient``.

These are integration-folder tests but don't need a live DB or network —
they exercise the record/replay key stability, persistence round-trip, and
error surfaces using tmp_path and a fake inner client.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Tuple

import pytest

from src.api.blueprints.chat.llm.base import (
    LLMChunk, LLMMessage, StreamResult, ToolSpec, Usage,
)

from .recording_llm import (
    RecordingError, RecordingLLMClient, _compute_key,
)


# ---------------------------------------------------------------------------
# Fake inner LLM that mirrors AnthropicClient's stream shape
# ---------------------------------------------------------------------------

@dataclass
class _FakeInnerStream:
    chunks: List[LLMChunk]
    result_obj: StreamResult

    async def __aenter__(self) -> "_FakeInnerStream":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for c in self.chunks:
            yield c

    @property
    def result(self) -> StreamResult:
        return self.result_obj


class _FakeInnerClient:
    provider = "fake-inner"
    model = "real-1"

    def __init__(self, scripted: List[Tuple[List[LLMChunk], StreamResult]]):
        self._scripted = list(scripted)
        self.calls = 0

    async def stream(self, **kwargs: Any) -> _FakeInnerStream:
        chunks, result = self._scripted[self.calls]
        self.calls += 1
        return _FakeInnerStream(chunks=list(chunks), result_obj=result)


# ---------------------------------------------------------------------------
# Key stability
# ---------------------------------------------------------------------------

def test_key_is_stable_for_same_inputs():
    sys = "you are a bot"
    messages = [LLMMessage(role="user", content="hi")]
    tools = [ToolSpec(name="ping", description="p", input_schema={})]
    assert _compute_key(sys, messages, tools) == _compute_key(sys, messages, tools)


def test_key_differs_when_messages_change():
    tools = []
    a = _compute_key("s", [LLMMessage(role="user", content="hi")], tools)
    b = _compute_key("s", [LLMMessage(role="user", content="bye")], tools)
    assert a != b


def test_key_ignores_admin_required_on_tools():
    """admin_required is a RBAC flag; it doesn't affect the LLM-facing tool
    spec, so changing it shouldn't invalidate a recording."""
    msgs = [LLMMessage(role="user", content="hi")]
    open_tool = ToolSpec(name="t", description="", input_schema={})
    locked_tool = ToolSpec(name="t", description="", input_schema={},
                           admin_required=True)
    assert (_compute_key("s", msgs, [open_tool])
            == _compute_key("s", msgs, [locked_tool]))


# ---------------------------------------------------------------------------
# Record mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_writes_entry_to_scenario_file(tmp_path: Path,
                                                     monkeypatch):
    monkeypatch.setenv("CHAT_LLM_REPLAY", "0")
    inner = _FakeInnerClient([
        ([LLMChunk(kind="text", text="Hi")],
         StreamResult(stop_reason="end_turn", final_text="Hi",
                      usage=Usage(input_tokens=3, output_tokens=1))),
    ])
    client = RecordingLLMClient(scenario="unit", fixtures_dir=tmp_path,
                                wrapped=inner)
    stream = await client.stream(system_prompt="sys", messages=[
        LLMMessage(role="user", content="hi")], tools=[], max_output_tokens=16)
    chunks = []
    async with stream:
        async for c in stream:
            chunks.append(c)

    recorded = tmp_path / "unit.json"
    assert recorded.exists()
    import json
    doc = json.loads(recorded.read_text())
    assert doc["scenario"] == "unit"
    assert len(doc["entries"]) == 1
    entry = doc["entries"][0]
    assert entry["result"]["final_text"] == "Hi"
    assert entry["chunks"][0]["kind"] == "text"
    assert entry["chunks"][0]["text"] == "Hi"


@pytest.mark.asyncio
async def test_record_refuses_without_wrapped(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CHAT_LLM_REPLAY", "0")
    client = RecordingLLMClient(scenario="unit", fixtures_dir=tmp_path,
                                wrapped=None)
    with pytest.raises(RecordingError, match="wrapped"):
        await client.stream(system_prompt="", messages=[], tools=[],
                            max_output_tokens=1)


@pytest.mark.asyncio
async def test_record_aborts_skip_persistence(tmp_path: Path, monkeypatch):
    """A stream that exits via exception shouldn't pollute the fixture."""
    monkeypatch.setenv("CHAT_LLM_REPLAY", "0")
    inner = _FakeInnerClient([
        ([LLMChunk(kind="text", text="Hi")],
         StreamResult(stop_reason="end_turn", final_text="Hi")),
    ])
    client = RecordingLLMClient(scenario="unit", fixtures_dir=tmp_path,
                                wrapped=inner)
    stream = await client.stream(system_prompt="sys", messages=[],
                                 tools=[], max_output_tokens=1)
    try:
        async with stream:
            raise RuntimeError("test aborts the turn")
    except RuntimeError:
        pass

    assert not (tmp_path / "unit.json").exists()


# ---------------------------------------------------------------------------
# Replay mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_replay_round_trip(tmp_path: Path, monkeypatch):
    """Record an entry, then re-open in replay mode and verify it plays
    the same chunks + result without touching the inner client."""
    monkeypatch.setenv("CHAT_LLM_REPLAY", "0")
    inner = _FakeInnerClient([
        ([LLMChunk(kind="text", text="partial "),
          LLMChunk(kind="text", text="answer")],
         StreamResult(stop_reason="end_turn", final_text="partial answer",
                      usage=Usage(input_tokens=7, output_tokens=2))),
    ])
    recorder = RecordingLLMClient(scenario="rt", fixtures_dir=tmp_path,
                                  wrapped=inner)

    sys = "sys"
    msgs = [LLMMessage(role="user", content="hi")]
    tools: List[ToolSpec] = []

    stream = await recorder.stream(system_prompt=sys, messages=msgs,
                                   tools=tools, max_output_tokens=16)
    async with stream:
        async for _ in stream:
            pass

    # Switch to replay mode; inner is None to prove it isn't called.
    monkeypatch.setenv("CHAT_LLM_REPLAY", "1")
    replay = RecordingLLMClient(scenario="rt", fixtures_dir=tmp_path,
                                wrapped=None)
    stream = await replay.stream(system_prompt=sys, messages=msgs,
                                 tools=tools, max_output_tokens=16)

    replayed = []
    async with stream:
        async for c in stream:
            replayed.append(c)
        res = stream.result

    assert [c.text for c in replayed] == ["partial ", "answer"]
    assert res.final_text == "partial answer"
    assert res.usage.input_tokens == 7
    assert res.usage.output_tokens == 2


@pytest.mark.asyncio
async def test_replay_missing_scenario_file_errors(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CHAT_LLM_REPLAY", "1")
    with pytest.raises(RecordingError, match="No recording at"):
        RecordingLLMClient(scenario="missing", fixtures_dir=tmp_path)


@pytest.mark.asyncio
async def test_replay_missing_key_errors(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CHAT_LLM_REPLAY", "1")
    # Pre-seed a scenario file with a different key so lookup misses.
    (tmp_path / "x.json").write_text(
        '{"scenario":"x","entries":[{"key_hash":"deadbeef","chunks":[],'
        '"result":{"stop_reason":"end_turn","final_text":"","tool_calls":[],'
        '"usage":{"input_tokens":0,"output_tokens":0,"cached_tokens":0}}}]}'
    )
    client = RecordingLLMClient(scenario="x", fixtures_dir=tmp_path)
    with pytest.raises(RecordingError, match="No recorded entry"):
        await client.stream(system_prompt="sys", messages=[
            LLMMessage(role="user", content="hi")],
            tools=[], max_output_tokens=16)


@pytest.mark.asyncio
async def test_replay_default_when_env_unset(tmp_path: Path, monkeypatch):
    """Unset CHAT_LLM_REPLAY defaults to replay so CI doesn't need API keys."""
    monkeypatch.delenv("CHAT_LLM_REPLAY", raising=False)
    with pytest.raises(RecordingError, match="No recording at"):
        RecordingLLMClient(scenario="absent", fixtures_dir=tmp_path)
