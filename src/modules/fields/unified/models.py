# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pydantic request models for the Unified Fields API. The write body is
exactly {name, function, sources} — data_type/encoded_key are server-derived."""

from typing import List
from pydantic import BaseModel, Field, field_validator

from src.modules.fields.unified.definition import VALID_FUNCTIONS


def _check_function(v: str) -> str:
    if v not in VALID_FUNCTIONS:
        raise ValueError(f"function must be one of: {', '.join(VALID_FUNCTIONS)}")
    return v


class UnifiedFieldWrite(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    function: str
    sources: List[str] = Field(..., min_length=2)

    @field_validator("function")
    @classmethod
    def _valid_function(cls, v: str) -> str:
        return _check_function(v)


class UnifiedFieldPreviewRequest(BaseModel):
    function: str
    sources: List[str] = Field(..., min_length=2)
    sample_size: int = Field(default=10, ge=1, le=100)

    @field_validator("function")
    @classmethod
    def _valid_function(cls, v: str) -> str:
        return _check_function(v)
