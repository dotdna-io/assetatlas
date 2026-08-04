# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ChatService orchestration (mocked repo + fake LLM)."""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List, Tuple
from unittest.mock import MagicMock

import pytest

from src.api.blueprints.chat.llm.base import LLMChunk, StreamResult, Usage
from src.api.blueprints.chat.repository import (
    MessageRow, SessionRow, ToolCallRow,
)
from src.api.blueprints.chat.service import (
    ChatService, _clean_title, _fallback_title, _to_llm_messages,
)
from src.api.blueprints.chat.tool_registry import ToolRegistry


USER_ID = "00000000-0000-0000-0000-000000000001"
SESSION_ID = "00000000-0000-0000-0000-000000000099"


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


def _make_session(session_id: str = SESSION_ID,
                  user_id: str = USER_ID) -> SessionRow:
    now = datetime.now(timezone.utc)
    return SessionRow(id=session_id, user_id=user_id, title="t",
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
    """Replace svc.repo with a configured mock returning fake rows."""
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


@pytest.mark.asyncio
async def test_stream_turn_text_only():
    llm = FakeLLM([
        ([LLMChunk(kind="text", text="Hello")],
         StreamResult(stop_reason="end_turn", final_text="Hello",
                      usage=Usage(input_tokens=5, output_tokens=1))),
    ])
    registry = ToolRegistry()
    svc = ChatService(db=MagicMock(), llm=llm, registry=registry,
                      max_tool_iterations=5)
    _wire_repo(svc)
    session = svc.create_session(user_id=USER_ID, title="t")

    events = []
    async for e in svc.stream_turn(session_id=session.id, user_text="hi",
                                   user_role="user", user_id=USER_ID):
        events.append(e)

    kinds = [e.kind for e in events]
    assert "message.start" in kinds
    assert "delta" in kinds
    assert "message.end" in kinds
    assert "done" in kinds


def test_stream_turn_honors_routing_flag_from_worker_thread():
    """Regression for the PR #803 flag-propagation bug.

    The SSE route drives the *entire* turn inside a daemon worker thread
    (``routes.py``: ``threading.Thread(target=lambda: asyncio.run(driver()))``)
    so Flask's sync workers can iterate the ``async def`` generator. Python
    ``contextvars`` — which back Flask 3.x's ``current_app`` — are NOT
    inherited across a thread boundary, so reading the routing flag via
    ``current_app.settings_manager`` raises ``RuntimeError`` *in that thread*
    and the ``except RuntimeError`` silently forced routing OFF on every
    production request, regardless of the DB value. (Every other dependency
    in ``_stream_inner`` is an injected ``self.*`` attribute, which is why
    only the flag read misbehaved.)

    The flag must therefore be read from a dependency injected at
    construction — like ``providers_service`` — not from ``current_app``.
    This drives the exact production topology: ``settings_manager`` reports
    the flag ON, the turn runs from a separate thread with no app context,
    and routing must still engage (the router selects the per-turn tool set
    instead of advertising every role tool).

    Encodes WHY, not just WHAT: the assertion fails the moment the read goes
    back through ``current_app`` (or any other app-context-bound path),
    because the worker thread has no context to resolve it against.
    """
    class _Spec:
        def __init__(self, name: str):
            self.name = name

    class _RecordingRouter:
        """Router stub: records that selection ran and returns a reduced set
        distinct from the full role tool list."""
        def __init__(self, reduced):
            self.reduced = reduced
            self.calls: List[Any] = []

        def select(self, user_text, role, loaded, top_k=None):
            self.calls.append((user_text, role, set(loaded)))
            return list(self.reduced)

        def stable_prefix_len(self, specs):
            # Routing ON consults the router for the cache-prefix boundary too;
            # the value is irrelevant to THIS test (it asserts the selected tool
            # set, not the cache breakpoint), so the whole reduced set is stable.
            return len(specs)

    class _CapturingLLM(FakeLLM):
        def __init__(self, scripted):
            super().__init__(scripted)
            self.tools_seen: List[List[Any]] = []

        async def stream(self, **kwargs: Any) -> FakeStream:
            self.tools_seen.append(list(kwargs.get("tools") or []))
            return await super().stream(**kwargs)

    class _SettingsStub:
        """Stand-in for SettingsManager.get(category, key, default)."""
        def __init__(self, mapping):
            self.mapping = mapping

        def get(self, category, key, default=None):
            return self.mapping.get((category, key), default)

    reduced = [_Spec("core_only")]
    router = _RecordingRouter(reduced)
    llm = _CapturingLLM([
        ([LLMChunk(kind="text", text="hi")],
         StreamResult(stop_reason="end_turn", final_text="hi",
                      usage=Usage(input_tokens=1, output_tokens=1))),
    ])
    # A role tool set DISTINCT from the router's reduced set, so "routing
    # engaged" is observable in what the LLM is actually offered.
    registry = ToolRegistry()

    async def _list_assets() -> str:
        return "ok"

    registry.register("list_assets", _list_assets, "",
                      {"type": "object", "properties": {}},
                      admin_required=False)

    settings = _SettingsStub({("chat", "tool_routing_enabled"): True})
    svc = ChatService(db=MagicMock(), llm=llm, registry=registry,
                      tool_router=router, settings_manager=settings,
                      max_tool_iterations=3)
    _wire_repo(svc)
    session = svc.create_session(user_id=USER_ID, title="t")

    # Drive the turn the way the SSE route does: a separate daemon thread
    # running its own event loop, with NO Flask app context present.
    result: dict = {}

    def _target():
        async def _drive():
            async for _ in svc.stream_turn(
                session_id=session.id, user_text="how many servers?",
                user_role="admin", user_id=USER_ID,
            ):
                pass
        try:
            asyncio.run(_drive())
        except BaseException as e:  # surface worker-thread failures to assert
            result["error"] = e

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=10)

    assert "error" not in result, \
        f"worker thread raised: {result.get('error')!r}"
    # Routing engaged: the router was consulted and the LLM received its
    # reduced set, not the full role tool list. On the bug, the flag read
    # raised RuntimeError in the context-less thread → routing fell back to
    # OFF → the router was never called and the LLM saw every role tool.
    assert router.calls, (
        "router.select was never called — the routing flag read returned "
        "False inside the worker thread (no Flask app context), so routing "
        "never engaged despite the setting being ON"
    )
    assert llm.tools_seen, "LLM stream was never invoked"
    assert [s.name for s in llm.tools_seen[0]] == ["core_only"], (
        "LLM was offered the full role tool set instead of the router's "
        "reduced selection — routing did not engage"
    )


