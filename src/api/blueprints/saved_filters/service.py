# SPDX-License-Identifier: AGPL-3.0-or-later
"""SavedFiltersService — business rules over SavedFilterRepository."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.api.middleware.error_handler import ConflictError, ForbiddenError

from .models import SavedFilterCreate, SavedFilterUpdate
from .repository import SavedFilterRepository, SavedFilterRow


class SavedFiltersService:
    def __init__(self, repo: SavedFilterRepository):
        self.repo = repo

    def create(self, *, owner_id: str, is_admin: bool,
               data: SavedFilterCreate) -> SavedFilterRow:
        if data.visibility == "org" and not is_admin:
            raise ForbiddenError("Only admins can publish a filter to the shared library")
        if self.repo.name_exists(owner_id=owner_id, name=data.name):
            raise ConflictError(f"A saved filter named '{data.name}' already exists")
        return self.repo.create(
            owner_id=owner_id, name=data.name, description=data.description,
            payload=data.payload, visibility=data.visibility,
        )

    def list_for_user(self, *, user_id: str) -> Dict[str, List[SavedFilterRow]]:
        # Phase 3: own filters + the org-wide library (others' published
        # filters). Specific-user "shared_with_me" lands in a later phase.
        return {
            "mine": self.repo.list_for_owner(owner_id=user_id),
            "shared_with_me": [],
            "library": self.repo.list_org(exclude_owner=user_id),
        }

    def get(self, filter_id: str, *, user_id: str) -> Optional[SavedFilterRow]:
        return self.repo.get_for_user(filter_id, user_id=user_id)

    def update(self, filter_id: str, *, owner_id: str, is_admin: bool,
               data: SavedFilterUpdate) -> Optional[SavedFilterRow]:
        existing = self.repo.get_for_owner(filter_id, owner_id=owner_id)
        if existing is None:
            return None
        name = data.name if data.name is not None else existing.name
        description = (data.description if data.description is not None
                      else existing.description)
        payload = data.payload if data.payload is not None else existing.payload
        visibility = (data.visibility if data.visibility is not None
                      else existing.visibility)
        if visibility == "org" and not is_admin:
            raise ForbiddenError("Only admins can publish a filter to the shared library")
        if name != existing.name and self.repo.name_exists(
                owner_id=owner_id, name=name, exclude_id=filter_id):
            raise ConflictError(f"A saved filter named '{name}' already exists")
        return self.repo.update(
            filter_id, owner_id=owner_id, name=name, description=description,
            payload=payload, visibility=visibility,
        )

    def delete(self, filter_id: str, *, owner_id: str) -> bool:
        return self.repo.soft_delete(filter_id, owner_id=owner_id)

    @staticmethod
    def to_dict(row: SavedFilterRow, *, viewer_id: str) -> Dict[str, Any]:
        is_owner = row.owner_id == str(viewer_id)
        return {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "payload": row.payload,
            "visibility": row.visibility,
            "access": "owner" if is_owner else row.visibility,
            "can_edit": is_owner,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }
