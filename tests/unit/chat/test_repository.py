# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for ChatRepository with a mocked db adapter.

Verifies the repository constructs the right SQL, serialises JSONB params
correctly, and returns the expected dataclasses without needing a live DB.
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

from src.api.blueprints.chat.repository import (
    ChatRepository,
    FeedbackRow,
    MessageRow,
    SessionList,
    SessionRow,
    ToolCallRow,
)


def _fake_session_row(user_id=None):
    return {
        "id": uuid4(),
        "user_id": user_id or uuid4(),
        "title": "t",
        "model": "m",
        "provider": "anthropic",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }


def _fake_session_row_with_total(total, user_id=None):
    row = _fake_session_row(user_id=user_id)
    row["_total"] = total
    return row


def _fake_message_row(session_id=None, seq=1, role="user", content="hi",
                      tool_calls=None):
    return {
        "id": uuid4(),
        "session_id": session_id or uuid4(),
        "seq": seq,
        "role": role,
        "content": content,
        "tool_calls": tool_calls,
        "input_tokens": None,
        "output_tokens": None,
        "error": None,
        "created_at": datetime.now(timezone.utc),
    }


# ---------- create_session ----------

def test_create_session_returns_session_row():
    db = MagicMock()
    row = _fake_session_row()
    db.execute_query.return_value = [row]
    repo = ChatRepository(db)

    s = repo.create_session(
        user_id=str(uuid4()), title="t", model="m", provider="anthropic",
    )

    assert isinstance(s, SessionRow)
    assert s.title == "t"
    assert s.model == "m"
    assert s.provider == "anthropic"
    assert db.execute_query.call_count == 1
    args, _ = db.execute_query.call_args
    assert "INSERT INTO chat_sessions" in args[0]
    assert "RETURNING" in args[0]


# ---------- load_session ----------

def test_load_session_returns_none_when_empty():
    db = MagicMock()
    db.execute_query.return_value = []
    repo = ChatRepository(db)

    assert repo.load_session(str(uuid4()), user_id=str(uuid4())) is None


def test_load_session_returns_row_when_found():
    db = MagicMock()
    db.execute_query.return_value = [_fake_session_row()]
    repo = ChatRepository(db)

    s = repo.load_session(str(uuid4()), user_id=str(uuid4()))

    assert isinstance(s, SessionRow)


def test_load_session_respects_user_id():
    db = MagicMock()
    db.execute_query.return_value = []
    repo = ChatRepository(db)
    sid = str(uuid4())
    uid = str(uuid4())

    repo.load_session(sid, user_id=uid)

    args, _ = db.execute_query.call_args
    params = args[1]
    assert params["sid"] == sid
    assert params["uid"] == uid


# ---------- list_sessions ----------

def test_list_sessions_pagination():
    db = MagicMock()
    rows = [_fake_session_row_with_total(total=42) for _ in range(3)]
    db.execute_query.return_value = rows
    repo = ChatRepository(db)

    out = repo.list_sessions(user_id=str(uuid4()), page=2, per_page=10)

    assert isinstance(out, SessionList)
    assert out.total == 42
    assert len(out.sessions) == 3
    # Single call: the SELECT pulls total via COUNT(*) OVER ().
    assert db.execute_query.call_count == 1
    args, _ = db.execute_query.call_args
    sql = args[0]
    params = args[1]
    assert "COUNT(*) OVER ()" in sql
    assert params["lim"] == 10
    assert params["off"] == 10  # (page-1)*per_page = 10


def test_list_sessions_empty_total_zero():
    db = MagicMock()
    db.execute_query.return_value = []
    repo = ChatRepository(db)

    out = repo.list_sessions(user_id=str(uuid4()))

    assert out.sessions == []
    assert out.total == 0
    assert db.execute_query.call_count == 1


# ---------- rename_session ----------

def test_rename_session_updates_title_and_timestamp():
    db = MagicMock()
    db.execute_query.return_value = [_fake_session_row()]
    repo = ChatRepository(db)

    s = repo.rename_session(str(uuid4()), user_id=str(uuid4()), title="new")

    assert isinstance(s, SessionRow)
    args, _ = db.execute_query.call_args
    sql = args[0]
    assert "UPDATE chat_sessions" in sql
    assert "SET title" in sql
    # updated_at is bumped by the chat_sessions_updated_at trigger, not SQL.


def test_rename_session_returns_none_when_not_found():
    db = MagicMock()
    db.execute_query.return_value = []
    repo = ChatRepository(db)

    assert repo.rename_session(
        str(uuid4()), user_id=str(uuid4()), title="new",
    ) is None


# ---------- delete_session ----------

