# SPDX-License-Identifier: AGPL-3.0-or-later
"""JwtCache + SSE parser unit tests. AssetAtlasClient itself is
exercised end-to-end in later phases."""
from __future__ import annotations

import asyncio
import time

import pytest

from src.teams_bridge.state import JwtCache, _parse_sse


class _FakeReader:
    """Minimal aiohttp.StreamReader stand-in that returns the supplied
    lines from readline() and an empty bytes object on EOF."""

    def __init__(self, payload: bytes):
        self._buf = payload

    async def readline(self) -> bytes:
        if not self._buf:
            return b""
        idx = self._buf.find(b"\n")
        if idx == -1:
            line, self._buf = self._buf, b""
            return line
        line = self._buf[: idx + 1]
        self._buf = self._buf[idx + 1 :]
        return line


def test_jwt_cache_hit():
    c = JwtCache(skew_seconds=0)
    c.set("aad-1", "tnt-1", "tok", "user-1", expires_in=60)
    assert c.get("aad-1", "tnt-1") == "tok"


def test_jwt_cache_miss_when_expired():
    c = JwtCache(skew_seconds=0)
    c.set("aad-1", "tnt-1", "tok", "user-1", expires_in=0)
    time.sleep(0.01)
    assert c.get("aad-1", "tnt-1") is None


def test_jwt_cache_skew_subtracts_from_ttl():
    c = JwtCache(skew_seconds=60)
    c.set("aad-1", "tnt-1", "tok", "user-1", expires_in=30)
    # expires_in=30 minus skew=60 → already considered expired
    assert c.get("aad-1", "tnt-1") is None


def test_jwt_cache_invalidate():
    c = JwtCache(skew_seconds=0)
    c.set("aad-1", "tnt-1", "tok", "user-1", expires_in=60)
    c.invalidate("aad-1", "tnt-1")
    assert c.get("aad-1", "tnt-1") is None


def test_jwt_cache_keys_isolate_tenants():
    c = JwtCache(skew_seconds=0)
    c.set("aad-1", "tnt-1", "tok-a", "user-a", expires_in=60)
    c.set("aad-1", "tnt-2", "tok-b", "user-b", expires_in=60)
    assert c.get("aad-1", "tnt-1") == "tok-a"
    assert c.get("aad-1", "tnt-2") == "tok-b"


def test_jwt_cache_get_user_id():
    c = JwtCache(skew_seconds=0)
    c.set("aad-1", "tnt-1", "tok", "user-42", expires_in=60)
    assert c.get_user_id("aad-1", "tnt-1") == "user-42"
    assert c.get_user_id("aad-1", "tnt-2") is None


@pytest.mark.asyncio
async def test_parse_sse_yields_typed_events():
    body = (
        b"event: message.start\n"
        b"data: {\"message_id\": \"m1\"}\n"
        b"\n"
        b"event: delta\n"
        b"data: {\"text\": \"hello \"}\n"
        b"\n"
        b"event: delta\n"
        b"data: {\"text\": \"world\"}\n"
        b"\n"
        b"event: done\n"
        b"data: {}\n"
        b"\n"
    )
    events = []
    async for ev in _parse_sse(_FakeReader(body)):
        events.append(ev)
    assert [e["event"] for e in events] == [
        "message.start", "delta", "delta", "done",
    ]
    assert events[0]["data"] == {"message_id": "m1"}
    assert events[1]["data"] == {"text": "hello "}


@pytest.mark.asyncio
async def test_parse_sse_handles_multiline_data():
    body = (
        b"event: chunk\n"
        b"data: line1\n"
        b"data: line2\n"
        b"\n"
    )
    events = [ev async for ev in _parse_sse(_FakeReader(body))]
    assert len(events) == 1
    # data lines joined with \n; non-JSON falls through to {"raw": ...}
    assert events[0]["data"] == {"raw": "line1\nline2"}


@pytest.mark.asyncio
async def test_parse_sse_yields_nothing_on_empty_body():
    events = [ev async for ev in _parse_sse(_FakeReader(b""))]
    assert events == []
