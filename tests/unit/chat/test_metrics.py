# SPDX-License-Identifier: AGPL-3.0-or-later
"""Chat Prometheus metrics: verify counters/histograms fire with expected
labels at each emission point (tool registry, turn orchestrator, routes)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List, Tuple
from unittest.mock import MagicMock

import pytest

from src.api.blueprints.chat.llm.base import LLMChunk, StreamResult, Usage
from src.api.blueprints.chat.repository import MessageRow, SessionRow
from src.api.blueprints.chat.service import ChatService
from src.api.blueprints.chat.tool_registry import ToolRegistry
from src.api.utils import metrics as m


USER_ID = "00000000-0000-0000-0000-000000000001"
SESSION_ID = "00000000-0000-0000-0000-000000000099"


# ---------------------------------------------------------------------------
# Helpers — read Prometheus collector samples by label set
# ---------------------------------------------------------------------------

def _counter_value(counter, **labels) -> float:
    """Return the current sample value for ``counter`` with matching labels.

    Returns 0.0 if the labelled child hasn't been created yet. Scans the
    collector's internal samples rather than reaching into _metrics so the
    test keeps working across prometheus_client versions.
    """
    for metric in counter.collect():
        for sample in metric.samples:
            # Counter samples come out with "_total" suffix plus a "_created"
            # gauge sibling — we only want the count.
            if not sample.name.endswith("_total"):
                continue
            if all(sample.labels.get(k) == v for k, v in labels.items()):
                return sample.value
    return 0.0


def _histogram_count(histogram, **labels) -> float:
    for metric in histogram.collect():
        for sample in metric.samples:
            if not sample.name.endswith("_count"):
                continue
            if all(sample.labels.get(k) == v for k, v in labels.items()):
                return sample.value
    return 0.0


# ---------------------------------------------------------------------------
# Fakes borrowed from test_service
# ---------------------------------------------------------------------------

@dataclass
class FakeStream:
    chunks: List[LLMChunk]
    result_obj: StreamResult

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for c in self.chunks:
            yield c

    @property
    def result(self):
        return self.result_obj


class FakeLLM:
    provider = "fake"
    model = "fake-1"

    def __init__(self, scripted: List[Tuple[List[LLMChunk], StreamResult]]):
        self._scripted = scripted
        self.calls = 0

    async def stream(self, **kwargs: Any) -> FakeStream:
        chunks, result = self._scripted[self.calls]
        self.calls += 1
        return FakeStream(chunks, result)


def _make_session() -> SessionRow:
    now = datetime.now(timezone.utc)
    return SessionRow(id=SESSION_ID, user_id=USER_ID, title="t",
                      model="fake-1", provider="fake",
                      created_at=now, updated_at=now)


def _make_message(msg_id: str, role: str, content=None,
                  tool_calls=None, seq: int = 1) -> MessageRow:
    now = datetime.now(timezone.utc)
    return MessageRow(id=msg_id, session_id=SESSION_ID, seq=seq, role=role,
                      content=content, tool_calls=tool_calls,
                      input_tokens=None, output_tokens=None, error=None,
                      created_at=now)


def _wire_repo(svc: ChatService) -> MagicMock:
    repo = MagicMock()
    session = _make_session()
    repo.create_session.return_value = session
    repo.load_session.return_value = session
    counter = {"n": 0}

    def _append(*a, **kw):
        counter["n"] += 1
        return _make_message(
            msg_id=f"msg-{counter['n']}",
            role=kw.get("role", "user"),
            content=kw.get("content"),
            tool_calls=kw.get("tool_calls"),
            seq=counter["n"],
        )

    repo.append_message.side_effect = _append
    repo.load_messages.return_value = []
    repo.load_tool_calls_by_message.return_value = {}
    repo.record_tool_call.return_value = None
    svc.repo = repo
    return repo


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_call_ok_increments_counter_and_histogram():
    registry = ToolRegistry()

    async def _ping() -> str:
        return "pong"

    registry.register("ping_ok", _ping, "", {"type": "object"},
                      admin_required=False)

    before_ct = _counter_value(m.chat_tool_calls_total,
                               tool_name="ping_ok", status="ok")
    before_hist = _histogram_count(m.chat_tool_call_duration_seconds,
                                   tool_name="ping_ok")

    inv = await registry.invoke("ping_ok", {}, user_role="user")
    assert inv.status == "ok"

    assert _counter_value(m.chat_tool_calls_total,
                          tool_name="ping_ok", status="ok") == before_ct + 1
    assert _histogram_count(m.chat_tool_call_duration_seconds,
                            tool_name="ping_ok") == before_hist + 1


@pytest.mark.asyncio
async def test_tool_call_error_increments_error_status():
    registry = ToolRegistry()

    async def _boom() -> str:
        raise RuntimeError("kaboom")

    registry.register("boom", _boom, "", {"type": "object"},
                      admin_required=False)

    before = _counter_value(m.chat_tool_calls_total,
                            tool_name="boom", status="error")
    inv = await registry.invoke("boom", {}, user_role="user")
    assert inv.status == "error"
    assert _counter_value(m.chat_tool_calls_total,
                          tool_name="boom", status="error") == before + 1


@pytest.mark.asyncio
async def test_tool_call_denied_increments_denied_status():
    registry = ToolRegistry()

    async def _admin_only() -> str:
        return "secret"

    registry.register("admin_only", _admin_only, "", {"type": "object"},
                      admin_required=True)

    before = _counter_value(m.chat_tool_calls_total,
                            tool_name="admin_only", status="denied")
    inv = await registry.invoke("admin_only", {}, user_role="user")
    assert inv.status == "denied"
    assert _counter_value(m.chat_tool_calls_total,
                          tool_name="admin_only", status="denied") == before + 1


@pytest.mark.asyncio
async def test_tool_call_timeout_increments_timeout_status():
    import asyncio

    registry = ToolRegistry(per_tool_timeout=0.01)

    async def _slow() -> str:
        await asyncio.sleep(1.0)
        return "never"

    registry.register("slow", _slow, "", {"type": "object"},
                      admin_required=False)

    before = _counter_value(m.chat_tool_calls_total,
                            tool_name="slow", status="timeout")
    inv = await registry.invoke("slow", {}, user_role="user")
    assert inv.status == "timeout"
    assert _counter_value(m.chat_tool_calls_total,
                          tool_name="slow", status="timeout") == before + 1


# ---------------------------------------------------------------------------
# Turn-level metrics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_turn_done_emits_tokens_and_duration():
    llm = FakeLLM([
        ([LLMChunk(kind="text", text="Hi")],
         StreamResult(stop_reason="end_turn", final_text="Hi",
                      usage=Usage(input_tokens=7, output_tokens=3))),
    ])
    svc = ChatService(db=MagicMock(), llm=llm, registry=ToolRegistry(),
                      max_tool_iterations=3)
    _wire_repo(svc)

    before_turns = _counter_value(m.chat_turns_total,
                                  provider="fake", status="done")
    before_in = _counter_value(m.chat_tokens_input_total,
                               provider="fake", model="fake-1")
    before_out = _counter_value(m.chat_tokens_output_total,
                                provider="fake", model="fake-1")
    before_dur = _histogram_count(m.chat_turn_duration_seconds,
                                  provider="fake")

    events = []
    async for e in svc.stream_turn(session_id=SESSION_ID, user_text="hi",
                                   user_role="user", user_id=USER_ID):
        events.append(e)

    assert _counter_value(m.chat_turns_total,
                          provider="fake", status="done") == before_turns + 1
    assert _counter_value(m.chat_tokens_input_total,
                          provider="fake", model="fake-1") == before_in + 7
    assert _counter_value(m.chat_tokens_output_total,
                          provider="fake", model="fake-1") == before_out + 3
    assert _histogram_count(m.chat_turn_duration_seconds,
                            provider="fake") == before_dur + 1


@pytest.mark.asyncio
async def test_chat_turn_iteration_limit_records_distinct_status():
    tool_call = {"id": "x", "name": "ping", "arguments": {}}
    scripted = [
        ([LLMChunk(kind="tool_call_start", tool_call_id="x",
                   tool_name="ping")],
         StreamResult(stop_reason="tool_use", tool_calls=[tool_call],
                      usage=Usage(input_tokens=1, output_tokens=1)))
        for _ in range(5)
    ]
    llm = FakeLLM(scripted)
    registry = ToolRegistry()

    async def _ping() -> str:
        return "pong"

    registry.register("ping", _ping, "", {"type": "object"},
                      admin_required=False)
    svc = ChatService(db=MagicMock(), llm=llm, registry=registry,
                      max_tool_iterations=2)
    _wire_repo(svc)

    before = _counter_value(m.chat_turns_total, provider="fake",
                            status="tool_iterations_exceeded")
    async for _ in svc.stream_turn(session_id=SESSION_ID, user_text="hi",
                                   user_role="user", user_id=USER_ID):
        pass

    assert _counter_value(
        m.chat_turns_total, provider="fake",
        status="tool_iterations_exceeded",
    ) == before + 1


@pytest.mark.asyncio
async def test_chat_turn_session_not_found_records_status():
    llm = FakeLLM([])
    svc = ChatService(db=MagicMock(), llm=llm, registry=ToolRegistry())
    repo = _wire_repo(svc)
    repo.load_session.return_value = None

    before = _counter_value(m.chat_turns_total, provider="fake",
                            status="session_not_found")
    async for _ in svc.stream_turn(session_id=SESSION_ID, user_text="hi",
                                   user_role="user", user_id=USER_ID):
        pass

    assert _counter_value(m.chat_turns_total, provider="fake",
                          status="session_not_found") == before + 1


@pytest.mark.asyncio
async def test_chat_turn_timeout_records_status_and_rate_limit_hit():
    import asyncio as _asyncio

    class HangingStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        def __aiter__(self):
            return self._iter()

        async def _iter(self):
            await _asyncio.sleep(5.0)
            yield LLMChunk(kind="text", text="never")

        @property
        def result(self):
            return StreamResult(
                stop_reason="end_turn", final_text="",
                usage=Usage(input_tokens=0, output_tokens=0),
            )

    class HangingLLM:
        provider = "fake"
        model = "fake-1"

        async def stream(self, **kw):
            return HangingStream()

    svc = ChatService(db=MagicMock(), llm=HangingLLM(),
                      registry=ToolRegistry(), turn_timeout_seconds=0)
    _wire_repo(svc)

    before_turn = _counter_value(m.chat_turns_total, provider="fake",
                                 status="turn_timeout")
    before_rl = _counter_value(m.chat_rate_limit_hits_total,
                               limit_type="turn_timeout")
    async for _ in svc.stream_turn(session_id=SESSION_ID, user_text="hi",
                                   user_role="user", user_id=USER_ID):
        pass

    assert _counter_value(m.chat_turns_total, provider="fake",
                          status="turn_timeout") == before_turn + 1
    assert _counter_value(m.chat_rate_limit_hits_total,
                          limit_type="turn_timeout") == before_rl + 1


# ---------------------------------------------------------------------------
# Helpers behave sanely — feedback + rate-limit trackers are tested via the
# route layer in the blueprint test; these direct unit checks ensure the
# helper functions don't silently swallow their arguments.
# ---------------------------------------------------------------------------

def test_track_chat_feedback_increments_counter():
    before_up = _counter_value(m.chat_feedback_total, rating="up")
    before_down = _counter_value(m.chat_feedback_total, rating="down")
    m.track_chat_feedback("up")
    m.track_chat_feedback("down")
    m.track_chat_feedback("down")
    assert _counter_value(m.chat_feedback_total, rating="up") == before_up + 1
    assert _counter_value(m.chat_feedback_total, rating="down") == before_down + 2


def test_track_chat_rate_limit_increments_counter():
    before_d = _counter_value(m.chat_rate_limit_hits_total, limit_type="daily")
    before_c = _counter_value(m.chat_rate_limit_hits_total,
                              limit_type="concurrent")
    m.track_chat_rate_limit("daily")
    m.track_chat_rate_limit("concurrent")
    assert _counter_value(m.chat_rate_limit_hits_total,
                          limit_type="daily") == before_d + 1
    assert _counter_value(m.chat_rate_limit_hits_total,
                          limit_type="concurrent") == before_c + 1