def test_delete_session_soft_deletes_and_returns_bool():
    db = MagicMock()
    db.execute_query.return_value = [{"id": uuid4()}]
    repo = ChatRepository(db)

    assert repo.delete_session(str(uuid4()), user_id=str(uuid4())) is True
    args, _ = db.execute_query.call_args
    sql = args[0]
    assert "UPDATE chat_sessions" in sql
    assert "deleted_at = NOW()" in sql


def test_delete_session_returns_false_when_nothing_to_delete():
    db = MagicMock()
    db.execute_query.return_value = []
    repo = ChatRepository(db)

    assert repo.delete_session(str(uuid4()), user_id=str(uuid4())) is False


# ---------- append_message ----------

def test_append_message_bumps_parent_updated_at():
    db = MagicMock()
    message_row = _fake_message_row(seq=1)
    db.execute_query.return_value = [message_row]
    repo = ChatRepository(db)

    m = repo.append_message(str(uuid4()), role="user", content="hi")

    assert isinstance(m, MessageRow)
    assert m.seq == 1
    # Single CTE round-trip: INSERT + session bump in one query.
    assert db.execute_query.call_count == 1
    args, _ = db.execute_query.call_args
    sql = args[0]
    assert "WITH ins AS" in sql
    assert "UPDATE chat_sessions SET updated_at = NOW()" in sql


def test_append_message_serialises_tool_calls_to_json():
    db = MagicMock()
    tool_calls = [{"id": "t1", "name": "list_assets"}]
    message_row = _fake_message_row(
        seq=2, role="assistant", content=None, tool_calls=tool_calls,
    )
    db.execute_query.return_value = [message_row]
    repo = ChatRepository(db)

    repo.append_message(
        str(uuid4()), role="assistant", tool_calls=tool_calls,
    )

    insert_args, _ = db.execute_query.call_args
    params = insert_args[1]
    assert params["tc"] == json.dumps(tool_calls)


def test_append_message_tool_calls_none_when_omitted():
    db = MagicMock()
    db.execute_query.return_value = [_fake_message_row()]
    repo = ChatRepository(db)

    repo.append_message(str(uuid4()), role="user", content="hello")

    insert_args, _ = db.execute_query.call_args
    params = insert_args[1]
    assert params["tc"] is None


# ---------- load_messages ----------

def test_load_messages_orders_by_seq_asc():
    db = MagicMock()
    db.execute_query.return_value = []
    repo = ChatRepository(db)

    repo.load_messages(str(uuid4()))

    args, _ = db.execute_query.call_args
    sql = args[0]
    assert "FROM chat_messages" in sql
    assert "ORDER BY seq ASC" in sql


def test_load_messages_returns_empty_when_none():
    db = MagicMock()
    db.execute_query.return_value = None
    repo = ChatRepository(db)

    assert repo.load_messages(str(uuid4())) == []


# ---------- record_tool_call ----------

def test_record_tool_call_computes_result_bytes():
    db = MagicMock()
    tc_row = {
        "id": uuid4(),
        "message_id": uuid4(),
        "tool_name": "list_assets",
        "arguments": {"q": "srv"},
        "result": "hello",
        "status": "ok",
        "duration_ms": 12,
        "error": None,
        "created_at": datetime.now(timezone.utc),
    }
    db.execute_query.return_value = [tc_row]
    repo = ChatRepository(db)

    out = repo.record_tool_call(
        message_id=str(uuid4()),
        tool_name="list_assets",
        arguments={"q": "srv"},
        result="hello",
        status="ok",
        duration_ms=12,
    )

    assert isinstance(out, ToolCallRow)
    args, _ = db.execute_query.call_args
    sql = args[0]
    params = args[1]
    assert "INSERT INTO chat_tool_calls" in sql
    assert params["rb"] == 5  # "hello" -> 5 bytes
    assert params["a"] == json.dumps({"q": "srv"})


def test_record_tool_call_handles_null_result():
    db = MagicMock()
    tc_row = {
        "id": uuid4(),
        "message_id": uuid4(),
        "tool_name": "list_assets",
        "arguments": {},
        "result": None,
        "status": "error",
        "duration_ms": None,
        "error": "boom",
        "created_at": datetime.now(timezone.utc),
    }
    db.execute_query.return_value = [tc_row]
    repo = ChatRepository(db)

    repo.record_tool_call(
        message_id=str(uuid4()),
        tool_name="list_assets",
        arguments={},
        result=None,
        status="error",
        error="boom",
    )

    args, _ = db.execute_query.call_args
    params = args[1]
    assert params["rb"] == 0
    assert params["r"] is None