@pytest.mark.asyncio
async def test_stream_turn_passes_stable_prefix_len_when_routing_on():
    """Routing ON: the prompt-cache breakpoint must sit after the STABLE tool
    prefix, not the whole advertised list — whose retrieved tail varies per turn
    and would bust the cache. The service therefore passes the router's
    stable_prefix_len(iter_tools), derived from the selected tools, as
    cache_stable_tool_prefix — NOT len(iter_tools). The test pins prefix(2) !=
    len(3) so a regression to len() is caught."""
    class _Spec:
        def __init__(self, name: str):
            self.name = name

    class _StubRouter:
        def __init__(self):
            # 2 stable core tools + 1 variable retrieved tool: prefix 2 != len 3.
            self.selected = [_Spec("query_assets"), _Spec("get_asset"),
                             _Spec("get_integration")]
            self.prefix_arg = None

        def select(self, user_text, role, loaded, top_k=None):
            return list(self.selected)

        def stable_prefix_len(self, specs):
            self.prefix_arg = specs
            return 2

    class _SettingsStub:
        def get(self, category, key, default=None):
            return (True if (category, key) == ("chat", "tool_routing_enabled")
                    else default)

    captured: dict = {}

    class _CapturingLLM(FakeLLM):
        async def stream(self, **kwargs: Any) -> FakeStream:
            captured["prefix"] = kwargs.get("cache_stable_tool_prefix")
            captured["tools"] = list(kwargs.get("tools") or [])
            return await super().stream(**kwargs)

    router = _StubRouter()
    llm = _CapturingLLM([
        ([LLMChunk(kind="text", text="hi")],
         StreamResult(stop_reason="end_turn", final_text="hi",
                      usage=Usage(input_tokens=1, output_tokens=1))),
    ])
    svc = ChatService(db=MagicMock(), llm=llm, registry=ToolRegistry(),
                      tool_router=router, settings_manager=_SettingsStub(),
                      max_tool_iterations=3)
    _wire_repo(svc)
    session = svc.create_session(user_id=USER_ID, title="t")

    async for _ in svc.stream_turn(session_id=session.id,
                                   user_text="how many servers?",
                                   user_role="admin", user_id=USER_ID):
        pass

    # The router was asked for the stable prefix of the SELECTED tools...
    assert [s.name for s in router.prefix_arg] == \
        ["query_assets", "get_asset", "get_integration"]
    # ...and that count (2), not len(iter_tools)==3, reached the LLM.
    assert captured["prefix"] == 2
    assert len(captured["tools"]) == 3


@pytest.mark.asyncio
async def test_stream_turn_passes_full_len_as_stable_prefix_when_routing_off():
    """Routing OFF: the whole advertised set is byte-identical every iteration,
    so the entire list is the stable prefix and the breakpoint stays at the end
    (today's behavior). The service passes len(iter_tools)."""
    captured: dict = {}

    class _CapturingLLM(FakeLLM):
        async def stream(self, **kwargs: Any) -> FakeStream:
            captured["prefix"] = kwargs.get("cache_stable_tool_prefix")
            captured["tools"] = list(kwargs.get("tools") or [])
            return await super().stream(**kwargs)

    llm = _CapturingLLM([
        ([LLMChunk(kind="text", text="hi")],
         StreamResult(stop_reason="end_turn", final_text="hi",
                      usage=Usage(input_tokens=1, output_tokens=1))),
    ])
    registry = ToolRegistry()

    async def _a() -> str:
        return "ok"

    async def _b() -> str:
        return "ok"

    registry.register("list_assets", _a, "",
                      {"type": "object", "properties": {}},
                      admin_required=False)
    registry.register("get_dashboard", _b, "",
                      {"type": "object", "properties": {}},
                      admin_required=False)

    # No router, no settings_manager → routing forced OFF.
    svc = ChatService(db=MagicMock(), llm=llm, registry=registry,
                      max_tool_iterations=3)
    _wire_repo(svc)
    session = svc.create_session(user_id=USER_ID, title="t")

    async for _ in svc.stream_turn(session_id=session.id, user_text="hi",
                                   user_role="user", user_id=USER_ID):
        pass

    # Whole advertised list is the stable prefix → prefix == len(tools).
    assert captured["prefix"] == len(captured["tools"]) == 2


@pytest.mark.asyncio
async def test_stream_turn_with_one_tool_call():
    tool_call = {"id": "t1", "name": "ping", "arguments": {}}
    llm = FakeLLM([
        ([LLMChunk(kind="tool_call_start", tool_call_id="t1",
                   tool_name="ping"),
          LLMChunk(kind="tool_call_end", tool_call_id="t1",
                   tool_name="ping")],
         StreamResult(stop_reason="tool_use", tool_calls=[tool_call],
                      usage=Usage(input_tokens=3, output_tokens=1))),
        ([LLMChunk(kind="text", text="ok")],
         StreamResult(stop_reason="end_turn", final_text="ok",
                      usage=Usage(input_tokens=4, output_tokens=1))),
    ])
    registry = ToolRegistry()

    async def _ping() -> str:
        return "pong"

    registry.register("ping", _ping, "ping",
                      {"type": "object", "properties": {}},
                      admin_required=False)
    svc = ChatService(db=MagicMock(), llm=llm, registry=registry,
                      max_tool_iterations=5)
    _wire_repo(svc)
    session = svc.create_session(user_id=USER_ID, title="t")

    events = []
    async for e in svc.stream_turn(session_id=session.id,
                                   user_text="do ping",
                                   user_role="user", user_id=USER_ID):
        events.append(e)

    assert any(e.kind == "tool.start" and e.payload["name"] == "ping"
               for e in events)
    assert any(e.kind == "tool.end" and e.payload["name"] == "ping"
               and e.payload["ok"] is True for e in events)
    assert any(e.kind == "message.end" for e in events)


@pytest.mark.asyncio
async def test_stream_turn_propagates_artifact_on_tool_end_and_persists():
    """A tool that emits an artifact should: (1) ride the artifact on its
    tool.end SSE payload for live rendering, and (2) be persisted via
    repo.record_tool_call(artifact=...) for history reload."""
    tool_call = {"id": "t1", "name": "chartable", "arguments": {}}
    llm = FakeLLM([
        ([LLMChunk(kind="tool_call_start", tool_call_id="t1",
                   tool_name="chartable"),
          LLMChunk(kind="tool_call_end", tool_call_id="t1",
                   tool_name="chartable")],
         StreamResult(stop_reason="tool_use", tool_calls=[tool_call],
                      usage=Usage(input_tokens=3, output_tokens=1))),
        ([LLMChunk(kind="text", text="done")],
         StreamResult(stop_reason="end_turn", final_text="done",
                      usage=Usage(input_tokens=4, output_tokens=1))),
    ])

    artifact = {
        "type": "donut", "title": "Assets by Category",
        "series": [{"label": "server", "value": 5}],
        "meta": {"total": 5, "other_count": 0},
    }

    registry = ToolRegistry()

    async def _handler():
        return "COUNT: 5", artifact

    registry.register("chartable", _handler, "",
                      {"type": "object", "properties": {}},
                      admin_required=False)

    svc = ChatService(db=MagicMock(), llm=llm, registry=registry,
                      max_tool_iterations=5)
    repo = _wire_repo(svc)
    session = svc.create_session(user_id=USER_ID, title="t")

    events = []
    async for e in svc.stream_turn(session_id=session.id,
                                   user_text="chart it",
                                   user_role="user", user_id=USER_ID):
        events.append(e)

    # SSE: tool.end payload carries the artifact for inline rendering.
    tool_end = next(e for e in events
                    if e.kind == "tool.end" and e.payload["name"] == "chartable")
    assert tool_end.payload["artifact"] == artifact

    # Persistence: record_tool_call was called with artifact=... so
    # reopening the conversation redraws the chart from the DB.
    persisted_artifacts = [
        c.kwargs.get("artifact") for c in repo.record_tool_call.call_args_list
    ]
    assert artifact in persisted_artifacts


