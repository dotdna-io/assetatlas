# SPDX-License-Identifier: AGPL-3.0-or-later
"""SavedFilterRepository — per-user saved Explorer filters (raw SQL).

Named-placeholder idiom (%(name)s), consistent with execute_query().
Modelled on src/api/blueprints/chat/repository.py. Phase 1 reads/writes are
owner-scoped only; access widening (link/org/grants) lands in later phases.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import psycopg2

from src.api.middleware.error_handler import ConflictError
from src.database.postgres_adapter import PostgreSQLAdapter

_COLS = ("id, owner_id, name, description, payload, visibility, "
         "created_at, updated_at")


def _is_duplicate_name(exc: Exception) -> bool:
    """True when ``exc`` is the per-owner unique-name violation.

    psycopg2 normally raises UniqueViolation; some adapter configurations
    surface the parent IntegrityError instead, so fall back to matching the
    index name (mirrors src/modules/teams_link/repository.py). saved_filters'
    only unique constraint besides the random-UUID pkey is uq_saved_filters_
    owner_name, so a UniqueViolation here is always a name collision.
    """
    if isinstance(exc, psycopg2.errors.UniqueViolation):
        return True
    return "uq_saved_filters_owner_name" in str(exc)


@dataclass
class SavedFilterRow:
    id: str
    owner_id: str
    name: str
    description: Optional[str]
    payload: Dict[str, Any]
    visibility: str
    created_at: datetime
    updated_at: datetime


def _from_row(r: Dict[str, Any]) -> SavedFilterRow:
    return SavedFilterRow(
        id=str(r["id"]),
        owner_id=str(r["owner_id"]),
        name=r["name"],
        description=r["description"],
        payload=r["payload"],          # jsonb comes back as a dict
        visibility=r["visibility"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


class SavedFilterRepository:
    """CRUD for saved_filters, scoped to the owning user."""

    def __init__(self, db: PostgreSQLAdapter):
        self.db = db

    def create(self, *, owner_id: str, name: str, description: Optional[str],
               payload: Dict[str, Any], visibility: str) -> SavedFilterRow:
        sql = f"""
            INSERT INTO saved_filters (owner_id, name, description, payload, visibility)
            VALUES (%(owner_id)s::uuid, %(name)s, %(description)s,
                    %(payload)s::jsonb, %(visibility)s)
            RETURNING {_COLS}
        """
        try:
            rows = self.db.execute_query(sql, {
                "owner_id": str(owner_id),
                "name": name,
                "description": description,
                "payload": json.dumps(payload, allow_nan=False),
                "visibility": visibility,
            })
        except psycopg2.IntegrityError as exc:
            # The service pre-checks name_exists(), but a concurrent create with
            # the same name races that check; the partial unique index is the
            # real guard. Map its violation to a clean 409 rather than letting
            # it surface as a generic 500.
            if _is_duplicate_name(exc):
                raise ConflictError(
                    f"A saved filter named '{name}' already exists"
                ) from exc
            raise
        return _from_row(rows[0])

    def get_for_owner(self, filter_id: str, *, owner_id: str) -> Optional[SavedFilterRow]:
        sql = f"""
            SELECT {_COLS}
            FROM saved_filters
            WHERE id = %(id)s::uuid AND owner_id = %(uid)s::uuid
              AND deleted_at IS NULL
        """
        rows = self.db.execute_query(sql, {"id": str(filter_id), "uid": str(owner_id)})
        return _from_row(rows[0]) if rows else None

    def get_for_user(self, filter_id: str, *, user_id: str) -> Optional[SavedFilterRow]:
        """Readable by the owner, or by anyone when visibility is link/org.

        Phase 2 sharing: private filters remain owner-only (grants land in a
        later phase). Returns None for a private filter the user doesn't own,
        so the route 404s without leaking existence.
        """
        sql = f"""
            SELECT {_COLS}
            FROM saved_filters
            WHERE id = %(id)s::uuid
              AND deleted_at IS NULL
              AND (owner_id = %(uid)s::uuid OR visibility IN ('link','org'))
        """
        rows = self.db.execute_query(sql, {"id": str(filter_id), "uid": str(user_id)})
        return _from_row(rows[0]) if rows else None

    def list_for_owner(self, *, owner_id: str) -> List[SavedFilterRow]:
        sql = f"""
            SELECT {_COLS}
            FROM saved_filters
            WHERE owner_id = %(uid)s::uuid AND deleted_at IS NULL
            ORDER BY updated_at DESC
        """
        rows = self.db.execute_query(sql, {"uid": str(owner_id)}) or []
        return [_from_row(r) for r in rows]

    def list_org(self, *, exclude_owner: str) -> List[SavedFilterRow]:
        """Org-library filters (visibility='org') not owned by the viewer.

        The viewer's own org filters already appear in their `mine` list, so
        exclude them here to avoid showing a filter twice.
        """
        sql = f"""
            SELECT {_COLS}
            FROM saved_filters
            WHERE visibility = 'org' AND deleted_at IS NULL
              AND owner_id <> %(uid)s::uuid
            ORDER BY updated_at DESC
        """
        rows = self.db.execute_query(sql, {"uid": str(exclude_owner)}) or []
        return [_from_row(r) for r in rows]

    def update(self, filter_id: str, *, owner_id: str, name: str,
               description: Optional[str], payload: Dict[str, Any],
               visibility: str) -> Optional[SavedFilterRow]:
        sql = f"""
            UPDATE saved_filters
            SET name = %(name)s, description = %(description)s,
                payload = %(payload)s::jsonb, visibility = %(visibility)s
            WHERE id = %(id)s::uuid AND owner_id = %(uid)s::uuid
              AND deleted_at IS NULL
            RETURNING {_COLS}
        """
        try:
            rows = self.db.execute_query(sql, {
                "id": str(filter_id),
                "uid": str(owner_id),
                "name": name,
                "description": description,
                "payload": json.dumps(payload, allow_nan=False),
                "visibility": visibility,
            })
        except psycopg2.IntegrityError as exc:
            # Concurrent rename to an already-taken name races name_exists();
            # translate the unique-index violation to a 409 (see create()).
            if _is_duplicate_name(exc):
                raise ConflictError(
                    f"A saved filter named '{name}' already exists"
                ) from exc
            raise
        return _from_row(rows[0]) if rows else None

    def soft_delete(self, filter_id: str, *, owner_id: str) -> bool:
        sql = """
            UPDATE saved_filters
            SET deleted_at = NOW()
            WHERE id = %(id)s::uuid AND owner_id = %(uid)s::uuid
              AND deleted_at IS NULL
            RETURNING id
        """
        rows = self.db.execute_query(sql, {"id": str(filter_id), "uid": str(owner_id)})
        return bool(rows)

    def name_exists(self, *, owner_id: str, name: str,
                    exclude_id: Optional[str] = None) -> bool:
        sql = """
            SELECT id FROM saved_filters
            WHERE owner_id = %(uid)s::uuid AND lower(name) = lower(%(name)s)
              AND deleted_at IS NULL
              AND (%(exclude)s::uuid IS NULL OR id <> %(exclude)s::uuid)
            LIMIT 1
        """
        rows = self.db.execute_query(sql, {
            "uid": str(owner_id),
            "name": name,
            "exclude": str(exclude_id) if exclude_id else None,
        })
        return bool(rows)