def test_record_tool_call_persists_artifact_as_jsonb():
    """When a tool produces a chart envelope, record_tool_call serialises
    it into the `artifact` JSONB column. Downstream SELECTs deserialise
    it automatically, so ToolCallRow.artifact is a native dict."""
    db = MagicMock()
    artifact = {
        "type": "donut",
        "title": "Assets by Category",
        "series": [{"label": "server", "value": 5}],
        "meta": {"total": 5, "other_count": 0},
    }
    tc_row = {
        "id": uuid4(),
        "message_id": uuid4(),
        "tool_name": "aggregate_assets",
        "arguments": {"row_field": "category"},
        "result": "COUNT: 5",
        "status": "ok",
        "duration_ms": 20,
        "error": None,
        "created_at": datetime.now(timezone.utc),
        "artifact": artifact,  # psycopg2 deserialises JSONB to dict
    }
    db.execute_query.return_value = [tc_row]
    repo = ChatRepository(db)

    out = repo.record_tool_call(
        message_id=str(uuid4()),
        tool_name="aggregate_assets",
        arguments={"row_field": "category"},
        result="COUNT: 5",
        status="ok",
        artifact=artifact,
    )

    args, _ = db.execute_query.call_args
    sql = args[0]
    params = args[1]
    assert "artifact" in sql
    # Artifact goes in as serialised JSON with ::jsonb cast, same as arguments.
    assert params["art"] == json.dumps(artifact)
    # Returned row's artifact column is exposed as a native dict on the
    # dataclass so callers don't have to know about the JSONB plumbing.
    assert out.artifact == artifact


def test_record_tool_call_omits_artifact_when_not_supplied():
    """Tools that don't produce a chart leave the column NULL — not an
    empty dict — so the SQL shape stays stable and the frontend can do
    a simple truthy check."""
    db = MagicMock()
    tc_row = {
        "id": uuid4(), "message_id": uuid4(), "tool_name": "search_assets",
        "arguments": {}, "result": "found 3", "status": "ok",
        "duration_ms": 5, "error": None,
        "created_at": datetime.now(timezone.utc),
        "artifact": None,
    }
    db.execute_query.return_value = [tc_row]
    repo = ChatRepository(db)

    out = repo.record_tool_call(
        message_id=str(uuid4()),
        tool_name="search_assets",
        arguments={},
        result="found 3",
        status="ok",
    )

    params = db.execute_query.call_args[0][1]
    assert params["art"] is None
    assert out.artifact is None


def test_load_tool_calls_by_message_returns_artifact():
    db = MagicMock()
    mid = uuid4()
    artifact = {"type": "donut", "title": "x", "series": []}
    db.execute_query.return_value = [{
        "id": uuid4(), "message_id": mid, "tool_name": "aggregate_assets",
        "arguments": {}, "result": "r", "status": "ok", "duration_ms": 1,
        "error": None, "created_at": datetime.now(timezone.utc),
        "artifact": artifact,
    }]
    repo = ChatRepository(db)
    out = repo.load_tool_calls_by_message(str(uuid4()))
    all_rows = [tc for calls in out.values() for tc in calls]
    assert len(all_rows) == 1
    assert all_rows[0].artifact == artifact
    # Verify the SELECT lists the new column so history reload actually
    # fetches it (regression guard for accidentally dropping it).
    assert "artifact" in db.execute_query.call_args[0][0]


# ---------- upsert_feedback ----------

def test_upsert_feedback_uses_on_conflict_fetch_false():
    db = MagicMock()
    repo = ChatRepository(db)

    repo.upsert_feedback(
        message_id=str(uuid4()), user_id=str(uuid4()), rating=1,
    )

    args, kwargs = db.execute_query.call_args
    sql = args[0]
    assert "INSERT INTO chat_feedback" in sql
    assert "ON CONFLICT (message_id, user_id)" in sql
    assert "DO UPDATE" in sql
    assert kwargs.get("fetch") is False


# ---------- list_feedback_for_message ----------

def test_list_feedback_for_message_empty_when_none():
    db = MagicMock()
    db.execute_query.return_value = None
    repo = ChatRepository(db)

    assert repo.list_feedback_for_message(str(uuid4())) == []


def test_list_feedback_for_message_returns_rows():
    db = MagicMock()
    feedback = [
        {"rating": 1, "comment": "great", "created_at": datetime.now(timezone.utc)},
        {"rating": -1, "comment": None, "created_at": datetime.now(timezone.utc)},
    ]
    db.execute_query.return_value = feedback
    repo = ChatRepository(db)

    out = repo.list_feedback_for_message(str(uuid4()))

    assert len(out) == 2
    assert all(isinstance(r, FeedbackRow) for r in out)
    assert out[0].rating == 1
    assert out[0].comment == "great"
    assert out[1].rating == -1
    assert out[1].comment is None


# ---------------------------------------------------------------------------
# Admin aggregates (Phase 12.1)
# ---------------------------------------------------------------------------

def _usage_db_mock(*, totals, active_users, top_users, by_provider,
                    tool_calls, downvotes):
    """Build a MagicMock whose execute_query side-effects replay the five
    queries that ``usage_summary`` issues, in order. active_users is
    merged into the totals row (one combined scan)."""
    db = MagicMock()
    db.execute_query.side_effect = [
        [{**totals, "active_users": active_users}],
        top_users, by_provider, tool_calls, downvotes,
    ]
    return db


def test_usage_summary_shapes_response_from_five_queries():
    uid = uuid4()
    mid = uuid4()
    sid = uuid4()
    now = datetime.now(timezone.utc)

    db = _usage_db_mock(
        totals={
            "messages": 120, "sessions": 15,
            "tokens_input": 50_000, "tokens_output": 3_500,
        },
        active_users=7,
        top_users=[{"user_id": uid, "username": "alice", "messages": 40}],
        by_provider=[{"provider": "anthropic", "model": "claude-sonnet-4-6",
                      "input_tokens": 50_000, "output_tokens": 3_500,
                      "messages": 120}],
        tool_calls=[{"tool_name": "list_assets", "count": 80,
                     "error_count": 1, "timeout_count": 0, "denied_count": 0,
                     "avg_duration_ms": 120}],
        downvotes=[{
            "message_id": mid, "session_id": sid, "user_id": uid,
            "username": "bob", "comment": "wrong data",
            "created_at": now,
        }],
    )
    repo = ChatRepository(db)

    out = repo.usage_summary(days=7)

    assert out["window_days"] == 7
    assert out["totals"]["messages"] == 120
    assert out["totals"]["active_users"] == 7
    assert out["top_users"][0]["username"] == "alice"
    assert out["by_provider"][0]["provider"] == "anthropic"
    assert out["tool_calls"][0]["error_count"] == 1
    assert out["recent_downvotes"][0]["comment"] == "wrong data"
    # Datetimes serialised — Flask's jsonify wouldn't cope otherwise.
    assert isinstance(out["recent_downvotes"][0]["created_at"], str)


def test_usage_summary_clamps_days_via_param_cast():
    """Non-integer days inputs would fail at the SQL boundary; the repo
    casts to int defensively."""
    db = _usage_db_mock(
        totals={"messages": 0, "sessions": 0, "tokens_input": 0, "tokens_output": 0},
        active_users=0, top_users=[], by_provider=[], tool_calls=[],
        downvotes=[],
    )
    repo = ChatRepository(db)

    repo.usage_summary(days="30")  # type: ignore[arg-type]

    # First call is the totals query; its second positional arg is params.
    first_call_params = db.execute_query.call_args_list[0][0][1]
    assert first_call_params == {"days": 30}


def test_audit_messages_pagination_and_filters():
    db = MagicMock()
    now = datetime.now(timezone.utc)
    db.execute_query.side_effect = [
        [{"n": 7}],
        [{
            "message_id": uuid4(), "session_id": uuid4(), "user_id": uuid4(),
            "username": "alice", "role": "user",
            "content": "This is a long message " * 20,
            "input_tokens": None, "output_tokens": None, "error": None,
            "created_at": now,
        }],
    ]
    repo = ChatRepository(db)

    out = repo.audit_messages(
        days=14, page=2, per_page=25,
        user_id=str(uuid4()), role="user",
    )

    assert out["total"] == 7
    assert out["page"] == 2
    assert out["per_page"] == 25
    assert len(out["items"]) == 1
    # Preview bounded so the list view stays scannable.
    assert len(out["items"][0]["content_preview"]) <= 240

    # Both queries carry the offset = (page - 1) * per_page = 25 and role
    # filter; count query + page query both receive it.
    count_params = db.execute_query.call_args_list[0][0][1]
    page_params = db.execute_query.call_args_list[1][0][1]
    assert page_params["offset"] == 25
    assert page_params["limit"] == 25
    assert page_params["days"] == 14
    assert page_params["role"] == "user"
    assert "uid" in page_params
    # Count query shares the filter params.
    assert count_params["days"] == 14
    assert count_params["role"] == "user"


def test_audit_messages_without_filters_omits_where_clauses():
    db = MagicMock()
    db.execute_query.side_effect = [[{"n": 0}], []]
    repo = ChatRepository(db)

    repo.audit_messages(days=7, page=1, per_page=50)

    page_sql = db.execute_query.call_args_list[1][0][0]
    assert "s.user_id = %(uid)s" not in page_sql
    assert "m.role = %(role)s" not in page_sql
