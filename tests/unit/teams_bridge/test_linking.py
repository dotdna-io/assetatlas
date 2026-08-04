# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adaptive Card builder tests — pure JSON, no SDK dependency."""
from __future__ import annotations

from src.teams_bridge.linking import build_feedback_card, build_link_card


def test_link_card_has_open_url_action():
    card = build_link_card(
        link_url="https://aa.example/teams/link?token=abc",
        display_name="Alice",
    )
    assert card["type"] == "AdaptiveCard"
    actions = card["actions"]
    assert len(actions) == 1
    assert actions[0]["type"] == "Action.OpenUrl"
    assert actions[0]["url"] == "https://aa.example/teams/link?token=abc"
    # display_name should appear in the body somewhere
    body_text = " ".join(b.get("text", "") for b in card["body"])
    assert "Alice" in body_text


def test_feedback_card_has_two_buttons_carrying_message_id():
    card = build_feedback_card(message_id="msg-1")
    actions = card["actions"]
    assert len(actions) == 2
    titles = {a["title"] for a in actions}
    assert "👍 Helpful" in titles
    assert "👎 Not" in titles
    for a in actions:
        assert a["type"] == "Action.Submit"
        assert a["data"]["message_id"] == "msg-1"
        assert a["data"]["action"] == "feedback"
        assert a["data"]["rating"] in (-1, 1)