@pytest.mark.asyncio
async def test_stream_turn_text_only_tool_has_no_artifact_on_payload():
    """Tools that return a plain string keep the tool.end payload shape
    unchanged — no ``artifact`` key at all. Existing SSE consumers must
    not see a new field on every event."""
    tool_call = {"id": "t1", "name": "ping", "arguments": {}}
    llm = FakeLLM([
        ([LLMChunk(kind="tool_call_start", tool_call_id="t1",
                   tool_name="ping"),
          LLMChunk(kind="tool_call_end", tool_call_id="t1",
                   tool_name="ping")],
         StreamResult(stop_reason="tool_use", tool_calls=[tool_call],
                      usage=Usage(input_tokens=3, output_tokens=1))),
        ([LLMChunk(kind="text", text="ok")],
         StreamResult(stop_reason="end_turn", final_text="ok",
                      usage=Usage(input_tokens=4, output_tokens=1))),
    ])
    registry = ToolRegistry()

    async def _ping():
        return "pong"

    registry.register("ping", _ping, "",
                      {"type": "object", "properties": {}},
                      admin_required=False)
    svc = ChatService(db=MagicMock(), llm=llm, registry=registry,
                      max_tool_iterations=5)
    repo = _wire_repo(svc)
    session = svc.create_session(user_id=USER_ID, title="t")

    events = []
    async for e in svc.stream_turn(session_id=session.id, user_text="p",
                                   user_role="user", user_id=USER_ID):
        events.append(e)

    tool_end = next(e for e in events
                    if e.kind == "tool.end" and e.payload["name"] == "ping")
    assert "artifact" not in tool_end.payload

    # Persistence still carries artifact=None explicitly — the DB insert
    # gets a consistent param shape regardless of whether a chart rendered.
    ping_call = next(c for c in repo.record_tool_call.call_args_list
                     if c.kwargs.get("tool_name") == "ping")
    assert ping_call.kwargs.get("artifact") is None


@pytest.mark.asyncio
async def test_stream_turn_exceeds_iteration_limit():
    tool_call = {"id": "x", "name": "ping", "arguments": {}}

    def _stuck():
        return (
            [LLMChunk(kind="tool_call_start", tool_call_id="x",
                      tool_name="ping"),
             LLMChunk(kind="tool_call_end", tool_call_id="x",
                      tool_name="ping")],
            StreamResult(stop_reason="tool_use", tool_calls=[tool_call],
                         usage=Usage(input_tokens=1, output_tokens=1)),
        )

    llm = FakeLLM([_stuck() for _ in range(10)])
    registry = ToolRegistry()

    async def _ping() -> str:
        return "pong"

    registry.register("ping", _ping, "",
                      {"type": "object", "properties": {}},
                      admin_required=False)
    svc = ChatService(db=MagicMock(), llm=llm, registry=registry,
                      max_tool_iterations=3)
    repo = _wire_repo(svc)
    session = svc.create_session(user_id=USER_ID, title="t")

    events = []
    async for e in svc.stream_turn(session_id=session.id, user_text="hi",
                                   user_role="user", user_id=USER_ID):
        events.append(e)

    # The summary fallback always produces a user-facing message — either
    # the model's summary text or a synthesised apology — so we should NOT
    # see an error frame on the iteration-cap path. The audit tag on the
    # persisted message carries the root cause for ops observability.
    assert not any(e.kind == "error" for e in events), \
        f"unexpected error frame: {[(e.kind, e.payload) for e in events]}"
    # max_iter LLM calls in the tool loop + 1 summary-fallback attempt.
    assert llm.calls == 4, f"expected 4 LLM calls, got {llm.calls}"
    # Each iteration persisted its tool call.
    assert repo.record_tool_call.call_count == 3
    # 1 user + 3 interim assistants + 1 summary-apology = 5.
    assert repo.append_message.call_count == 5
    # Audit tag on the final message preserves the root cause for ops.
    final_call = repo.append_message.call_args_list[-1]
    assert final_call.kwargs["error"] == "tool_iterations_exceeded:apology"


def _make_tool_call_row(*, message_id: str, tool_name: str,
                        result: str) -> ToolCallRow:
    return ToolCallRow(
        id=f"tcr-{message_id}-{tool_name}",
        message_id=message_id,
        tool_name=tool_name,
        arguments={},
        result=result,
        status="ok",
        duration_ms=1,
        error=None,
        created_at=datetime.now(timezone.utc),
    )


def test_to_llm_messages_pairs_tool_use_with_tool_result():
    """Regression: follow-up turns previously sent tool_use blocks without
    their tool_result counterparts, which Anthropic rejects with HTTP 400.
    _to_llm_messages must emit a tool-role message per persisted tool call."""
    user_msg = _make_message("u1", "user", content="find Eric")
    asst_msg = _make_message(
        "a1", "assistant", content="looking up",
        tool_calls=[
            {"id": "toolu_1", "name": "list_assets", "arguments": {"search": "Eric"}},
            {"id": "toolu_2", "name": "get_asset", "arguments": {"asset_id": "x"}},
        ],
        seq=2,
    )
    final_msg = _make_message("a2", "assistant", content="Found it.", seq=3)
    tc_map = {
        "a1": [
            _make_tool_call_row(message_id="a1", tool_name="list_assets",
                                result="asset x"),
            _make_tool_call_row(message_id="a1", tool_name="get_asset",
                                result="asset detail"),
        ],
    }

    out = _to_llm_messages([user_msg, asst_msg, final_msg], tc_map)

    roles = [m.role for m in out]
    assert roles == ["user", "assistant", "tool", "tool", "assistant"]
    # The two tool-role messages must match the tool_use ids positionally.
    assert out[2].tool_call_id == "toolu_1"
    assert out[2].content == "asset x"
    assert out[3].tool_call_id == "toolu_2"
    assert out[3].content == "asset detail"


def test_to_llm_messages_emits_empty_tool_result_when_call_not_persisted():
    """Defensive: if a tool_use block was saved but the corresponding
    chat_tool_calls row is missing (e.g. server crashed mid-turn), emit an
    empty placeholder so the follow-up request doesn't 400."""
    asst_msg = _make_message(
        "a1", "assistant", content=None,
        tool_calls=[{"id": "toolu_1", "name": "list_assets", "arguments": {}}],
        seq=1,
    )

    out = _to_llm_messages([asst_msg], {})

    assert [m.role for m in out] == ["assistant", "tool"]
    assert out[1].tool_call_id == "toolu_1"
    assert out[1].content == ""


