# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bot Framework activity handler for the AssetAtlas Teams bridge.

For each user message:
  1. Resolve the user's AssetAtlas JWT (cached or via /exchange).
     If unlinked, send the linking Adaptive Card.
  2. Detect /new and natural variants → drop the conversation→session
     mapping and reply with an acknowledgement.
  3. Otherwise: ensure a chat session exists for this conversation,
     drive /api/chat/stream via plan_stream_actions, post an initial
     placeholder, update it in-place as deltas arrive, then attach a
     feedback card.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from botbuilder.core import ActivityHandler, MessageFactory, TurnContext
from botbuilder.schema import Activity, ActivityTypes, Attachment

from src.teams_bridge.artifacts import build_artifact_card
from src.teams_bridge.commands import Command, parse_command
from src.teams_bridge.linking import build_link_card
from src.teams_bridge.relay import plan_stream_actions
from src.teams_bridge.state import AssetAtlasClient, JwtCache

logger = logging.getLogger(__name__)


class TeamsBridgeBot(ActivityHandler):

    def __init__(
        self, *, client: AssetAtlasClient, jwt_cache: JwtCache,
    ):
        self._client = client
        self._jwt = jwt_cache

    async def on_message_activity(
        self, turn_context: TurnContext,
    ) -> None:
        activity = turn_context.activity

        v = activity.value or {}
        if (activity.text or "").strip() == "" and \
                v.get("action") == "feedback":
            await self._handle_feedback_submit(turn_context, v)
            return

        aad = activity.from_property.aad_object_id or ""
        tnt = _tenant_id(activity)
        display_name = activity.from_property.name or "Teams user"
        conv_id = activity.conversation.id
        text = (activity.text or "").strip()

        if not aad or not tnt:
            await turn_context.send_activity(
                "I can only chat with users in a Teams tenant — sorry.",
            )
            return

        token, user_id = await self._resolve_jwt(aad, tnt)
        if token is None:
            await self._send_link_card(
                turn_context, aad, tnt, display_name,
            )
            return

        if parse_command(text) is Command.NEW:
            await self._client.delete_conversation_session(conv_id)
            await turn_context.send_activity(
                "Started a new chat. What can I help with?",
            )
            return

        try:
            session_id = await self._resolve_session(
                conv_id=conv_id, user_id=user_id, token=token,
            )
            message_id = await self._stream_turn(
                turn_context=turn_context, token=token,
                session_id=session_id, message=text,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Chat relay failed: %s", exc)
            await turn_context.send_activity(
                "Sorry, I couldn't reach AssetAtlas. Please try again.",
            )
            return

        # Feedback card (👍/👎 → /api/chat/feedback) deferred from v1:
        # Teams' renderer applies the "card unsupported" fallback to
        # Adaptive Cards with Action.Submit unless they carry the
        # Teams-specific msteams.type=messageBack routing field. The
        # feedback handler itself (`_handle_feedback_submit`) is still
        # wired so we can re-enable by sending the card again once we
        # add the msteams.type extension to build_feedback_card().

    # ---- helpers ----

    async def _resolve_jwt(
        self, aad: str, tnt: str,
    ) -> Tuple[Optional[str], Optional[str]]:
        token = self._jwt.get(aad, tnt)
        if token is not None:
            return token, self._jwt.get_user_id(aad, tnt)
        res = await self._client.exchange(
            aad_object_id=aad, tenant_id=tnt,
        )
        if res is None:
            return None, None
        self._jwt.set(
            aad, tnt, res["access_token"], res["user_id"],
            expires_in=res["expires_in"],
        )
        return res["access_token"], res["user_id"]

    async def _resolve_session(
        self, *, conv_id: str, user_id: str, token: str,
    ) -> str:
        existing = await self._client.get_conversation_session(conv_id)
        if existing is not None:
            return existing["session_id"]
        session_id = await self._client.create_chat_session(
            access_token=token, title="Microsoft Teams chat",
        )
        await self._client.set_conversation_session(
            conversation_id=conv_id,
            user_id=user_id,
            session_id=session_id,
        )
        return session_id

    async def _stream_turn(
        self, *, turn_context: TurnContext, token: str, session_id: str,
        message: str,
    ) -> Optional[str]:
        """Drive /api/chat/stream → post the LLM reply to Teams.

        Returns the assistant message_id on success (so the caller can
        attach a feedback card), or None on error.

        Teams' update_activity does not reliably apply text edits in 1:1
        chats — the placeholder bubble persists and updates stack up as
        separate messages. Microsoft's actual Teams streaming API uses a
        different `streaminfo` entity protocol that's a bigger rewrite,
        deferred from v1. For now we send a typing indicator while the
        LLM works, then post a single send_activity for the final text.
        plan_stream_actions still drives the artifact emission unchanged.
        """
        await turn_context.send_activity(Activity(type=ActivityTypes.typing))

        events = self._client.stream_chat(
            access_token=token, session_id=session_id, message=message,
        )

        message_id: Optional[str] = None
        # emit_streaming=False: skip the post_streaming/update_streaming
        # path entirely, since Teams 1:1 won't reliably apply the edits.
        async for action in plan_stream_actions(
            events, throttle_ms=750, emit_streaming=False,
        ):
            if action.kind == "post_final":
                if action.text:
                    await turn_context.send_activity(action.text)
                message_id = action.message_id
            elif action.kind == "post_card" and action.artifact:
                card = build_artifact_card(action.artifact)
                title = action.artifact.get("title") or "Result"
                await turn_context.send_activity(MessageFactory.attachment(
                    Attachment(
                        content_type="application/vnd.microsoft.card.adaptive",
                        content=card,
                    ),
                    text=title,
                ))
        return message_id

    async def _handle_feedback_submit(
        self, turn_context: TurnContext, value: dict,
    ) -> None:
        message_id = value.get("message_id")
        rating = int(value.get("rating", 0))
        if not message_id or rating not in (-1, 1):
            return
        activity = turn_context.activity
        aad = activity.from_property.aad_object_id or ""
        tnt = _tenant_id(activity)
        if not aad or not tnt:
            return
        token, _ = await self._resolve_jwt(aad, tnt)
        if token is None:
            return
        try:
            await self._client.send_feedback(
                access_token=token,
                message_id=message_id,
                rating=rating,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Feedback send failed: %s", exc)

    async def _send_link_card(
        self, turn_context: TurnContext,
        aad: str, tnt: str, display_name: str,
    ) -> None:
        init = await self._client.initiate_link(
            aad_object_id=aad, tenant_id=tnt, display_name=display_name,
        )
        card = build_link_card(
            link_url=init["link_url"], display_name=display_name,
        )
        # Short text= keeps Teams from showing the "card unsupported"
        # placeholder when it can't render the attachment, without
        # cluttering the message for clients that DO render the card.
        await turn_context.send_activity(MessageFactory.attachment(
            Attachment(
                content_type="application/vnd.microsoft.card.adaptive",
                content=card,
            ),
            text="Link your AssetAtlas account",
        ))


def _tenant_id(activity) -> str:
    """Extract the Entra tenant id from a Bot Framework Activity.

    `conversation.tenant_id` is set by Teams in most channels; the
    `channel_data.tenant.id` fallback covers older channel-data-only
    payloads still seen in some Teams clients.
    """
    return (
        activity.conversation.tenant_id
        or (activity.channel_data or {}).get("tenant", {}).get("id")
        or ""
    )
