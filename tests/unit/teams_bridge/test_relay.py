# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the SSE → StreamAction async planner."""
from __future__ import annotations

from typing import AsyncIterator, Iterable

from src.teams_bridge.relay import StreamAction, plan_stream_actions


async def _aiter(events: Iterable[dict]) -> AsyncIterator[dict]:
    for ev in events:
        yield ev


def _ev(kind, **data):
    return {"event": kind, "data": data}


def _clock(ticks):
    """Iterate through `ticks` per call. No silent fallback — running
    out raises StopIteration, surfacing test-authoring mistakes loudly."""
    it = iter(ticks)
    return lambda: next(it)


async def _collect(events, **kwargs):
    return [a async for a in plan_stream_actions(_aiter(events), **kwargs)]


async def test_simple_stream_emits_post_then_final():
    events = [
        _ev("message.start", message_id="m1"),
        _ev("delta", text="Hello "),
        _ev("delta", text="world"),
        _ev("message.end", message_id="m1"),
        _ev("done"),
    ]
    actions = await _collect(
        events, throttle_ms=0,
        now=_clock([0, 100, 200, 300, 400, 500]),
    )
    kinds = [a.kind for a in actions]
    assert kinds[0] == "post_streaming"
    assert "update_streaming" in kinds
    assert kinds[-1] == "post_final"
    final = next(a for a in actions if a.kind == "post_final")
    assert final.text == "Hello world"
    assert final.message_id == "m1"


async def test_throttle_coalesces_deltas():
    events = [
        _ev("message.start", message_id="m1"),
        _ev("delta", text="a"),
        _ev("delta", text="b"),
        _ev("delta", text="c"),
        _ev("message.end", message_id="m1"),
        _ev("done"),
    ]
    actions = await _collect(
        events, throttle_ms=99999, now=_clock([0]*10),
    )
    streaming = [a for a in actions if a.kind == "update_streaming"]
    assert len(streaming) == 0
    final = next(a for a in actions if a.kind == "post_final")
    assert final.text == "abc"


async def test_tool_call_uses_name_key():
    """Chat emits tool.start payload with `name` (not `tool_name`).
    The bridge must read `name` so the tool annotation is populated."""
    events = [
        _ev("message.start", message_id="m1"),
        _ev("delta", text="Looking up... "),
        _ev("tool.start", name="get_assets"),
        _ev("tool.end", name="get_assets"),
        _ev("delta", text="found 3."),
        _ev("message.end", message_id="m1"),
        _ev("done"),
    ]
    actions = await _collect(
        events, throttle_ms=0,
        now=_clock([0, 100, 200, 300, 400, 500, 600, 700]),
    )
    final = next(a for a in actions if a.kind == "post_final")
    assert "Looking up..." in final.text
    assert "Calling tool: get_assets" in final.text
    assert "found 3." in final.text


async def test_tool_start_falls_back_to_question_mark_when_no_name():
    events = [
        _ev("message.start", message_id="m1"),
        _ev("tool.start"),
        _ev("message.end", message_id="m1"),
        _ev("done"),
    ]
    actions = await _collect(events, throttle_ms=0, now=_clock([0]*4))
    final = next(a for a in actions if a.kind == "post_final")
    assert "Calling tool: ?" in final.text


async def test_error_short_circuits():
    events = [
        _ev("message.start", message_id="m1"),
        _ev("delta", text="working… "),
        _ev("error", message="stream_failed"),
    ]
    actions = await _collect(
        events, throttle_ms=0, now=_clock([0, 100, 200, 300]),
    )
    kinds = [a.kind for a in actions]
    assert kinds[0] == "post_streaming"
    assert kinds[-1] == "post_final"
    final = actions[-1]
    assert "Error" in final.text
    assert "stream_failed" in final.text
    assert final.message_id is None  # suppresses feedback card


async def test_message_id_carried_through():
    events = [
        _ev("message.start", message_id="m1"),
        _ev("message.end", message_id="m1"),
        _ev("done"),
    ]
    actions = await _collect(events, throttle_ms=0, now=_clock([0]*4))
    final = actions[-1]
    assert final.kind == "post_final"
    assert final.message_id == "m1"


async def test_artifact_emitted_as_post_card_after_final():
    events = [
        _ev("message.start", message_id="m1"),
        _ev("delta", text="hi"),
        _ev("tool.end",
            artifact={"type": "donut", "title": "x", "series": []}),
        _ev("message.end", message_id="m1"),
        _ev("done"),
    ]
    actions = await _collect(events, throttle_ms=0, now=_clock([0]*8))
    kinds = [a.kind for a in actions]
    final_idx = kinds.index("post_final")
    card_idx = kinds.index("post_card")
    assert final_idx < card_idx
    assert actions[card_idx].artifact["type"] == "donut"


async def test_multiple_artifacts_all_emitted_after_final():
    events = [
        _ev("message.start", message_id="m1"),
        _ev("tool.end",
            artifact={"type": "donut", "title": "a", "series": []}),
        _ev("tool.end",
            artifact={"type": "table", "title": "b",
                      "columns": [], "series": []}),
        _ev("message.end", message_id="m1"),
        _ev("done"),
    ]
    actions = await _collect(events, throttle_ms=0, now=_clock([0]*8))
    cards = [a for a in actions if a.kind == "post_card"]
    assert len(cards) == 2
    assert cards[0].artifact["type"] == "donut"
    assert cards[1].artifact["type"] == "table"


async def test_error_does_not_emit_buffered_artifacts():
    events = [
        _ev("message.start", message_id="m1"),
        _ev("tool.end",
            artifact={"type": "donut", "title": "x", "series": []}),
        _ev("error", message="boom"),
    ]
    actions = await _collect(events, throttle_ms=0, now=_clock([0]*4))
    assert not any(a.kind == "post_card" for a in actions)


def test_stream_action_is_a_dataclass():
    """Sanity: the dataclass shape didn't drift."""
    a = StreamAction("post_final", text="hi", message_id="m1")
    assert a.kind == "post_final"
    assert a.text == "hi"
    assert a.message_id == "m1"
    assert a.artifact is None
