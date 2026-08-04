# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repository unit tests with a stubbed db_adapter (no live DB)."""
from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import psycopg2
import pytest

from src.modules.teams_link.repository import (TeamsLinkRepository,
                                                UsedJtiStore)


@pytest.fixture
def repo():
    db = MagicMock()
    return TeamsLinkRepository(db_adapter=db), db


def test_upsert_link_runs_insert_with_fetch_false(repo):
    r, db = repo
    user_id = uuid4()
    r.upsert_link(
        aad_object_id="aad-1", tenant_id="t-1", user_id=user_id,
        linked_via_ip="10.0.0.1",
    )
    sql, params = db.execute_query.call_args[0]
    assert "INSERT INTO teams_account_links" in sql
    assert "ON CONFLICT" in sql
    assert "RETURNING" not in sql
    assert params["aad_object_id"] == "aad-1"
    assert params["user_id"] == user_id
    assert db.execute_query.call_args.kwargs == {"fetch": False}


def test_find_link_returns_none_when_missing(repo):
    r, db = repo
    db.execute_query.return_value = []
    assert r.find_link("aad-x", "t-x") is None


def test_find_link_returns_model_when_present(repo):
    r, db = repo
    db.execute_query.return_value = [{
        "id": uuid4(), "aad_object_id": "aad-1", "tenant_id": "t-1",
        "user_id": uuid4(), "linked_at": "2026-01-01T00:00:00Z",
        "linked_via_ip": None,
    }]
    link = r.find_link("aad-1", "t-1")
    assert link is not None
    assert link.aad_object_id == "aad-1"


def test_find_conversation_session_returns_none_when_missing(repo):
    r, db = repo
    db.execute_query.return_value = []
    assert r.find_conversation_session("conv-x") is None


def test_find_conversation_session_returns_model_when_present(repo):
    r, db = repo
    sess_id = uuid4()
    user_id = uuid4()
    db.execute_query.return_value = [{
        "id": uuid4(), "conversation_id": "conv-1",
        "user_id": user_id, "session_id": sess_id,
        "created_at": "2026-01-01T00:00:00Z",
        "last_active_at": "2026-01-01T00:00:00Z",
    }]
    found = r.find_conversation_session("conv-1")
    assert found is not None
    assert found.session_id == sess_id


def test_create_conversation_session_runs(repo):
    r, db = repo
    db.execute_query.return_value = [{
        "id": uuid4(), "conversation_id": "conv-1",
        "user_id": uuid4(), "session_id": uuid4(),
        "created_at": "2026-01-01T00:00:00Z",
        "last_active_at": "2026-01-01T00:00:00Z",
    }]
    r.create_conversation_session(
        conversation_id="conv-1", user_id=uuid4(), session_id=uuid4(),
    )
    sql, _params = db.execute_query.call_args[0]
    assert "INSERT INTO teams_conversation_sessions" in sql


def test_touch_conversation_session_runs(repo):
    r, db = repo
    r.touch_conversation_session("conv-1")
    sql, params = db.execute_query.call_args[0]
    assert "UPDATE teams_conversation_sessions" in sql
    assert params["conversation_id"] == "conv-1"


def test_delete_conversation_session_runs(repo):
    r, db = repo
    r.delete_conversation_session("conv-1")
    sql, params = db.execute_query.call_args[0]
    assert "DELETE FROM teams_conversation_sessions" in sql
    assert params["conversation_id"] == "conv-1"


def test_used_jti_store_mark_used_returns_true_on_clean_insert():
    db = MagicMock()
    store = UsedJtiStore(db_adapter=db)
    assert store.mark_used("jti-1") is True
    sql, _ = db.execute_query.call_args[0]
    assert "INSERT INTO teams_link_used_jtis" in sql


def test_used_jti_store_mark_used_returns_false_on_unique_violation():
    db = MagicMock()
    db.execute_query.side_effect = psycopg2.errors.lookup("23505")
    store = UsedJtiStore(db_adapter=db)
    assert store.mark_used("jti-1") is False


def test_used_jti_store_mark_used_propagates_other_errors():
    db = MagicMock()
    db.execute_query.side_effect = psycopg2.OperationalError("connection lost")
    store = UsedJtiStore(db_adapter=db)
    with pytest.raises(psycopg2.OperationalError):
        store.mark_used("jti-1")


def test_used_jti_store_is_used_returns_bool():
    db = MagicMock()
    db.execute_query.return_value = []
    store = UsedJtiStore(db_adapter=db)
    assert store.is_used("jti-x") is False
    db.execute_query.return_value = [{"?column?": 1}]
    assert store.is_used("jti-x") is True
