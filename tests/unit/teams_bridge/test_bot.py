# SPDX-License-Identifier: AGPL-3.0-or-later
"""TeamsBridgeBot unit tests — linking flow and chat relay."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from src.teams_bridge.bot import TeamsBridgeBot
from src.teams_bridge.state import JwtCache


def _make_turn_context(*, aad: str = "aad-1", tenant: str = "tnt-1",
                      name: str = "Alice", text: str = "hi"):
    """Build a TurnContext-shaped MagicMock with the fields the bot reads."""
    tc = MagicMock()
    tc.activity = MagicMock()
    tc.activity.from_property = MagicMock(aad_object_id=aad, name=name)
    tc.activity.conversation = MagicMock(tenant_id=tenant, id="conv-1")
    tc.activity.text = text
    tc.activity.channel_data = {}
    sent_resp = MagicMock()
    sent_resp.id = "activity-1"
    tc.send_activity = AsyncMock(return_value=sent_resp)
    tc.update_activity = AsyncMock()
    return tc


async def test_unlinked_user_gets_link_card():
    client = MagicMock()
    client.exchange = AsyncMock(return_value=None)
    client.initiate_link = AsyncMock(return_value={
        "link_url": "https://aa.example/teams/link?token=abc",
        "expires_at": "2026-01-01T00:00:00Z",
    })
    bot = TeamsBridgeBot(client=client, jwt_cache=JwtCache())
    tc = _make_turn_context()

    await bot.on_message_activity(tc)

    client.exchange.assert_called_once()
    client.initiate_link.assert_called_once()
    sent = tc.send_activity.call_args[0][0]
    assert sent.attachments
    assert (sent.attachments[0].content_type ==
            "application/vnd.microsoft.card.adaptive")
    assert sent.attachments[0].content["actions"][0]["url"] == \
        "https://aa.example/teams/link?token=abc"


async def test_linked_user_gets_chat_reply_via_cache():
    client = MagicMock()
    client.get_conversation_session = AsyncMock(return_value={
        "session_id": "sess-1", "user_id": "user-1",
        "last_active_at": "2026-01-01T00:00:00Z",
    })

    async def _fake_stream(**_):
        for ev in [
            {"event": "message.start", "data": {"message_id": "m1"}},
            {"event": "delta", "data": {"text": "Hello "}},
            {"event": "delta", "data": {"text": "world"}},
            {"event": "message.end", "data": {"message_id": "m1"}},
            {"event": "done", "data": {}},
        ]:
            yield ev
    client.stream_chat = _fake_stream

    cache = JwtCache(skew_seconds=0)
    cache.set("aad-1", "tnt-1", "cached-jwt", "user-1", expires_in=300)

    bot = TeamsBridgeBot(client=client, jwt_cache=cache)
    tc = _make_turn_context()

    await bot.on_message_activity(tc)

    # No /exchange call when cache hit
    client.exchange.assert_not_called()
    # send_activity called twice: a typing indicator followed by the
    # final reply. update_activity is NOT used (Teams 1:1 doesn't
    # reliably apply text edits; see bot.py:_stream_turn for context).
    assert tc.send_activity.call_count == 2
    tc.update_activity.assert_not_called()

    typing_call = tc.send_activity.call_args_list[0][0][0]
    assert getattr(typing_call, "type", None) == "typing"

    final_text = tc.send_activity.call_args_list[-1][0][0]
    assert "Hello world" in final_text


async def test_linked_user_caches_jwt_after_exchange():
    client = MagicMock()
    client.exchange = AsyncMock(return_value={
        "access_token": "fresh-jwt", "expires_in": 300,
        "user_id": "user-1",
    })
    # Stub the rest of the relay so we don't follow the chat path
    client.get_conversation_session = AsyncMock(return_value={
        "session_id": "sess-1", "user_id": "user-1",
        "last_active_at": "2026-01-01T00:00:00Z",
    })

    async def _empty_stream(**_):
        for ev in [
            {"event": "message.start", "data": {"message_id": "m1"}},
            {"event": "message.end", "data": {"message_id": "m1"}},
            {"event": "done", "data": {}},
        ]:
            yield ev
    client.stream_chat = _empty_stream

    cache = JwtCache(skew_seconds=0)
    bot = TeamsBridgeBot(client=client, jwt_cache=cache)
    await bot.on_message_activity(_make_turn_context())

    assert cache.get("aad-1", "tnt-1") == "fresh-jwt"
    assert cache.get_user_id("aad-1", "tnt-1") == "user-1"


async def test_user_without_aad_gets_friendly_error():
    client = MagicMock()
    bot = TeamsBridgeBot(client=client, jwt_cache=JwtCache())
    tc = _make_turn_context(aad="")
    tc.activity.conversation = MagicMock(tenant_id="", id="conv-x")
    tc.activity.channel_data = {}

    await bot.on_message_activity(tc)

    client.exchange.assert_not_called()
    sent = tc.send_activity.call_args[0][0]
    assert isinstance(sent, str)
    assert "tenant" in sent.lower()


async def test_new_command_resets_session():
    client = MagicMock()
    client.delete_conversation_session = AsyncMock()
    cache = JwtCache(skew_seconds=0)
    cache.set("aad-1", "tnt-1", "cached-jwt", "user-1", expires_in=300)
    bot = TeamsBridgeBot(client=client, jwt_cache=cache)

    tc = _make_turn_context(text="/new")
    await bot.on_message_activity(tc)

    client.delete_conversation_session.assert_called_once_with("conv-1")
    sent = tc.send_activity.call_args[0][0]
    assert "new chat" in sent.lower()


async def test_chat_reply_with_artifact_emits_chart_card():
    client = MagicMock()
    client.get_conversation_session = AsyncMock(return_value={
        "session_id": "sess-1", "user_id": "user-1",
        "last_active_at": "2026-01-01T00:00:00Z",
    })

    async def _fake_stream(**_):
        for ev in [
            {"event": "message.start", "data": {"message_id": "m1"}},
            {"event": "delta", "data": {"text": "Found "}},
            {"event": "tool.end", "data": {
                "artifact": {
                    "type": "donut", "title": "OS",
                    "series": [{"label": "Windows", "value": 12}],
                },
            }},
            {"event": "delta", "data": {"text": "12 hosts."}},
            {"event": "message.end", "data": {"message_id": "m1"}},
            {"event": "done", "data": {}},
        ]:
            yield ev
    client.stream_chat = _fake_stream

    cache = JwtCache(skew_seconds=0)
    cache.set("aad-1", "tnt-1", "cached-jwt", "user-1", expires_in=300)
    bot = TeamsBridgeBot(client=client, jwt_cache=cache)
    tc = _make_turn_context()

    await bot.on_message_activity(tc)

    sends = tc.send_activity.call_args_list
    # First call is the typing indicator, then the final text reply,
    # then the chart card. (No feedback card in v1.)
    assert getattr(sends[0][0][0], "type", None) == "typing"
    assert "Found" in sends[1][0][0]
    chart_activity = None
    for call in sends:
        msg = call[0][0]
        if hasattr(msg, "attachments") and msg.attachments:
            content = msg.attachments[0].content
            if any(b.get("type") == "Chart.Donut"
                   for b in content.get("body", [])):
                chart_activity = msg
                break
    assert chart_activity is not None, \
        f"no chart card was sent; sends were: {sends}"


async def test_feedback_submit_calls_send_feedback():
    client = MagicMock()
    client.send_feedback = AsyncMock()
    cache = JwtCache(skew_seconds=0)
    cache.set("aad-1", "tnt-1", "cached-jwt", "user-1", expires_in=300)
    bot = TeamsBridgeBot(client=client, jwt_cache=cache)

    tc = _make_turn_context(text="")  # empty text + value populated
    tc.activity.value = {
        "action": "feedback", "message_id": "m1", "rating": 1,
    }

    await bot.on_message_activity(tc)

    client.send_feedback.assert_called_once_with(
        access_token="cached-jwt",
        message_id="m1",
        rating=1,
    )
    # No streaming activity sent
    tc.send_activity.assert_not_called()


async def test_feedback_submit_invalid_rating_ignored():
    client = MagicMock()
    client.send_feedback = AsyncMock()
    cache = JwtCache(skew_seconds=0)
    cache.set("aad-1", "tnt-1", "cached-jwt", "user-1", expires_in=300)
    bot = TeamsBridgeBot(client=client, jwt_cache=cache)

    tc = _make_turn_context(text="")
    tc.activity.value = {
        "action": "feedback", "message_id": "m1", "rating": 99,
    }

    await bot.on_message_activity(tc)
    client.send_feedback.assert_not_called()


async def test_chat_relay_error_responds_friendly():
    client = MagicMock()
    client.get_conversation_session = AsyncMock(return_value={
        "session_id": "sess-1", "user_id": "user-1",
        "last_active_at": "2026-01-01T00:00:00Z",
    })

    async def _broken_stream(**_):
        yield {"event": "message.start", "data": {"message_id": "m1"}}
        yield {"event": "error", "data": {"message": "stream_failed"}}
    client.stream_chat = _broken_stream

    cache = JwtCache(skew_seconds=0)
    cache.set("aad-1", "tnt-1", "cached-jwt", "user-1", expires_in=300)
    bot = TeamsBridgeBot(client=client, jwt_cache=cache)
    tc = _make_turn_context()

    await bot.on_message_activity(tc)

    # Error path: typing indicator + a single send_activity carrying
    # the error text. update_activity isn't used.
    sends = tc.send_activity.call_args_list
    assert getattr(sends[0][0][0], "type", None) == "typing"
    final = sends[-1][0][0]
    assert "error" in final.lower()
    assert "stream_failed" in final.lower()
    tc.update_activity.assert_not_called()