@pytest.mark.asyncio
async def test_stream_turn_aborts_after_consecutive_internal_errors():
    """When 3 tool calls in a row raise internal errors, the turn aborts
    with a tool_errors_exceeded event rather than looping to max_iter."""
    tool_call = {"id": "t1", "name": "broken", "arguments": {}}

    def _iter():
        return (
            [LLMChunk(kind="tool_call_start", tool_call_id="t1",
                      tool_name="broken"),
             LLMChunk(kind="tool_call_end", tool_call_id="t1",
                      tool_name="broken")],
            StreamResult(stop_reason="tool_use", tool_calls=[tool_call],
                         usage=Usage(input_tokens=1, output_tokens=1)),
        )

    llm = FakeLLM([_iter() for _ in range(10)])
    registry = ToolRegistry()

    async def _broken(**kwargs):
        raise RuntimeError("database unavailable")

    registry.register("broken", _broken, "",
                      {"type": "object", "properties": {}},
                      admin_required=False)
    svc = ChatService(db=MagicMock(), llm=llm, registry=registry,
                      max_tool_iterations=10)
    _wire_repo(svc)
    session = svc.create_session(user_id=USER_ID, title="t")

    events = []
    async for e in svc.stream_turn(session_id=session.id,
                                   user_text="do broken",
                                   user_role="user", user_id=USER_ID):
        events.append(e)

    # Aborted — no error frame (summary apology covers user-facing UX);
    # llm.calls = 3 iteration attempts that raised + 1 summary fallback.
    # max_iter was 10; without the abort the loop would have consumed all 10.
    assert not any(e.kind == "error" for e in events)
    assert llm.calls < 10
    assert llm.calls == 4


@pytest.mark.asyncio
async def test_stream_turn_abort_pairs_unprocessed_parallel_tool_uses():
    """Regression: when one assistant turn emits several PARALLEL tool calls
    and the turn aborts mid-batch (consecutive internal errors trip the break),
    the unprocessed tool_use blocks must still be paired with a tool_result
    before the summary LLM call. Otherwise the provider (Bedrock Converse /
    Anthropic) rejects the request with 'Expected toolResult blocks ... for the
    following Ids' for the tool_use ids that have no result."""
    ids = ["t1", "t2", "t3", "t4", "t5"]
    tool_calls = [{"id": i, "name": "broken", "arguments": {}} for i in ids]

    initial = (
        [], StreamResult(stop_reason="tool_use", tool_calls=tool_calls,
                         usage=Usage(input_tokens=1, output_tokens=1)),
    )
    summary = (
        [LLMChunk(kind="text", text="The tools failed repeatedly.")],
        StreamResult(stop_reason="end_turn",
                     final_text="The tools failed repeatedly; sorry.",
                     usage=Usage(input_tokens=1, output_tokens=1)),
    )

    class _CapturingLLM(FakeLLM):
        def __init__(self, scripted):
            super().__init__(scripted)
            self.message_snapshots: List[List[Any]] = []

        async def stream(self, **kwargs: Any) -> FakeStream:
            self.message_snapshots.append(list(kwargs.get("messages") or []))
            return await super().stream(**kwargs)

    llm = _CapturingLLM([initial, summary])
    registry = ToolRegistry()

    async def _broken(**kwargs):
        raise RuntimeError("database unavailable")

    registry.register("broken", _broken, "",
                      {"type": "object", "properties": {}},
                      admin_required=False)
    svc = ChatService(db=MagicMock(), llm=llm, registry=registry,
                      max_tool_iterations=10)
    _wire_repo(svc)
    session = svc.create_session(user_id=USER_ID, title="t")

    async for _ in svc.stream_turn(session_id=session.id,
                                   user_text="do broken across sources",
                                   user_role="user", user_id=USER_ID):
        pass

    # 2nd LLM call is the summary fallback after abort; its message history
    # must answer every tool_use id from the assistant turn.
    assert len(llm.message_snapshots) == 2
    summary_messages = llm.message_snapshots[1]
    tool_use_ids = {tc["id"]
                    for m in summary_messages if m.role == "assistant"
                    for tc in (m.tool_calls or [])}
    tool_result_ids = {m.tool_call_id
                       for m in summary_messages if m.role == "tool"}
    missing = tool_use_ids - tool_result_ids
    assert not missing, f"tool_use ids without a tool_result: {missing}"


@pytest.mark.asyncio
async def test_stream_turn_iteration_cap_recovers_with_summary():
    """When the tool loop hits max_iter, the next LLM call (with tools=[])
    should be used to synthesise a user-facing final answer. Success path:
    the summary produces text; the stream emits delta+message.end+done and
    persists the assistant message with ``error='tool_iterations_exceeded:
    recovered'`` for audit. No error frame goes to the client."""
    tool_call = {"id": "x", "name": "ping", "arguments": {}}

    def _stuck():
        return (
            [LLMChunk(kind="tool_call_start", tool_call_id="x",
                      tool_name="ping")],
            StreamResult(stop_reason="tool_use", tool_calls=[tool_call],
                         usage=Usage(input_tokens=1, output_tokens=1)),
        )

    summary = (
        [LLMChunk(kind="text", text="I couldn't finish but here's "),
         LLMChunk(kind="text", text="what I found.")],
        StreamResult(stop_reason="end_turn",
                     final_text="I couldn't finish but here's what I found.",
                     usage=Usage(input_tokens=3, output_tokens=9)),
    )

    # 2 iterations of tool loop then 1 summary call.
    llm = FakeLLM([_stuck(), _stuck(), summary])
    registry = ToolRegistry()

    async def _ping() -> str:
        return "pong"

    registry.register("ping", _ping, "", {"type": "object", "properties": {}},
                      admin_required=False)
    svc = ChatService(db=MagicMock(), llm=llm, registry=registry,
                      max_tool_iterations=2)
    repo = _wire_repo(svc)
    session = svc.create_session(user_id=USER_ID, title="t")

    events = []
    async for e in svc.stream_turn(session_id=session.id, user_text="hi",
                                   user_role="user", user_id=USER_ID):
        events.append(e)

    kinds = [e.kind for e in events]
    assert "error" not in kinds, (
        f"summary fallback should suppress the error frame: {kinds}"
    )
    assert "message.end" in kinds
    assert "done" in kinds
    # The delta text from the summary call reaches the user.
    delta_text = "".join(e.payload.get("text", "") for e in events
                          if e.kind == "delta")
    assert "here's what I found" in delta_text

    # Persisted summary message carries the audit-trail error marker so the
    # recovery stays visible in chat_messages for operators.
    summary_append_call = repo.append_message.call_args_list[-1]
    assert summary_append_call.kwargs.get("error") \
        == "tool_iterations_exceeded:recovered"


