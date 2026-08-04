# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the chat /stream SSE endpoint."""


def _wire_streaming_chat_service(blueprint_app, events):
    """Make blueprint_app.chat_service.stream_turn yield the given events."""

    async def _gen(*args, **kwargs):
        for e in events:
            yield e

    blueprint_app.chat_service.stream_turn = _gen
    return blueprint_app


def _allow_rate_limit(blueprint_app, *, per_day=100):
    blueprint_app.chat_rate_limiter.check_and_incr.return_value = (
        True, 1, per_day - 1)
    blueprint_app.chat_rate_limiter.acquire_concurrency_lock.return_value = True
    blueprint_app.chat_rate_limiter.release_concurrency_lock.return_value = None
    # Route reads svc.turn_timeout to bound t.join(); MagicMock would TypeError
    # under float() — pin a real numeric value.
    blueprint_app.chat_service.turn_timeout = 1.0


def _wire_raising_chat_service(blueprint_app, exc):
    """Make blueprint_app.chat_service.stream_turn raise ``exc`` when iterated,
    so the route's stream-error handler runs against a realistic failure."""

    async def _gen(*args, **kwargs):
        raise exc
        yield  # pragma: no cover - presence makes this an async generator

    blueprint_app.chat_service.stream_turn = _gen
    return blueprint_app


# ---- Auth gate ----

class TestStreamAuth:
    def test_no_auth_returns_401(self, client):
        resp = client.post("/api/chat/stream",
                           json={"session_id": "s", "message": "hi"})
        assert resp.status_code == 401


# ---- Rate limiting ----

