# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime, timezone
from unittest.mock import MagicMock

import psycopg2
import pytest

from src.api.blueprints.saved_filters.repository import (
    SavedFilterRepository, SavedFilterRow,
)
from src.api.middleware.error_handler import ConflictError

_NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)


def _row(**over):
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "owner_id": "22222222-2222-2222-2222-222222222222",
        "name": "Servers",
        "description": None,
        "payload": {"version": 1},
        "visibility": "private",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    base.update(over)
    return base


def test_create_passes_owner_and_serialises_payload():
    db = MagicMock()
    db.execute_query.return_value = [_row()]
    repo = SavedFilterRepository(db)

    out = repo.create(owner_id="22222222-2222-2222-2222-222222222222",
                      name="Servers", description=None,
                      payload={"version": 1}, visibility="private")

    assert isinstance(out, SavedFilterRow)
    assert out.id == "11111111-1111-1111-1111-111111111111"
    # params dict carries the owner id and a JSON-serialised payload string
    args, kwargs = db.execute_query.call_args
    params = args[1]
    assert params["owner_id"] == "22222222-2222-2222-2222-222222222222"
    assert params["payload"] == '{"version": 1}'
    assert params["visibility"] == "private"


def test_get_for_owner_returns_none_when_no_row():
    db = MagicMock()
    db.execute_query.return_value = []
    repo = SavedFilterRepository(db)
    assert repo.get_for_owner("x", owner_id="y") is None


def test_get_for_user_queries_owner_or_shared():
    db = MagicMock()
    db.execute_query.return_value = [_row(visibility="link")]
    repo = SavedFilterRepository(db)
    out = repo.get_for_user("fid", user_id="someone-else")
    assert out is not None and out.visibility == "link"
    sql, params = db.execute_query.call_args[0]
    # security-critical clause: owner OR link/org
    assert "owner_id = %(uid)s::uuid OR visibility IN ('link','org')" in sql
    assert params["id"] == "fid" and params["uid"] == "someone-else"


def test_get_for_user_returns_none_when_no_row():
    db = MagicMock()
    db.execute_query.return_value = []
    repo = SavedFilterRepository(db)
    assert repo.get_for_user("fid", user_id="u") is None


def test_list_for_owner_maps_rows():
    db = MagicMock()
    db.execute_query.return_value = [_row(name="A"), _row(name="B")]
    repo = SavedFilterRepository(db)
    out = repo.list_for_owner(owner_id="y")
    assert [r.name for r in out] == ["A", "B"]


def test_list_org_excludes_viewer_and_filters_to_org():
    db = MagicMock()
    db.execute_query.return_value = [_row(visibility="org", name="Fleet")]
    repo = SavedFilterRepository(db)
    out = repo.list_org(exclude_owner="me")
    assert [r.name for r in out] == ["Fleet"]
    sql, params = db.execute_query.call_args[0]
    assert "visibility = 'org'" in sql
    assert "owner_id <> %(uid)s::uuid" in sql
    # the library must never leak soft-deleted filters to other users
    assert "deleted_at IS NULL" in sql
    assert params["uid"] == "me"


def test_soft_delete_returns_true_when_row_affected():
    db = MagicMock()
    db.execute_query.return_value = [{"id": "x"}]
    repo = SavedFilterRepository(db)
    assert repo.soft_delete("x", owner_id="y") is True


def test_soft_delete_returns_false_when_nothing_affected():
    db = MagicMock()
    db.execute_query.return_value = []
    repo = SavedFilterRepository(db)
    assert repo.soft_delete("x", owner_id="y") is False


def test_name_exists_true_when_row_returned():
    db = MagicMock()
    db.execute_query.return_value = [{"id": "x"}]
    repo = SavedFilterRepository(db)
    assert repo.name_exists(owner_id="y", name="Servers") is True


def test_create_translates_unique_violation_to_conflict():
    # Guards the name_exists() TOCTOU race: a concurrent duplicate trips the
    # partial unique index, which must surface as 409 (ConflictError), not 500.
    db = MagicMock()
    db.execute_query.side_effect = psycopg2.errors.UniqueViolation("dup")
    repo = SavedFilterRepository(db)
    with pytest.raises(ConflictError):
        repo.create(owner_id="y", name="Servers", description=None,
                    payload={"version": 1}, visibility="private")


def test_update_translates_unique_violation_to_conflict():
    db = MagicMock()
    db.execute_query.side_effect = psycopg2.errors.UniqueViolation("dup")
    repo = SavedFilterRepository(db)
    with pytest.raises(ConflictError):
        repo.update("fid", owner_id="y", name="Taken", description=None,
                    payload={"version": 1}, visibility="private")


def test_create_reraises_non_unique_integrity_error():
    # A FK violation (e.g. owner deleted) is an IntegrityError but NOT a name
    # collision; it must propagate, not be mislabelled as a duplicate-name 409.
    db = MagicMock()
    db.execute_query.side_effect = psycopg2.errors.ForeignKeyViolation("fk")
    repo = SavedFilterRepository(db)
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        repo.create(owner_id="y", name="Servers", description=None,
                    payload={"version": 1}, visibility="private")