@pytest.mark.asyncio
async def test_stream_turn_summary_scrubs_leaked_tool_call_xml():
    """When a tool-calling-native model (Qwen3-coder, etc.) leaks
    <tool_call>…</tool_call> as raw text in the summary call, we strip
    the XML. If only the XML remained, the synthesised apology is sent
    to the user instead of raw markup."""
    tool_call = {"id": "x", "name": "ping", "arguments": {}}

    def _stuck():
        return (
            [LLMChunk(kind="tool_call_start", tool_call_id="x",
                      tool_name="ping")],
            StreamResult(stop_reason="tool_use", tool_calls=[tool_call],
                         usage=Usage(input_tokens=1, output_tokens=1)),
        )

    xml_only = (
        [LLMChunk(kind="text",
                   text='<tool_call>\n<function=list_assets>\n'
                        '<parameter=limit>10</parameter>\n</function>\n</tool_call>')],
        StreamResult(stop_reason="end_turn",
                     final_text='<tool_call>\n<function=list_assets>\n'
                                '<parameter=limit>10</parameter>\n</function>\n'
                                '</tool_call>',
                     usage=Usage(input_tokens=2, output_tokens=5)),
    )

    llm = FakeLLM([_stuck(), _stuck(), xml_only])
    registry = ToolRegistry()

    async def _ping() -> str:
        return "pong"

    registry.register("ping", _ping, "", {"type": "object", "properties": {}},
                      admin_required=False)
    svc = ChatService(db=MagicMock(), llm=llm, registry=registry,
                      max_tool_iterations=2)
    repo = _wire_repo(svc)
    session = svc.create_session(user_id=USER_ID, title="t")

    events = []
    async for e in svc.stream_turn(session_id=session.id, user_text="hi",
                                   user_role="user", user_id=USER_ID):
        events.append(e)

    deltas = [e.payload.get("text", "") for e in events if e.kind == "delta"]
    combined = "".join(deltas)
    # User never sees the raw XML
    assert "<tool_call>" not in combined
    assert "<function=" not in combined
    # But we do surface the apology so the bubble isn't blank
    assert "couldn't complete" in combined or "tool-use budget" in combined
    # Audit tag marks it as an apology (summary couldn't be salvaged)
    final_append = repo.append_message.call_args_list[-1]
    assert final_append.kwargs["error"] == "tool_iterations_exceeded:apology"


@pytest.mark.asyncio
async def test_stream_turn_frequency_advisory_after_three_same_tool_calls():
    """Same tool, different args three times → third result is prefixed
    with a frequency advisory so the model knows permuting isn't working."""
    def _call(arg_v: str):
        # Different args each time so the exact-repeat detector won't fire.
        return {"id": f"t_{arg_v}", "name": "ping", "arguments": {"q": arg_v}}

    def _round(arg_v: str):
        return (
            [LLMChunk(kind="tool_call_start", tool_call_id=f"t_{arg_v}",
                      tool_name="ping")],
            StreamResult(stop_reason="tool_use", tool_calls=[_call(arg_v)],
                         usage=Usage(input_tokens=1, output_tokens=1)),
        )

    final = (
        [LLMChunk(kind="text", text="ok")],
        StreamResult(stop_reason="end_turn", final_text="ok",
                     usage=Usage(input_tokens=1, output_tokens=1)),
    )

    llm = FakeLLM([_round("a"), _round("b"), _round("c"), final])
    registry = ToolRegistry()

    async def _ping(**_kw) -> str:
        return "pong"

    registry.register("ping", _ping, "", {"type": "object"},
                      admin_required=False)
    svc = ChatService(db=MagicMock(), llm=llm, registry=registry,
                      max_tool_iterations=5)
    repo = _wire_repo(svc)
    session = svc.create_session(user_id=USER_ID, title="t")

    async for _ in svc.stream_turn(session_id=session.id, user_text="x",
                                   user_role="user", user_id=USER_ID):
        pass

    results = [c.kwargs["result"]
               for c in repo.record_tool_call.call_args_list]
    assert "Call #" not in results[0]
    assert "Call #" not in results[1]
    assert results[2].startswith("[Call #3 to 'ping'")


@pytest.mark.asyncio
async def test_stream_turn_repeat_tool_call_gets_advisory_prefix():
    """Two identical calls to the same tool in one turn tag the second
    result with a [REPEAT ...] prefix so the model sees it's looping."""
    tool_call = {"id": "a", "name": "ping", "arguments": {"q": "same"}}

    def _round():
        return (
            [LLMChunk(kind="tool_call_start", tool_call_id="a",
                      tool_name="ping")],
            StreamResult(stop_reason="tool_use", tool_calls=[tool_call],
                         usage=Usage(input_tokens=1, output_tokens=1)),
        )

    final = (
        [LLMChunk(kind="text", text="done")],
        StreamResult(stop_reason="end_turn", final_text="done",
                     usage=Usage(input_tokens=1, output_tokens=1)),
    )

    llm = FakeLLM([_round(), _round(), final])
    registry = ToolRegistry()

    async def _ping(**_kw) -> str:
        return "pong"

    registry.register("ping", _ping, "", {"type": "object"},
                      admin_required=False)
    svc = ChatService(db=MagicMock(), llm=llm, registry=registry,
                      max_tool_iterations=5)
    repo = _wire_repo(svc)
    session = svc.create_session(user_id=USER_ID, title="t")

    async for _ in svc.stream_turn(session_id=session.id, user_text="x",
                                   user_role="user", user_id=USER_ID):
        pass

    # The tool was invoked twice (advisory, not aggressive: we still do the
    # real work so legitimate re-polls aren't masked).
    assert repo.record_tool_call.call_count == 2
    first_result = repo.record_tool_call.call_args_list[0].kwargs["result"]
    second_result = repo.record_tool_call.call_args_list[1].kwargs["result"]
    assert "REPEAT" not in first_result
    assert second_result.startswith("[REPEAT of iteration 1")
    assert "pong" in second_result


