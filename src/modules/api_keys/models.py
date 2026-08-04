# SPDX-License-Identifier: AGPL-3.0-or-later
# src/modules/api_keys/models.py
"""Pydantic models for API key management."""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator


class ApiKeyCreate(BaseModel):
    """Request model for creating an API key."""
    name: str = Field(..., min_length=1, max_length=100, description="Human-readable label")
    expires_at: datetime = Field(..., description="Expiration timestamp (must be in the future)")

    @field_validator('expires_at')
    @classmethod
    def expires_must_be_future(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError('expires_at must be timezone-aware')
        from datetime import timezone
        if v <= datetime.now(timezone.utc):
            raise ValueError('expires_at must be in the future')
        return v


class ApiKeyUpdate(BaseModel):
    """Request model for updating an API key."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    is_active: Optional[bool] = None


class ApiKeyResponse(BaseModel):
    """Response model for an API key (never includes the key itself)."""
    id: str
    user_id: str
    name: str
    key_prefix: str
    expires_at: datetime
    is_active: bool
    last_used_at: Optional[datetime] = None
    last_used_ip: Optional[str] = None
    created_at: datetime
    created_by: Optional[str] = None  # username, populated by service


class ApiKeyCreatedResponse(BaseModel):
    """Response after creating a key — includes the plaintext key (shown once)."""
    id: str
    name: str
    key: str = Field(..., description="Full API key — shown only at creation time")
    key_prefix: str
    expires_at: datetime
    created_at: datetime


class ApiKeyListResponse(BaseModel):
    """Response for listing API keys."""
    api_keys: List[ApiKeyResponse]
    total: int
