# SPDX-License-Identifier: AGPL-3.0-or-later
"""Integration tests for ChatRepository against a live PostgreSQL.

Requires DATABASE_URL to point at a DB where migration 074 (chat tables) has
been applied. Each test creates its own throwaway user (chat_sessions.user_id
has a FK to users.id) and removes it — alongside the cascaded chat_* rows —
in teardown.

Marked `integration` so they're skipped by default; run with `pytest -m integration`.
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def chat_adapter():
    """Construct a PostgreSQLAdapter backed by DATABASE_URL.

    Skips the module if:
      - DATABASE_URL is not set,
      - the DB is not reachable,
      - migration 074 has not been applied (no chat_sessions table).

    The chat_sessions probe below is the authoritative gate — a DB without
    migration 074 skips cleanly, so no separate localhost gate is needed.
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL not set — skipping chat repository integration tests")

    import psycopg2

    try:
        probe = psycopg2.connect(db_url)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"Cannot connect to database: {exc}")

    try:
        with probe.cursor() as cur:
            cur.execute("SELECT to_regclass('public.chat_sessions')")
            if cur.fetchone()[0] is None:
                pytest.skip(
                    "Migration 074 not applied (chat_sessions table missing)"
                )
    finally:
        probe.close()

    from src.database.postgres_connection import PostgreSQLConnectionManager
    from src.database.postgres_adapter import PostgreSQLAdapter

    cm = PostgreSQLConnectionManager(
        database_url=db_url, min_connections=1, max_connections=2,
    )
    yield PostgreSQLAdapter(connection_manager=cm)
    cm.close_all_connections()