@pytest.mark.asyncio
async def test_stream_turn_error_counter_resets_on_success():
    """Two failures followed by a success should NOT abort — only unbroken
    error streaks matter."""
    tool_use_call = {"id": "tx", "name": "flaky", "arguments": {}}
    # Three iterations: fail, fail, success-then-end-turn.
    llm = FakeLLM([
        ([LLMChunk(kind="tool_call_start", tool_call_id="tx",
                   tool_name="flaky"),
          LLMChunk(kind="tool_call_end", tool_call_id="tx",
                   tool_name="flaky")],
         StreamResult(stop_reason="tool_use", tool_calls=[tool_use_call],
                      usage=Usage(input_tokens=1, output_tokens=1))),
        ([LLMChunk(kind="tool_call_start", tool_call_id="tx",
                   tool_name="flaky"),
          LLMChunk(kind="tool_call_end", tool_call_id="tx",
                   tool_name="flaky")],
         StreamResult(stop_reason="tool_use", tool_calls=[tool_use_call],
                      usage=Usage(input_tokens=1, output_tokens=1))),
        ([LLMChunk(kind="tool_call_start", tool_call_id="tx",
                   tool_name="flaky"),
          LLMChunk(kind="tool_call_end", tool_call_id="tx",
                   tool_name="flaky")],
         StreamResult(stop_reason="tool_use", tool_calls=[tool_use_call],
                      usage=Usage(input_tokens=1, output_tokens=1))),
        ([LLMChunk(kind="text", text="done")],
         StreamResult(stop_reason="end_turn", final_text="done",
                      usage=Usage(input_tokens=1, output_tokens=1))),
    ])
    registry = ToolRegistry()

    state = {"count": 0}

    async def _flaky(**kwargs):
        state["count"] += 1
        # fail, fail, succeed, (unreachable)
        if state["count"] <= 2:
            raise RuntimeError("temp glitch")
        return "ok payload"

    registry.register("flaky", _flaky, "",
                      {"type": "object", "properties": {}},
                      admin_required=False)
    svc = ChatService(db=MagicMock(), llm=llm, registry=registry,
                      max_tool_iterations=10)
    _wire_repo(svc)
    session = svc.create_session(user_id=USER_ID, title="t")

    events = []
    async for e in svc.stream_turn(session_id=session.id,
                                   user_text="go",
                                   user_role="user", user_id=USER_ID):
        events.append(e)

    kinds = [e.kind for e in events]
    # No error event; normal completion.
    assert "error" not in kinds
    assert "message.end" in kinds


# ---------- auto-title generation ----------

class TestCleanTitle:
    def test_strips_wrapping_quotes_and_trailing_period(self):
        assert _clean_title('"Find Eric Garza".') == "Find Eric Garza"

    def test_takes_first_non_empty_line(self):
        assert _clean_title(
            "\n\nSure! Here is the title:\nWorkstation Compliance Report",
        ) == "Sure! Here is the title"

    def test_caps_at_60_chars(self):
        long = "Word " * 30
        out = _clean_title(long)
        assert out is not None
        assert len(out) <= 60

    def test_returns_none_on_empty(self):
        assert _clean_title("") is None
        assert _clean_title("   \n  ") is None
        assert _clean_title('""') is None


@pytest.mark.asyncio
async def test_generate_title_returns_cleaned_text():
    llm = FakeLLM([
        ([LLMChunk(kind="text", text="Workstation Compliance Report.")],
         StreamResult(stop_reason="end_turn",
                      final_text="Workstation Compliance Report.",
                      usage=Usage(input_tokens=5, output_tokens=3))),
    ])
    svc = ChatService(db=MagicMock(), llm=llm, registry=ToolRegistry())
    title = await svc.generate_title("how many workstations are out of date?")
    assert title == "Workstation Compliance Report"


@pytest.mark.asyncio
async def test_generate_title_returns_none_on_empty_input():
    svc = ChatService(db=MagicMock(), llm=FakeLLM([]),
                      registry=ToolRegistry())
    assert await svc.generate_title("") is None
    assert await svc.generate_title("   ") is None


@pytest.mark.asyncio
async def test_generate_title_falls_back_to_streamed_chunks_when_final_text_empty():
    # Some providers leave result.final_text empty even though they streamed
    # text deltas (#735 P4); the title must come from the accumulated chunks.
    llm = FakeLLM([
        ([LLMChunk(kind="text", text="Stale Asset Report")],
         StreamResult(stop_reason="end_turn", final_text="",
                      usage=Usage(input_tokens=5, output_tokens=3))),
    ])
    svc = ChatService(db=MagicMock(), llm=llm, registry=ToolRegistry())
    title = await svc.generate_title("which assets are stale?")
    assert title == "Stale Asset Report"


@pytest.mark.asyncio
async def test_generate_title_falls_back_to_message_when_model_returns_nothing():
    # No text at all (no chunks, empty final_text) -> deterministic title from
    # the opening message so a new chat is never left on the default title.
    llm = FakeLLM([
        ([], StreamResult(stop_reason="end_turn", final_text="",
                          usage=Usage(input_tokens=5, output_tokens=0))),
    ])
    svc = ChatService(db=MagicMock(), llm=llm, registry=ToolRegistry())
    title = await svc.generate_title("how many workstations are out of date?")
    assert title == "how many workstations are out of date?"


def test_fallback_title_truncates_and_handles_blank():
    assert _fallback_title("   ") is None
    out = _fallback_title("word " * 40)
    assert out is not None and len(out) <= 60


def test_title_token_budget_accommodates_reasoning_models():
    # Reasoning models (Qwen3 etc.) burn output tokens on a <think> block before
    # the title; too small a budget cuts the call off mid-reasoning with empty
    # content, so every title falls back to the raw message (#735 P4). Keep
    # ample headroom — 200 was far too small (~544 tokens were needed on
    # qwen3.6-35b-a3b just for a one-line title).
    from src.api.blueprints.chat.service import _TITLE_MAX_OUTPUT_TOKENS
    assert _TITLE_MAX_OUTPUT_TOKENS >= 1024


@pytest.mark.asyncio
async def test_generate_title_propagates_provider_errors():
    """Service method is the pure async helper — the route is responsible for
    catching provider errors, matching the ``admin_test_config`` pattern."""
    class BoomLLM:
        provider = "fake"
        model = "fake-1"

        async def stream(self, **kw):
            raise RuntimeError("provider unavailable")

    svc = ChatService(db=MagicMock(), llm=BoomLLM(), registry=ToolRegistry())
    with pytest.raises(RuntimeError):
        await svc.generate_title("any question")


@pytest.mark.asyncio
async def test_generate_title_disables_thinking_for_openai_compat():
    # On qwen3-class reasoning models the hidden <think> block alone blows the
    # route's 15s budget, so the title call must opt out of thinking where the
    # provider supports the per-request switch (openai_compat / vLLM). The main
    # chat keeps reasoning.
    captured: dict = {}

    class _CapturingLLM(FakeLLM):
        provider = "openai_compat"

        async def stream(self, **kwargs: Any):
            captured.update(kwargs)
            return await super().stream(**kwargs)

    llm = _CapturingLLM([
        ([LLMChunk(kind="text", text="Asset Counts")],
         StreamResult(stop_reason="end_turn", final_text="Asset Counts",
                      usage=Usage(input_tokens=5, output_tokens=2))),
    ])
    svc = ChatService(db=MagicMock(), llm=llm, registry=ToolRegistry())
    title = await svc.generate_title("how many assets do I have?")
    assert title == "Asset Counts"
    assert captured.get("disable_thinking") is True


@pytest.mark.asyncio
async def test_generate_title_keeps_thinking_for_other_providers():
    # Providers without the per-request enable_thinking switch (anthropic,
    # bedrock, real openai, …) must not receive the flag at all.
    captured: dict = {}

    class _CapturingLLM(FakeLLM):
        provider = "anthropic"

        async def stream(self, **kwargs: Any):
            captured.update(kwargs)
            return await super().stream(**kwargs)

    llm = _CapturingLLM([
        ([LLMChunk(kind="text", text="Asset Counts")],
         StreamResult(stop_reason="end_turn", final_text="Asset Counts",
                      usage=Usage(input_tokens=5, output_tokens=2))),
    ])
    svc = ChatService(db=MagicMock(), llm=llm, registry=ToolRegistry())
    await svc.generate_title("how many assets do I have?")
    assert "disable_thinking" not in captured