class TestStreamRateLimits:
    def test_429_when_daily_cap_exceeded(self, client, authed_headers,
                                         blueprint_app):
        blueprint_app.chat_rate_limiter.check_and_incr.return_value = (
            False, 201, 0)
        resp = client.post(
            "/api/chat/stream",
            json={"session_id": "s", "message": "hi"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 429
        body = resp.get_data(as_text=True)
        assert "rate_limited" in body

    def test_429_when_concurrent_turn_locked(self, client, authed_headers,
                                             blueprint_app):
        blueprint_app.chat_rate_limiter.check_and_incr.return_value = (
            True, 1, 99)
        blueprint_app.chat_rate_limiter.acquire_concurrency_lock.return_value = False
        resp = client.post(
            "/api/chat/stream",
            json={"session_id": "s", "message": "hi"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 429
        assert "concurrent_turn_in_progress" in resp.get_data(as_text=True)


# ---- Happy path ----

class TestStreamHappy:
    def test_emits_message_start_delta_end_done(self, client, authed_headers,
                                                blueprint_app):
        from src.api.blueprints.chat.service import StreamEvent
        _allow_rate_limit(blueprint_app)
        _wire_streaming_chat_service(blueprint_app, [
            StreamEvent(kind="message.start",
                        payload={"message_id": "m1", "role": "user"}),
            StreamEvent(kind="delta", payload={"text": "hi"}),
            StreamEvent(kind="message.end",
                        payload={"message_id": "m2", "role": "assistant"}),
            StreamEvent(kind="done"),
        ])
        resp = client.post(
            "/api/chat/stream",
            json={"session_id": "s-1", "message": "hi"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"
        body = resp.get_data(as_text=True)
        # No error frame on happy path
        assert "event: error" not in body
        # Frames appear in the expected order
        i_start = body.index("event: message.start")
        i_delta = body.index("event: delta")
        i_end = body.index("event: message.end")
        i_done = body.index("event: done")
        assert i_start < i_delta < i_end < i_done

    def test_releases_lock_after_stream_finishes(self, client, authed_headers,
                                                 blueprint_app):
        from src.api.blueprints.chat.service import StreamEvent
        _allow_rate_limit(blueprint_app)
        _wire_streaming_chat_service(blueprint_app, [
            StreamEvent(kind="done"),
        ])
        client.post(
            "/api/chat/stream",
            json={"session_id": "s-1", "message": "hi"},
            headers=authed_headers("user"),
        )
        rl = blueprint_app.chat_rate_limiter
        rl.release_concurrency_lock.assert_called_once_with("test-user-id")


# ---- Stream-error messaging (issue #695, Problem 2) ----

class TestStreamErrorMessaging:
    """A failed turn must reach the UI as an actionable, model-agnostic hint.
    The raw provider exception is logged server-side only — never sent to the
    client payload."""

    def test_tool_use_validation_surfaces_actionable_hint(
        self, client, authed_headers, blueprint_app,
    ):
        _allow_rate_limit(blueprint_app)
        _wire_raising_chat_service(blueprint_app, Exception(
            "An error occurred (ValidationException) when calling the "
            "ConverseStream operation: This model doesn't support tool use "
            "in streaming mode."
        ))
        resp = client.post(
            "/api/chat/stream",
            json={"session_id": "s-1", "message": "hi"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "event: error" in body
        # Stable code preserved for telemetry / callers.
        assert "stream_failed" in body
        # Actionable, model-agnostic guidance reaches the UI.
        assert "tool-capable model" in body
        assert "Settings" in body
        # Raw provider internals must NOT leak to the client payload.
        assert "ConverseStream" not in body

    def test_unknown_error_surfaces_generic_hint_without_raw_detail(
        self, client, authed_headers, blueprint_app,
    ):
        _allow_rate_limit(blueprint_app)
        _wire_raising_chat_service(blueprint_app,
                                   Exception("boom-secret-internal-detail"))
        resp = client.post(
            "/api/chat/stream",
            json={"session_id": "s-1", "message": "hi"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "stream_failed" in body
        # Raw exception text stays in the server log, not the client payload.
        assert "boom-secret-internal-detail" not in body
        # A generic but actionable hint is still present.
        assert "Settings" in body


# ---- Client disconnect cancels the in-flight turn ----

class TestStreamClientCancel:
    """A client that disconnects mid-turn (Stop button / closed tab) must
    actually cancel the in-flight LLM/tool work — not let it finish in the
    background and persist an answer nobody is waiting for."""

    def test_disconnect_cancels_inflight_turn_and_releases_lock(
        self, client, authed_headers, blueprint_app,
    ):
        # `client` is requested only for its side effect: it resets the
        # session-scoped service mocks between tests (see the client fixture).
        # Without it, release_concurrency_lock calls leak in from prior tests.
        import asyncio
        import threading
        from src.api.blueprints.chat.routes import stream_turn_route
        from src.api.blueprints.chat.service import StreamEvent

        _allow_rate_limit(blueprint_app)
        # Bounds the join after disconnect; the turn never actually completes.
        blueprint_app.chat_service.turn_timeout = 2.0

        observed = {"cancelled": False}
        first_frame_sent = threading.Event()

        async def _gen(*args, **kwargs):
            yield StreamEvent(kind="message.start",
                              payload={"message_id": "m", "role": "user"})
            first_frame_sent.set()
            try:
                # A long-running turn the client gives up on. Cancellation
                # raises CancelledError here instead of running to completion.
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                observed["cancelled"] = True
                raise
            yield StreamEvent(kind="done")  # pragma: no cover

        blueprint_app.chat_service.stream_turn = _gen

        # The Flask test client buffers the whole response (draining the turn
        # to completion), so it can't model a mid-stream disconnect. Invoke the
        # view inside a request context, pull one SSE frame off the response
        # generator, then close() it — exactly what a real client abort does to
        # the streaming response on the server side.
        with blueprint_app.test_request_context(
            "/api/chat/stream", method="POST",
            json={"session_id": "s", "message": "hi"},
            headers=authed_headers("user"),
        ):
            resp = stream_turn_route()
            gen = resp.response
            iterator = iter(gen)

            first = next(iterator)
            assert "message.start" in (
                first.decode() if isinstance(first, bytes) else first
            )
            assert first_frame_sent.wait(2.0)

            # Simulate the browser aborting the fetch / closing the tab.
            gen.close()

        # The worker observed cancellation (didn't sleep to completion) and the
        # concurrency lock was released so the next turn can start immediately.
        assert observed["cancelled"] is True
        blueprint_app.chat_rate_limiter.release_concurrency_lock \
            .assert_called_once_with("test-user-id")


# ---- P1 (#735): request scope released before SSE drain ----

class TestStreamScopeRelease:
    """The stream generator must call end_scope() before draining the worker
    queue so the request's idle DB connection is returned to the pool before
    teardown.

    Test infrastructure note: conftest._fake_init_database registers the
    before_request/teardown_request hooks against a local mock_cm, then
    the blueprint_app fixture overwrites app.connection_manager with a fresh
    MagicMock.  So teardown fires against the old mock — not against
    blueprint_app.connection_manager (cm).  That makes the assertion clean:
    the ONLY way cm.end_scope is called is via the explicit call inside
    _iter_sync.  Without the fix count == 0; with the fix count == 1.
    """

    def test_end_scope_called_inside_generator(
        self, client, authed_headers, blueprint_app
    ):
        from src.api.blueprints.chat.service import StreamEvent
        _allow_rate_limit(blueprint_app)
        _wire_streaming_chat_service(blueprint_app, [
            StreamEvent(kind="message.start", payload={}),
            StreamEvent(kind="done"),
        ])
        cm = blueprint_app.connection_manager
        cm.end_scope.reset_mock()

        resp = client.post(
            "/api/chat/stream",
            json={"session_id": "s-scope", "message": "hi"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        assert "message.start" in resp.get_data(as_text=True)

        # The teardown hook fires against the original mock_cm from
        # _fake_init_database (captured in its closure), not against cm.
        # Therefore cm.end_scope can only be incremented by the explicit
        # call inside _iter_sync — the P1 fix.
        # Without the fix: count == 0.  With the fix: count == 1.
        assert cm.end_scope.call_count == 1, (
            f"end_scope called {cm.end_scope.call_count} time(s); "
            "expected 1 (from inside _iter_sync — the P1 fix is missing)"
        )