@pytest.fixture
def temp_user(chat_adapter) -> Iterator[str]:
    """Create a throwaway user row and yield its id; delete on teardown.

    Cascades remove any chat_* rows keyed on the user (chat_sessions.user_id
    and chat_feedback.user_id both `ON DELETE CASCADE`).
    """
    user_id = str(uuid.uuid4())
    suffix = user_id.replace("-", "")[:12]
    chat_adapter.execute_query(
        """
        INSERT INTO users (id, username, email, password_hash, role)
        VALUES (%(id)s::uuid, %(u)s, %(e)s, %(p)s, 'user')
        """,
        {
            "id": user_id,
            "u": f"chat-it-{suffix}",
            "e": f"chat-it-{suffix}@test.local",
            "p": "x" * 60,
        },
        fetch=False,
    )
    try:
        yield user_id
    finally:
        chat_adapter.execute_query(
            "DELETE FROM users WHERE id = %(id)s::uuid",
            {"id": user_id},
            fetch=False,
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_session_roundtrip_and_list(chat_adapter, temp_user):
    """create_session -> load_session -> list_sessions returns our row."""
    from src.api.blueprints.chat.repository import ChatRepository, SessionRow

    repo = ChatRepository(chat_adapter)
    created = repo.create_session(
        user_id=temp_user, title="it-test", model="claude-sonnet-4-5",
        provider="anthropic",
    )
    assert isinstance(created, SessionRow)
    assert created.title == "it-test"

    loaded = repo.load_session(created.id, user_id=temp_user)
    assert loaded is not None
    assert loaded.id == created.id
    assert loaded.provider == "anthropic"

    listing = repo.list_sessions(user_id=temp_user)
    assert listing.total >= 1
    assert any(s.id == created.id for s in listing.sessions)


def test_append_messages_preserves_seq_order(chat_adapter, temp_user):
    """Two append_message calls produce seq=1 then seq=2, loaded in order."""
    from src.api.blueprints.chat.repository import ChatRepository

    repo = ChatRepository(chat_adapter)
    session = repo.create_session(
        user_id=temp_user, title="seq-test", model="claude-sonnet-4-5",
        provider="anthropic",
    )

    m1 = repo.append_message(session.id, role="user", content="hello")
    m2 = repo.append_message(session.id, role="assistant", content="hi back")

    assert m1.seq == 1
    assert m2.seq == 2

    msgs = repo.load_messages(session.id)
    assert [m.seq for m in msgs] == [1, 2]
    assert msgs[0].role == "user"
    assert msgs[1].role == "assistant"


def test_delete_session_soft_deletes(chat_adapter, temp_user):
    """delete_session flips deleted_at; load_session then returns None."""
    from src.api.blueprints.chat.repository import ChatRepository

    repo = ChatRepository(chat_adapter)
    session = repo.create_session(
        user_id=temp_user, title="delete-me", model="claude-sonnet-4-5",
        provider="anthropic",
    )
    assert repo.load_session(session.id, user_id=temp_user) is not None

    assert repo.delete_session(session.id, user_id=temp_user) is True
    assert repo.load_session(session.id, user_id=temp_user) is None

    # Second delete is a no-op (row already soft-deleted).
    assert repo.delete_session(session.id, user_id=temp_user) is False


def test_upsert_feedback_is_idempotent(chat_adapter, temp_user):
    """Second upsert_feedback for (message_id, user_id) updates the existing row."""
    from src.api.blueprints.chat.repository import ChatRepository

    repo = ChatRepository(chat_adapter)
    session = repo.create_session(
        user_id=temp_user, title="fb-test", model="claude-sonnet-4-5",
        provider="anthropic",
    )
    msg = repo.append_message(session.id, role="assistant", content="the answer")

    repo.upsert_feedback(
        message_id=msg.id, user_id=temp_user, rating=1, comment="great",
    )
    first = repo.list_feedback_for_message(msg.id)
    assert len(first) == 1
    assert first[0].rating == 1
    assert first[0].comment == "great"

    repo.upsert_feedback(
        message_id=msg.id, user_id=temp_user, rating=-1, comment="actually no",
    )
    second = repo.list_feedback_for_message(msg.id)
    assert len(second) == 1  # still one row, not two
    assert second[0].rating == -1
    assert second[0].comment == "actually no"


def test_record_tool_call_persists_and_computes_bytes(chat_adapter, temp_user):
    """record_tool_call stores arguments as JSONB and writes result_bytes."""
    from src.api.blueprints.chat.repository import ChatRepository

    repo = ChatRepository(chat_adapter)
    session = repo.create_session(
        user_id=temp_user, title="tool-test", model="claude-sonnet-4-5",
        provider="anthropic",
    )
    msg = repo.append_message(session.id, role="assistant", content=None,
                              tool_calls=[{"id": "t1", "name": "list_assets"}])

    tc = repo.record_tool_call(
        message_id=msg.id,
        tool_name="list_assets",
        arguments={"filter": "vendor=acme"},
        result="rows: 3",
        status="ok",
        duration_ms=42,
    )
    assert tc.tool_name == "list_assets"
    assert tc.status == "ok"

    # Verify what landed in the DB — result_bytes, arguments JSONB.
    rows = chat_adapter.execute_query(
        "SELECT tool_name, arguments, result, result_bytes, status, duration_ms "
        "FROM chat_tool_calls WHERE id = %(id)s::uuid",
        {"id": tc.id},
    )
    assert rows and len(rows) == 1
    row = rows[0]
    assert row["tool_name"] == "list_assets"
    assert row["arguments"] == {"filter": "vendor=acme"}
    assert row["result"] == "rows: 3"
    assert row["result_bytes"] == len("rows: 3".encode("utf-8"))
    assert row["status"] == "ok"
    assert row["duration_ms"] == 42


def test_record_tool_call_artifact_jsonb_roundtrip(chat_adapter, temp_user):
    """Chart envelopes survive an INSERT + SELECT + load_tool_calls_by_message
    roundtrip with psycopg2 JSONB (de)serialisation intact. Locks the wire
    shape the frontend renders from."""
    from src.api.blueprints.chat.repository import ChatRepository

    repo = ChatRepository(chat_adapter)
    session = repo.create_session(
        user_id=temp_user, title="artifact-test",
        model="claude-sonnet-4-5", provider="anthropic",
    )
    msg = repo.append_message(
        session.id, role="assistant", content="Let me check…",
        tool_calls=[{"id": "t1", "name": "aggregate_assets"}],
    )

    artifact = {
        "type": "donut",
        "title": "Assets by Category",
        "series": [
            {"label": "server", "value": 5},
            {"label": "workstation", "value": 3},
        ],
        "meta": {
            "source_tool": "aggregate_assets",
            "total": 8,
            "other_count": 0,
            "row_field": "category",
        },
    }

    tc = repo.record_tool_call(
        message_id=msg.id,
        tool_name="aggregate_assets",
        arguments={"row_field": "category"},
        result="COUNT: 8 assets match the filter.",
        status="ok",
        duration_ms=17,
        artifact=artifact,
    )
    assert tc.artifact == artifact

    # Read via the history path the route uses on reload.
    grouped = repo.load_tool_calls_by_message(session.id)
    assert msg.id in grouped
    [loaded] = grouped[msg.id]
    assert loaded.artifact == artifact

    # A tool call with no artifact persists NULL, not {}, so the frontend
    # can do a simple truthy check.
    tc2 = repo.record_tool_call(
        message_id=msg.id,
        tool_name="search_assets",
        arguments={"query": "host"},
        result="found 3",
        status="ok",
    )
    assert tc2.artifact is None
    null_row = chat_adapter.execute_query(
        "SELECT artifact FROM chat_tool_calls WHERE id = %(id)s::uuid",
        {"id": tc2.id},
    )[0]
    assert null_row["artifact"] is None
