# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

from typing import List

from pydantic import BaseModel, ConfigDict, Field


class SplitRequest(BaseModel):
    """Body for POST /api/assets/<id>/split."""

    model_config = ConfigDict(extra="forbid")
    bridge: List[str] = Field(min_length=1)
    primary_cluster: List[str] = Field(min_length=1)
    clusters: List[List[str]] = Field(min_length=2)
