# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

from typing import List, Optional

from src.modules.merge_denylist.encoding import (
    to_canonical_component,
    to_canonical_range,
)
from src.modules.merge_denylist.models import DenylistRow
from src.modules.merge_denylist.repository import MergeDenylistRepository


def _to_dict(row: DenylistRow) -> dict:
    return {
        "id": row.id,
        "identifier_type": row.identifier_type,
        "match_type": row.match_type,
        "identifier_value": row.identifier_value,
        "raw_value": row.raw_value,
        "reason": row.reason,
        "created_by": row.created_by,
        "enabled": row.enabled,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class MergeDenylistService:
    def __init__(self, repo: MergeDenylistRepository):
        self.repo = repo

    def list_entries(self) -> List[dict]:
        return [_to_dict(r) for r in self.repo.list_all()]

    def add_entry(
        self,
        *,
        identifier_type: str,
        raw_value: Optional[str],
        reason: Optional[str],
        created_by: str,
        match_type: str = "exact",
        range_start: Optional[str] = None,
        range_end: Optional[str] = None,
        idempotent: bool = False,
    ) -> Optional[dict]:
        # Raises ValueError on unparseable value -> handler maps to 400.
        if match_type == "range":
            # A range is two bounds, not one value: identifier_value is their
            # canonical '<start>-<end>' spelling, and raw_value shows the admin
            # both of the things they typed.
            identifier_value, start, end = to_canonical_range(
                identifier_type, range_start, range_end
            )
            display = f"{range_start.strip()} - {range_end.strip()}"
        else:
            identifier_value = to_canonical_component(
                identifier_type, raw_value, match_type
            )
            start = end = None
            display = raw_value.strip()
        if idempotent:
            # Used by callers already inside a request transaction scope (e.g.
            # asset split), where a caught IntegrityError would still poison
            # the scope. Returns None if the entry already existed.
            row = self.repo.create_idempotent(
                identifier_type=identifier_type,
                match_type=match_type,
                identifier_value=identifier_value,
                raw_value=display,
                reason=reason,
                created_by=created_by,
                range_start=start,
                range_end=end,
            )
            return _to_dict(row) if row else None
        row = self.repo.create(
            identifier_type=identifier_type,
            match_type=match_type,
            identifier_value=identifier_value,
            raw_value=display,
            reason=reason,
            created_by=created_by,
            range_start=start,
            range_end=end,
        )
        return _to_dict(row)

    def remove_entry(self, entry_id: str) -> bool:
        return self.repo.delete(entry_id)
