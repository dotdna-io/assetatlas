# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Chat Blueprint Pydantic Models

Request/response models for chat session, streaming turn, and feedback
endpoints, plus shared records for messages, sessions, and tool calls.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


MessageRole = Literal["user", "assistant", "tool", "system"]
ToolCallStatus = Literal["ok", "error", "denied", "timeout"]

# Default title stamped on freshly-created sessions; the auto-title route only
# overwrites this exact value so a manually-renamed session is never clobbered
# by the LLM-generated title.
DEFAULT_SESSION_TITLE = "Untitled conversation"


# =============================================================================
# Request Models
# =============================================================================

class CreateSessionRequest(BaseModel):
    """Request model for creating a new chat session."""
    title: str = Field(default=DEFAULT_SESSION_TITLE, max_length=200)


class StreamTurnRequest(BaseModel):
    """Request model for streaming a user turn in a chat session."""
    session_id: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=20000)


class FeedbackRequest(BaseModel):
    """Request model for submitting feedback on a chat message."""
    message_id: str = Field(min_length=1)
    rating: int = Field(description="Thumbs rating: -1 (down) or 1 (up)")
    comment: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("rating")
    @classmethod
    def _rating_pm1(cls, v: int) -> int:
        if v not in (-1, 1):
            raise ValueError("rating must be -1 or 1")
        return v


class RenameSessionRequest(BaseModel):
    """Request model for renaming an existing chat session."""
    title: str = Field(min_length=1, max_length=200)


class AutoTitleRequest(BaseModel):
    """Request model for auto-generating a chat session title from the user's
    opening message. Matches StreamTurnRequest's message cap so the two
    endpoints accept the same text."""
    user_text: str = Field(min_length=1, max_length=20000)


# =============================================================================
# Shared Record Models
# =============================================================================

class ToolCallRecord(BaseModel):
    """Record of a tool invocation performed during a chat turn."""
    id: str
    tool_name: str
    arguments: Dict[str, Any]
    result: Optional[str] = None
    status: ToolCallStatus
    duration_ms: Optional[int] = None
    error: Optional[str] = None


class ChatMessage(BaseModel):
    """Persisted chat message record."""
    id: str
    session_id: str
    seq: int
    role: MessageRole
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    error: Optional[str] = None
    created_at: datetime


class ChatSession(BaseModel):
    """Persisted chat session record."""
    id: str
    user_id: str
    title: str
    model: str
    provider: str
    created_at: datetime
    updated_at: datetime
