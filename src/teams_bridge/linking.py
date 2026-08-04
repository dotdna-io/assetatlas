# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adaptive Card payload builders. Pure JSON; no SDK dependencies."""

from __future__ import annotations

from typing import Any, Dict

from src.teams_bridge._cards import adaptive_card, text_block


def build_link_card(*, link_url: str, display_name: str) -> Dict[str, Any]:
    """Card sent on first message from an unlinked Teams user."""
    return adaptive_card(
        body=[
            text_block("Link your AssetAtlas account",
                       size="Medium", weight="Bolder"),
            text_block(
                f"Hi **{display_name}** — to start chatting with "
                f"AssetAtlas in Teams, click below to confirm linking "
                f"your accounts. Once linked, send your message again.",
            ),
        ],
        actions=[
            {"type": "Action.OpenUrl",
             "title": "Link my AssetAtlas account",
             "url": link_url},
        ],
    )


def build_feedback_card(*, message_id: str) -> Dict[str, Any]:
    """Thumbs-up / thumbs-down card sent after each assistant turn."""
    return adaptive_card(
        body=[
            text_block("Was this answer helpful?", isSubtle=True),
        ],
        actions=[
            {"type": "Action.Submit",
             "title": "👍 Helpful",
             "data": {"action": "feedback", "rating": 1,
                      "message_id": message_id}},
            {"type": "Action.Submit",
             "title": "👎 Not",
             "data": {"action": "feedback", "rating": -1,
                      "message_id": message_id}},
        ],
    )
