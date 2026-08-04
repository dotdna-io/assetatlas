# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.modules.merge_denylist.encoding import check_type_and_match

IdentifierType = Literal["hostname", "fqdn", "mac", "serial"]
MatchType = Literal["exact", "prefix", "suffix", "range"]


class DenylistEntryCreate(BaseModel):
    """Body for POST /api/merge-denylist.

    exact/prefix/suffix entries carry one raw_value ('web01', 'web*',
    '*.corp.local', 'bc:24:11:*'); a range carries range_start + range_end
    instead, because a MAC's own '-' separator makes a single 'start-end' string
    ambiguous. Which fields go with which match_type is enforced here so a
    malformed body is a 400 rather than a CHECK violation surfacing as a 500.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    identifier_type: IdentifierType
    match_type: MatchType = "exact"
    raw_value: Optional[str] = Field(default=None, min_length=1, max_length=255)
    range_start: Optional[str] = Field(default=None, min_length=1, max_length=255)
    range_end: Optional[str] = Field(default=None, min_length=1, max_length=255)
    reason: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check_entry_shape(self) -> "DenylistEntryCreate":
        # The pair rule itself lives in the encoder, so the API rejects with the
        # same message the encoder would rather than a second copy of it.
        check_type_and_match(self.identifier_type, self.match_type)
        if self.match_type == "range":
            if not (self.range_start and self.range_end):
                raise ValueError("a range entry needs range_start and range_end")
            if self.raw_value is not None:
                raise ValueError(
                    "a range entry takes range_start/range_end, not raw_value"
                )
        else:
            if self.raw_value is None:
                raise ValueError(f"a {self.match_type} entry needs raw_value")
            if self.range_start is not None or self.range_end is not None:
                raise ValueError(
                    f"a {self.match_type} entry takes raw_value, "
                    f"not range_start/range_end"
                )
        return self


@dataclass
class DenylistRow:
    id: str
    identifier_type: str
    match_type: str
    identifier_value: str
    raw_value: str
    reason: Optional[str]
    created_by: str
    enabled: bool
    created_at: datetime
