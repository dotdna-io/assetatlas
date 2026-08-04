# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for chat blueprint Pydantic models.

Tests CreateSessionRequest, StreamTurnRequest, FeedbackRequest,
RenameSessionRequest, and shared record models.
"""

import pytest
from pydantic import ValidationError

from src.api.blueprints.chat.models import (
    CreateSessionRequest,
    StreamTurnRequest,
    FeedbackRequest,
    RenameSessionRequest,
    ToolCallRecord,
    ChatMessage,
    ChatSession,
)


def test_stream_turn_request_requires_session_and_message():
    r = StreamTurnRequest(session_id="abc", message="hi")
    assert r.session_id == "abc"
    with pytest.raises(ValidationError):
        StreamTurnRequest(session_id="", message="hi")
    with pytest.raises(ValidationError):
        StreamTurnRequest(session_id="abc", message="")


def test_stream_turn_request_message_length_capped():
    with pytest.raises(ValidationError):
        StreamTurnRequest(session_id="abc", message="x" * 20001)


def test_feedback_rating_must_be_plusminus_one():
    assert FeedbackRequest(message_id="m", rating=1).rating == 1
    assert FeedbackRequest(message_id="m", rating=-1).rating == -1
    with pytest.raises(ValidationError):
        FeedbackRequest(message_id="m", rating=0)


def test_create_session_defaults_title_to_untitled():
    r = CreateSessionRequest()
    assert r.title == "Untitled conversation"


def test_rename_session_requires_non_empty_title():
    with pytest.raises(ValidationError):
        RenameSessionRequest(title="")
    r = RenameSessionRequest(title="New name")
    assert r.title == "New name"