@pytest.mark.asyncio
async def test_llm_error_logs_raw_detail_but_sends_safe_hint(caplog):
    """When result.stop_reason == 'error' and result.error is populated
    (e.g. FoundryResponsesClient on response.failed, Bedrock in-stream
    validationException), the raw provider detail must be logged server-side
    (never silently dropped) but must NOT reach the client SSE payload — the
    client gets a safe, model-agnostic hint instead.

    Regression for two things at once: the orchestrator once silently dropped
    result.error; and once the chat UI began rendering the SSE ``message``,
    echoing result.error there leaked provider internals to users (issue
    #695). The detail's home is the server log, not the client."""
    llm = FakeLLM([
        ([LLMChunk(kind="text", text="Partial")],
         StreamResult(stop_reason="error",
                      final_text="Partial",
                      error="upstream timeout",
                      usage=Usage(input_tokens=3, output_tokens=1))),
    ])
    registry = ToolRegistry()
    svc = ChatService(db=MagicMock(), llm=llm, registry=registry,
                      max_tool_iterations=5)
    _wire_repo(svc)
    session = svc.create_session(user_id=USER_ID, title="t")

    events = []
    with caplog.at_level(logging.WARNING):
        async for e in svc.stream_turn(session_id=session.id, user_text="hi",
                                       user_role="user", user_id=USER_ID):
            events.append(e)

    error_events = [e for e in events if e.kind == "error"]
    assert len(error_events) == 1, "expected exactly one error SSE event"
    msg = error_events[0].payload["message"]
    # Raw provider detail must NOT leak to the client...
    assert "upstream timeout" not in msg, f"raw detail leaked to client: {msg!r}"
    assert "stop_reason=error" not in msg, f"internal prefix leaked: {msg!r}"
    # ...the client gets an actionable, model-agnostic hint instead...
    assert "Settings" in msg, f"missing actionable hint in {msg!r}"
    # ...and the raw detail is preserved in the server log, not dropped.
    assert "upstream timeout" in caplog.text


@pytest.mark.asyncio
async def test_stream_turn_max_tokens_keeps_partial_answer_no_error_frame():
    """A reply cut off at the output-token cap (stop_reason='max_tokens') is a
    length limit, NOT a provider misconfiguration. It must NOT surface the
    generic 'check the chat provider configuration' error: the partial answer
    the model streamed is persisted (not discarded), the user gets a clear
    truncation notice, and the turn ends cleanly."""
    llm = FakeLLM([
        ([LLMChunk(kind="text", text="Here are the first few assets: A, B")],
         StreamResult(stop_reason="max_tokens",
                      final_text="Here are the first few assets: A, B",
                      usage=Usage(input_tokens=50, output_tokens=2000))),
    ])
    registry = ToolRegistry()
    svc = ChatService(db=MagicMock(), llm=llm, registry=registry,
                      max_tool_iterations=5)
    repo = _wire_repo(svc)
    session = svc.create_session(user_id=USER_ID, title="t")

    events = []
    async for e in svc.stream_turn(session_id=session.id,
                                   user_text="list everything",
                                   user_role="user", user_id=USER_ID):
        events.append(e)

    kinds = [e.kind for e in events]
    assert "error" not in kinds, \
        f"max_tokens must not surface a provider-config error frame: {kinds}"
    assert "message.end" in kinds
    assert "done" in kinds

    # The partial answer is preserved + a truncation notice appended.
    final_append = repo.append_message.call_args_list[-1]
    content = final_append.kwargs["content"]
    assert "Here are the first few assets" in content, "partial answer dropped"
    assert "cut off" in content.lower() or "length limit" in content.lower()
    # Audit marker records the cause for ops (so admins know to raise the cap).
    assert final_append.kwargs.get("error") == "max_tokens:truncated"

    # The user sees the truncation notice live (as a delta), not a red banner.
    delta_text = "".join(e.payload.get("text", "") for e in events
                         if e.kind == "delta")
    assert "cut off" in delta_text.lower() or "length limit" in delta_text.lower()


@pytest.mark.asyncio
async def test_stream_turn_max_tokens_with_no_text_still_notifies():
    """A reasoning model can burn the whole budget on its hidden <think> block
    and be cut off with zero visible text. The user must still get a clear
    truncation notice (not a blank bubble and not the provider-config error)."""
    llm = FakeLLM([
        ([],  # nothing streamed — reasoning consumed the budget
         StreamResult(stop_reason="max_tokens", final_text="",
                      usage=Usage(input_tokens=50, output_tokens=2000))),
    ])
    registry = ToolRegistry()
    svc = ChatService(db=MagicMock(), llm=llm, registry=registry,
                      max_tool_iterations=5)
    repo = _wire_repo(svc)
    session = svc.create_session(user_id=USER_ID, title="t")

    events = []
    async for e in svc.stream_turn(session_id=session.id, user_text="hi",
                                   user_role="user", user_id=USER_ID):
        events.append(e)

    assert "error" not in [e.kind for e in events]
    assert "message.end" in [e.kind for e in events]
    final_append = repo.append_message.call_args_list[-1]
    assert final_append.kwargs.get("error") == "max_tokens:truncated"
    # A non-empty notice is both persisted and streamed to the user.
    assert final_append.kwargs["content"].strip() != ""
    delta_text = "".join(e.payload.get("text", "") for e in events
                         if e.kind == "delta")
    assert delta_text.strip() != ""


@pytest.mark.asyncio
async def test_stream_turn_total_time_budget_aborts_slow_turn():
    """A turn whose events each arrive within the PER-EVENT timeout but whose
    TOTAL elapsed time exceeds the total budget must abort gracefully (a
    turn_timeout event), not run unbounded. Regression for the per-event-only
    timeout that let a long tool fan-out emit events steadily for minutes —
    overrunning the public gateway's request timeout (Cloudflare ~100s) and
    504-ing instead of self-aborting with a clean message."""

    class _SlowStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        def __aiter__(self):
            return self._iter()

        async def _iter(self):
            # Many small, steadily-spaced chunks: each gap is far below the
            # per-event timeout, so ONLY a total budget can stop the turn.
            for i in range(200):
                await asyncio.sleep(0.01)
                yield LLMChunk(kind="text", text=f"{i} ")

        @property
        def result(self):
            return StreamResult(stop_reason="end_turn", final_text="done",
                                usage=Usage(input_tokens=1, output_tokens=1))

    class _SlowLLM:
        provider = "fake"
        model = "fake-1"

        async def stream(self, **kw):
            return _SlowStream()

    svc = ChatService(
        db=MagicMock(), llm=_SlowLLM(), registry=ToolRegistry(),
        max_tool_iterations=3,
        turn_timeout_seconds=5,          # per-event: never fires (0.01s gaps)
        turn_total_timeout_seconds=0.1,  # total: fires after ~10 chunks
    )
    _wire_repo(svc)
    session = svc.create_session(user_id=USER_ID, title="t")

    events = []
    async for e in svc.stream_turn(session_id=session.id, user_text="hi",
                                   user_role="user", user_id=USER_ID):
        events.append(e)

    # Aborted via the TOTAL budget (per-event never had a >5s gap), gracefully.
    assert any(e.kind == "error" and e.payload.get("code") == "turn_timeout"
               for e in events), \
        f"expected a turn_timeout abort: {[(e.kind, e.payload) for e in events]}"
    assert "done" in [e.kind for e in events]
    # Cut short well before consuming all 200 chunks.
    assert sum(1 for e in events if e.kind == "delta") < 200


