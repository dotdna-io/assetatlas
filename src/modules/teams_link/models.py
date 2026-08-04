# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pydantic models for Teams bridge link/exchange flows."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class LinkInitiateRequest(BaseModel):
    """Bridge → API: 'I'm seeing a Teams user, give me a link URL.'"""
    aad_object_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)


class LinkInitiateResponse(BaseModel):
    link_url: str
    expires_at: datetime


class LinkConfirmRequest(BaseModel):
    """User-browser → API: 'Confirm linking this token to the signed-in user.'"""
    token: str = Field(min_length=1)


class ExchangeRequest(BaseModel):
    """Bridge → API: 'Mint a JWT for the user behind this Teams identity.'"""
    aad_object_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)


class ExchangeResponse(BaseModel):
    access_token: str
    expires_in: int
    user_id: str


class TeamsAccountLink(BaseModel):
    """Persisted row from teams_account_links."""
    id: UUID
    aad_object_id: str
    tenant_id: str
    user_id: UUID
    linked_at: datetime
    linked_via_ip: Optional[str] = None


class TeamsConversationSession(BaseModel):
    """Persisted row from teams_conversation_sessions."""
    id: UUID
    conversation_id: str
    user_id: UUID
    session_id: UUID
    created_at: datetime
    last_active_at: datetime
