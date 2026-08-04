# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

from typing import List, Optional

import psycopg2

from src.api.middleware.error_handler import ConflictError
from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.merge_denylist.matcher import DenylistMatcher
from src.modules.merge_denylist.models import DenylistRow

_COLS = (
    "id, identifier_type, match_type, identifier_value, raw_value, reason, "
    "created_by, enabled, created_at"
)


def _row(r) -> DenylistRow:
    return DenylistRow(
        id=str(r["id"]),
        identifier_type=r["identifier_type"],
        match_type=r["match_type"],
        identifier_value=r["identifier_value"],
        raw_value=r["raw_value"],
        reason=r["reason"],
        created_by=r["created_by"],
        enabled=r["enabled"],
        created_at=r["created_at"],
    )


class MergeDenylistRepository:
    def __init__(self, db: PostgreSQLAdapter):
        self.db = db

    def list_all(self) -> List[DenylistRow]:
        sql = f"SELECT {_COLS} FROM merge_denylist ORDER BY created_at DESC"
        rows = self.db.execute_query(sql) or []
        return [_row(r) for r in rows]

    def create(
        self,
        *,
        identifier_type: str,
        identifier_value: str,
        raw_value: str,
        reason: Optional[str],
        created_by: str,
        match_type: str = "exact",
        range_start: Optional[int] = None,
        range_end: Optional[int] = None,
    ) -> DenylistRow:
        sql = f"""
            INSERT INTO merge_denylist
                (identifier_type, match_type, identifier_value, raw_value,
                 reason, created_by, range_start, range_end)
            VALUES
                (%(itype)s, %(mtype)s, %(ivalue)s, %(raw)s,
                 %(reason)s, %(by)s, %(rstart)s, %(rend)s)
            RETURNING {_COLS}
        """
        try:
            rows = self.db.execute_query(
                sql,
                {
                    "itype": identifier_type,
                    "mtype": match_type,
                    "ivalue": identifier_value,
                    "raw": raw_value,
                    "reason": reason,
                    "by": created_by,
                    "rstart": range_start,
                    "rend": range_end,
                },
            )
        except psycopg2.IntegrityError as exc:
            # Only the (identifier_type, identifier_value, match_type) unique
            # constraint maps to a 409; re-raise any other integrity violation
            # (CHECK, NOT NULL) rather than mislabel it as a duplicate.
            if getattr(exc, "pgcode", None) == "23505":  # unique_violation
                raise ConflictError(
                    f"'{raw_value}' is already on the denylist"
                ) from exc
            raise
        return _row(rows[0])

    def create_idempotent(
        self,
        *,
        identifier_type: str,
        identifier_value: str,
        raw_value: str,
        reason: Optional[str],
        created_by: str,
        match_type: str = "exact",
        range_start: Optional[int] = None,
        range_end: Optional[int] = None,
    ) -> Optional[DenylistRow]:
        """Insert, or no-op if this entry already exists.

        Uses ON CONFLICT DO NOTHING so a duplicate never raises — critical when
        called inside a request transaction scope, where a caught IntegrityError
        would still poison the scope and roll back the whole request at teardown.
        The conflict target names the columns of merge_denylist_unique so
        Postgres can infer it; if the two ever drift, every call raises.
        Returns the new DenylistRow, or None if it already existed.
        """
        sql = f"""
            INSERT INTO merge_denylist
                (identifier_type, match_type, identifier_value, raw_value,
                 reason, created_by, range_start, range_end)
            VALUES
                (%(itype)s, %(mtype)s, %(ivalue)s, %(raw)s,
                 %(reason)s, %(by)s, %(rstart)s, %(rend)s)
            ON CONFLICT (identifier_type, identifier_value, match_type) DO NOTHING
            RETURNING {_COLS}
        """
        rows = self.db.execute_query(
            sql,
            {
                "itype": identifier_type,
                "mtype": match_type,
                "ivalue": identifier_value,
                "raw": raw_value,
                "reason": reason,
                "by": created_by,
                "rstart": range_start,
                "rend": range_end,
            },
        )
        return _row(rows[0]) if rows else None

    def delete(self, entry_id: str) -> bool:
        sql = "DELETE FROM merge_denylist WHERE id = %(id)s::uuid RETURNING id"
        rows = self.db.execute_query(sql, {"id": str(entry_id)})
        return bool(rows)

    def load_enabled_components(self) -> DenylistMatcher:
        """Enabled entries as a matcher — read once per pipeline run.

        A structured matcher rather than a flat frozenset of components, because
        an entry can now be a pattern (#991). `component in matcher` still reads
        the same at both seams and still costs one set lookup for exact entries;
        DenylistMatcher explains how patterns stay O(1) in the entry count.
        """
        sql = (
            "SELECT identifier_type, match_type, identifier_value, "
            "range_start, range_end FROM merge_denylist WHERE enabled"
        )
        rows = self.db.execute_query(sql) or []
        return DenylistMatcher(rows)