class _FakeProvidersService:
    """Stand-in for ChatProvidersService that returns canned fingerprints
    and effective configs. Used to verify cross-worker config drift detection."""

    def __init__(self, fingerprint, effective_config):
        self._fingerprint = fingerprint
        self._effective_config = effective_config

    def set(self, fingerprint, effective_config):
        self._fingerprint = fingerprint
        self._effective_config = effective_config

    def get_active_config_fingerprint(self):
        return self._fingerprint

    def get_effective_config(self, _provider):
        return self._effective_config


@pytest.mark.asyncio
async def test_stream_turn_rebuilds_llm_when_db_config_changes(monkeypatch):
    """A worker whose in-memory LLM was built at startup must pick up
    config changes another worker (or external write) applied to the DB.

    Why this matters: the admin PATCH /chat/admin/config route rebuilds
    the LLM client in-process, but gunicorn spawns N workers each with
    its own ChatService. Without DB-driven staleness detection, only the
    PATCH-handling worker uses the new provider; other workers keep
    streaming through the old (potentially broken) client until restart.
    """
    initial_llm = FakeLLM([
        ([LLMChunk(kind="text", text="from-initial")],
         StreamResult(stop_reason="end_turn", final_text="from-initial",
                      usage=Usage(input_tokens=1, output_tokens=1))),
    ])
    new_llm = FakeLLM([
        ([LLMChunk(kind="text", text="from-new")],
         StreamResult(stop_reason="end_turn", final_text="from-new",
                      usage=Usage(input_tokens=1, output_tokens=1))),
    ])

    providers = _FakeProvidersService(
        fingerprint=("anthropic", "2026-01-01T00:00:00+00:00"),
        effective_config={"CHAT_LLM_PROVIDER": "anthropic"},
    )

    svc = ChatService(db=MagicMock(), llm=initial_llm,
                      registry=ToolRegistry(),
                      providers_service=providers,
                      max_tool_iterations=5)
    _wire_repo(svc)

    # First turn: fingerprint matches startup value → no rebuild.
    session = svc.create_session(user_id=USER_ID, title="t")
    async for _ in svc.stream_turn(session_id=session.id, user_text="one",
                                   user_role="user", user_id=USER_ID):
        pass
    assert svc.llm is initial_llm
    assert initial_llm.calls == 1

    # Admin (on another worker) flips the active provider. The DB now
    # advertises a different fingerprint; make_llm_client must be invoked
    # to produce the new client.
    providers.set(
        fingerprint=("openai", "2026-01-02T00:00:00+00:00"),
        effective_config={"CHAT_LLM_PROVIDER": "openai"},
    )
    monkeypatch.setattr(
        "src.api.blueprints.chat.llm.make_llm_client",
        lambda _cfg: new_llm,
    )

    async for _ in svc.stream_turn(session_id=session.id, user_text="two",
                                   user_role="user", user_id=USER_ID):
        pass

    assert svc.llm is new_llm, (
        "stream_turn must rebuild the LLM client when the DB fingerprint "
        "changes — otherwise non-PATCH gunicorn workers keep using the "
        "stale client and chat requests fail until container restart."
    )
    assert new_llm.calls == 1


@pytest.mark.asyncio
async def test_stream_turn_recovers_when_rebuild_starts_failing_then_works(monkeypatch):
    """When ``make_llm_client`` raises mid-turn (admin cleared the API
    key or similar), the worker keeps streaming on its previous client
    rather than 500-ing. Crucially, the moment the admin fixes the
    config, the very next chat turn picks it up — no manual intervention
    needed.

    This drives the recovery behaviorally rather than poking at the
    internal fingerprint cache, so it tests the user-visible guarantee.
    """
    initial_llm = FakeLLM([
        ([LLMChunk(kind="text", text="ok-1")],
         StreamResult(stop_reason="end_turn", final_text="ok-1",
                      usage=Usage(input_tokens=1, output_tokens=1))),
        ([LLMChunk(kind="text", text="ok-2")],
         StreamResult(stop_reason="end_turn", final_text="ok-2",
                      usage=Usage(input_tokens=1, output_tokens=1))),
    ])
    fixed_llm = FakeLLM([
        ([LLMChunk(kind="text", text="fixed")],
         StreamResult(stop_reason="end_turn", final_text="fixed",
                      usage=Usage(input_tokens=1, output_tokens=1))),
    ])

    providers = _FakeProvidersService(
        fingerprint=("anthropic", "2026-01-01T00:00:00+00:00"),
        effective_config={"CHAT_LLM_PROVIDER": "anthropic"},
    )

    svc = ChatService(db=MagicMock(), llm=initial_llm,
                      registry=ToolRegistry(),
                      providers_service=providers,
                      max_tool_iterations=5)
    _wire_repo(svc)
    session = svc.create_session(user_id=USER_ID, title="t")

    # Admin edits config but the save was broken (no api key, etc.) —
    # make_llm_client raises.
    providers.set(
        fingerprint=("openai", "2026-01-02T00:00:00+00:00"),
        effective_config={"CHAT_LLM_PROVIDER": "openai"},
    )
    monkeypatch.setattr(
        "src.api.blueprints.chat.llm.make_llm_client",
        MagicMock(side_effect=ValueError("no api key")),
    )
    async for _ in svc.stream_turn(session_id=session.id, user_text="t1",
                                   user_role="user", user_id=USER_ID):
        pass
    # Old client still in use → turn succeeds, doesn't surface a build
    # error to the user.
    assert svc.llm is initial_llm

    # Admin fixes the config — next make_llm_client succeeds.
    providers.set(
        fingerprint=("openai", "2026-01-03T00:00:00+00:00"),
        effective_config={"CHAT_LLM_PROVIDER": "openai"},
    )
    monkeypatch.setattr(
        "src.api.blueprints.chat.llm.make_llm_client",
        lambda _cfg: fixed_llm,
    )
    async for _ in svc.stream_turn(session_id=session.id, user_text="t2",
                                   user_role="user", user_id=USER_ID):
        pass
    # Recovery: the worker picks up the fix on its next turn. If the
    # failed attempt had wrongly advanced the cached fingerprint, this
    # turn would have skipped the rebuild and svc.llm would still be
    # initial_llm.
    assert svc.llm is fixed_llm
