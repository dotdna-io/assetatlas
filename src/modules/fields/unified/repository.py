# SPDX-License-Identifier: AGPL-3.0-or-later
"""Raw-SQL persistence for Unified Field definitions (Blueprint -> Service ->
Repository -> db_adapter.execute_query). Named %(...)s placeholders."""

import json
import logging
import psycopg2
from typing import Any, Dict, List, Optional

from src.modules.shared.exceptions import ConflictError
from src.modules.fields.types import FieldDataType

logger = logging.getLogger(__name__)

_COLS = "id, slug, name, function, sources, data_type, created_at, updated_at"


class UnifiedFieldsRepository:
    def __init__(self, db_adapter):
        self.db = db_adapter

    def list_all(self) -> List[Dict[str, Any]]:
        rows = self.db.execute_query(
            f"SELECT {_COLS} FROM unified_fields ORDER BY lower(name)")
        return [self._row(r) for r in (rows or [])]

    def list_for_catalog(self) -> List[Dict[str, Any]]:
        rows = self.db.execute_query(
            "SELECT slug, name, function, sources, data_type FROM unified_fields ORDER BY lower(name)")
        out = []
        for r in (rows or []):
            try:
                dt = FieldDataType(r["data_type"])
            except ValueError:
                logger.warning("unified_fields row slug=%r has unknown data_type %r — skipping",
                               r.get("slug"), r.get("data_type"))
                continue
            out.append({"slug": r["slug"], "name": r["name"], "function": r["function"],
                        "sources": list(r["sources"]), "data_type": dt})
        return out

    def get(self, field_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.execute_query(
            f"SELECT {_COLS} FROM unified_fields WHERE id = %(id)s::uuid",
            {"id": str(field_id)})
        return self._row(rows[0]) if rows else None

    def name_exists(self, name: str, exclude_id: Optional[str] = None) -> bool:
        sql = "SELECT 1 FROM unified_fields WHERE lower(name) = lower(%(name)s)"
        params: Dict[str, Any] = {"name": name}
        if exclude_id is not None:
            sql += " AND id <> %(id)s::uuid"
            params["id"] = str(exclude_id)
        return bool(self.db.execute_query(sql, params))

    def slug_exists(self, slug: str) -> bool:
        return bool(self.db.execute_query(
            "SELECT 1 FROM unified_fields WHERE lower(slug) = lower(%(slug)s)", {"slug": slug}))

    def create(self, *, slug: str, name: str, function: str,
               sources: List[str], data_type: str) -> Dict[str, Any]:
        try:
            rows = self.db.execute_query(
                f"""INSERT INTO unified_fields (slug, name, function, sources, data_type)
                    VALUES (%(slug)s, %(name)s, %(function)s, %(sources)s::jsonb, %(data_type)s)
                    RETURNING {_COLS}""",
                {"slug": slug, "name": name, "function": function,
                 "sources": json.dumps(sources), "data_type": data_type})
        except psycopg2.IntegrityError as exc:
            raise ConflictError("A unified field with that name already exists") from exc
        return self._row(rows[0])

    def update(self, field_id: str, *, name: str, function: str,
               sources: List[str], data_type: str) -> Optional[Dict[str, Any]]:
        try:
            rows = self.db.execute_query(
                f"""UPDATE unified_fields
                       SET name = %(name)s, function = %(function)s,
                           sources = %(sources)s::jsonb, data_type = %(data_type)s
                     WHERE id = %(id)s::uuid
                 RETURNING {_COLS}""",
                {"id": str(field_id), "name": name, "function": function,
                 "sources": json.dumps(sources), "data_type": data_type})
        except psycopg2.IntegrityError as exc:
            raise ConflictError("A unified field with that name already exists") from exc
        return self._row(rows[0]) if rows else None

    def delete(self, field_id: str) -> bool:
        rows = self.db.execute_query(
            "DELETE FROM unified_fields WHERE id = %(id)s::uuid RETURNING id",
            {"id": str(field_id)})
        return bool(rows)

    @staticmethod
    def _row(r: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": str(r["id"]),
            "slug": r["slug"],
            "name": r["name"],
            "function": r["function"],
            "sources": list(r["sources"]),
            "data_type": r["data_type"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
