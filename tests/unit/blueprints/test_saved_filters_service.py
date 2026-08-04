# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.api.blueprints.saved_filters.models import (
    SavedFilterCreate, SavedFilterUpdate,
)
from src.api.blueprints.saved_filters.repository import SavedFilterRow
from src.api.blueprints.saved_filters.service import SavedFiltersService
from src.api.middleware.error_handler import ConflictError, ForbiddenError

_NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)


def _row(**over):
    base = dict(id="fid", owner_id="uid", name="Servers", description=None,
                payload={"version": 1}, visibility="private",
                created_at=_NOW, updated_at=_NOW)
    base.update(over)
    return SavedFilterRow(**base)


def test_create_private_succeeds_for_non_admin():
    repo = MagicMock()
    repo.name_exists.return_value = False
    repo.create.return_value = _row()
    svc = SavedFiltersService(repo)

    out = svc.create(owner_id="uid", is_admin=False,
                     data=SavedFilterCreate(name="Servers", payload={"version": 1}))
    assert out.name == "Servers"
    repo.create.assert_called_once()


def test_create_org_requires_admin():
    repo = MagicMock()
    repo.name_exists.return_value = False
    svc = SavedFiltersService(repo)
    with pytest.raises(ForbiddenError):
        svc.create(owner_id="uid", is_admin=False,
                   data=SavedFilterCreate(name="X", payload={"version": 1},
                                          visibility="org"))
    repo.create.assert_not_called()


def test_create_duplicate_name_conflicts():
    repo = MagicMock()
    repo.name_exists.return_value = True
    svc = SavedFiltersService(repo)
    with pytest.raises(ConflictError):
        svc.create(owner_id="uid", is_admin=False,
                   data=SavedFilterCreate(name="Servers", payload={"version": 1}))


def test_list_for_user_fills_mine_and_library():
    repo = MagicMock()
    repo.list_for_owner.return_value = [_row(name="A")]
    repo.list_org.return_value = [_row(name="Fleet", visibility="org")]
    svc = SavedFiltersService(repo)
    out = svc.list_for_user(user_id="uid")
    assert [r.name for r in out["mine"]] == ["A"]
    assert [r.name for r in out["library"]] == ["Fleet"]
    assert out["shared_with_me"] == []
    repo.list_org.assert_called_once_with(exclude_owner="uid")


def test_update_merges_partial_fields():
    repo = MagicMock()
    repo.get_for_owner.return_value = _row(name="Old", description="d")
    repo.name_exists.return_value = False
    repo.update.return_value = _row(name="New", description="d")
    svc = SavedFiltersService(repo)

    out = svc.update("fid", owner_id="uid", is_admin=False,
                     data=SavedFilterUpdate(name="New"))
    assert out.name == "New"
    # description preserved from existing row (partial update)
    _, kwargs = repo.update.call_args
    assert kwargs["description"] == "d"
    assert kwargs["payload"] == {"version": 1}


def test_update_missing_returns_none():
    repo = MagicMock()
    repo.get_for_owner.return_value = None
    svc = SavedFiltersService(repo)
    assert svc.update("fid", owner_id="uid", is_admin=False,
                      data=SavedFilterUpdate(name="New")) is None


def test_to_dict_owner():
    svc = SavedFiltersService(MagicMock())
    d = svc.to_dict(_row(owner_id="u1", visibility="private"), viewer_id="u1")
    assert d["access"] == "owner" and d["can_edit"] is True
    # timestamps stay timezone-aware: the frontend parses created_at as UTC
    assert d["created_at"].endswith("+00:00")


def test_to_dict_non_owner_link():
    svc = SavedFiltersService(MagicMock())
    d = svc.to_dict(_row(owner_id="u1", visibility="link"), viewer_id="u2")
    assert d["access"] == "link" and d["can_edit"] is False


def test_get_uses_get_for_user():
    repo = MagicMock()
    repo.get_for_user.return_value = _row()
    svc = SavedFiltersService(repo)
    svc.get("fid", user_id="u2")
    repo.get_for_user.assert_called_once_with("fid", user_id="u2")
