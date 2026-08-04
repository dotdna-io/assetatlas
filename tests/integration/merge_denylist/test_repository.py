# SPDX-License-Identifier: AGPL-3.0-or-later
"""Integration tests for MergeDenylistRepository against a live PostgreSQL.

Requires DATABASE_URL to point at a DB where migration 131 (merge_denylist
table) has been applied. Marked `integration` so it's skipped by default;
run with `pytest -m integration`.
"""

from __future__ import annotations

import os

import pytest

from src.api.middleware.error_handler import ConflictError
from src.modules.merge_denylist.repository import MergeDenylistRepository

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def db_adapter():
    """Construct a PostgreSQLAdapter backed by DATABASE_URL.

    Skips the module if:
      - DATABASE_URL is not set,
      - it points at the shared dev DB (192.0.2.10) — migration 131 has not
        been applied there, and this suite must never be the thing that
        mutates that DB (mirrors tests/integration/conftest.py::grace_db),
      - the DB is not reachable,
      - migration 131 has not been applied (no merge_denylist table). This
        probe is the authoritative gate, same idiom as
        tests/integration/chat/test_repository_integration.py::chat_adapter.
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip(
            "DATABASE_URL not set — skipping merge_denylist repository integration tests"
        )
    if "192.0.2.10" in db_url:
        pytest.skip(
            "refusing to run against the shared dev DB (migration 131 not applied there)"
        )

    import psycopg2

    try:
        probe = psycopg2.connect(db_url)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"Cannot connect to database: {exc}")

    try:
        with probe.cursor() as cur:
            cur.execute("SELECT to_regclass('public.merge_denylist')")
            if cur.fetchone()[0] is None:
                pytest.skip(
                    "Migration 131 not applied (merge_denylist table missing)"
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_create_list_delete_roundtrip(db_adapter):
    repo = MergeDenylistRepository(db_adapter)
    row = repo.create(identifier_type="hostname", identifier_value="web01",
                       raw_value="web01", reason="reused name", created_by="tester")
    assert row.identifier_value == "web01"
    assert row.id in {r.id for r in repo.list_all()}
    assert "web01" in repo.load_enabled_components()
    assert repo.delete(row.id) is True
    assert repo.delete(row.id) is False  # already gone


def test_duplicate_raises_conflict(db_adapter):
    repo = MergeDenylistRepository(db_adapter)
    row = repo.create(identifier_type="mac", identifier_value="bc:24:11:22:33:44",
                       raw_value="BC:24:11:22:33:44", reason=None, created_by="t")
    try:
        with pytest.raises(ConflictError):
            repo.create(identifier_type="mac", identifier_value="bc:24:11:22:33:44",
                        raw_value="BC:24:11:22:33:44", reason=None, created_by="t")
    finally:
        repo.delete(row.id)  # keep the table clean for the next local/CI run


def test_create_idempotent_no_raise_on_duplicate(db_adapter):
    """create_idempotent() must never raise on a duplicate (identifier_type,
    identifier_value) -- contrast with create() above, which raises
    ConflictError. This is the mechanic the asset-split fix relies on:
    PostgreSQLConnectionManager.get_connection() marks a request-scope
    transaction 'errored' as soon as ANY exception propagates out of a DB
    call, before a repository's own try/except around psycopg2.IntegrityError
    even runs -- so catching and re-raising as ConflictError is already too
    late to save the scope. ON CONFLICT DO NOTHING avoids raising at all.
    """
    repo = MergeDenylistRepository(db_adapter)
    cm = db_adapter.conn_manager
    row = repo.create(identifier_type="serial", identifier_value="sn:split-idem-dup",
                       raw_value="SPLIT-IDEM-DUP", reason=None, created_by="t")
    new_row = None
    try:
        cm.begin_scope()
        dup = repo.create_idempotent(
            identifier_type="serial", identifier_value="sn:split-idem-dup",
            raw_value="SPLIT-IDEM-DUP", reason=None, created_by="t",
        )
        # A further write in the SAME scope, right after the no-op, proves the
        # no-op did not poison it -- a poisoned scope rolls back everything at
        # end_scope() instead (see
        # tests/integration/test_scope_rollback.py::test_scope_rolls_back_all_writes_on_late_failure).
        new_row = repo.create_idempotent(
            identifier_type="serial", identifier_value="sn:split-idem-new",
            raw_value="SPLIT-IDEM-NEW", reason=None, created_by="t",
        )
        cm.end_scope()

        assert dup is None  # no-op on the duplicate; must not raise
        assert new_row is not None  # a new distinct value still inserts normally
        assert new_row.identifier_value == "sn:split-idem-new"
        # Visible after the scope ended -> it committed, wasn't rolled back.
        assert new_row.id in {r.id for r in repo.list_all()}
    finally:
        if cm.is_scope_active():
            cm.end_scope(error=True)  # defensive: never leak scope depth on failure
        repo.delete(row.id)
        if new_row is not None:
            repo.delete(new_row.id)
