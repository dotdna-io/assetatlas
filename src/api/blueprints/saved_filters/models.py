# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pydantic request models for the saved_filters blueprint."""
from __future__ import annotations

import json
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# General-access level; mirrors the saved_filters.visibility CHECK constraint
# (migration 113). Declaring it as a Literal lets Pydantic reject unknown
# values natively (same idiom as chat/models.py MessageRole). Phase 1 only
# enforces 'org' as admin-only; 'link' is accepted for later sharing phases.
Visibility = Literal["private", "link", "org"]
MAX_PAYLOAD_BYTES = 65536  # 64 KB cap on the opaque view snapshot


def _validate_payload(v: Any) -> Dict[str, Any]:
    if not isinstance(v, dict):
        raise ValueError("payload must be a JSON object")
    try:
        # allow_nan=False rejects NaN/Infinity, which psycopg2 would otherwise
        # serialise as invalid JSON for the jsonb column.
        encoded = json.dumps(v, allow_nan=False)
    except ValueError as exc:
        raise ValueError(f"payload is not valid JSON: {exc}") from exc
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")
    return v


class SavedFilterCreate(BaseModel):
    """Body for POST /api/saved-filters."""
    # str_strip_whitespace trims name/description so a whitespace-only name is
    # rejected by min_length and " Servers "/"Servers" can't both slip past the
    # case-insensitive unique index as distinct rows.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    payload: Dict[str, Any] = Field(description="Opaque Explorer view snapshot")
    visibility: Visibility = "private"

    @field_validator("payload")
    @classmethod
    def _payload(cls, v: Any) -> Dict[str, Any]:
        return _validate_payload(v)


class SavedFilterUpdate(BaseModel):
    """Body for PUT /api/saved-filters/<id>. All fields optional (partial update)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    payload: Optional[Dict[str, Any]] = None
    visibility: Optional[Visibility] = None

    @field_validator("payload")
    @classmethod
    def _payload(cls, v: Any) -> Any:
        return v if v is None else _validate_payload(v)
