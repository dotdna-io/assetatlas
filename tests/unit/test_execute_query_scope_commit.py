# SPDX-License-Identifier: AGPL-3.0-or-later
from contextlib import contextmanager
from unittest.mock import MagicMock
from src.database.postgres_connection import PostgreSQLConnectionManager
from src.database.postgres_adapter import PostgreSQLAdapter


def _adapter(scope_active: bool):
    # Use a REAL connection manager (lazy pool, no DB) so execute_query exercises
    # the REAL commit_unless_scoped(); only is_scope_active() is stubbed. Mocking
    # commit_unless_scoped itself would make these assertions tautological.
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    cm = PostgreSQLConnectionManager(database_url="postgresql://u:p@localhost:1/x")
    cm.is_scope_active = MagicMock(return_value=scope_active)

    @contextmanager
    def fake_get_cursor(*a, **k):
        yield (cursor, conn)
    cm.get_cursor = fake_get_cursor
    return PostgreSQLAdapter(cm), conn


def test_execute_query_does_not_commit_inside_scope():
    adapter, conn = _adapter(scope_active=True)
    adapter.execute_query("INSERT INTO t VALUES (1)", (), fetch=False)
    conn.commit.assert_not_called()        # deferred to end_scope


def test_execute_query_commits_outside_scope():
    adapter, conn = _adapter(scope_active=False)
    adapter.execute_query("INSERT INTO t VALUES (1)", (), fetch=False)
    conn.commit.assert_called_once()       # unchanged behavior when unscoped


def _manager_with_fresh_conn():
    """Real manager whose pool internals are stubbed to hand out one mock conn."""
    cm = PostgreSQLConnectionManager(database_url="postgresql://u:p@localhost:1/x")
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    cm._get_connection_with_retry = MagicMock(return_value=conn)
    cm._release_connection = MagicMock()
    return cm, conn, cursor


def test_execute_autonomous_commits_even_inside_scope():
    # The live pipeline stage write must be visible immediately even though the
    # orchestrator runs the whole stage loop inside a scope (#800 regression):
    # a fresh autocommit connection bypasses commit_unless_scoped deferral.
    cm, conn, cursor = _manager_with_fresh_conn()
    cm.is_scope_active = MagicMock(return_value=True)  # scope IS active
    seen = {}
    cursor.execute.side_effect = lambda *a, **k: seen.update(autocommit=conn.autocommit)

    cm.execute_autonomous(
        "UPDATE pipeline_runs SET current_stage = %s WHERE id = %s",
        ("Scoring", "run-1"),
    )

    assert seen["autocommit"] is True  # the write ran in autocommit => committed immediately
    cursor.execute.assert_called_once_with(
        "UPDATE pipeline_runs SET current_stage = %s WHERE id = %s",
        ("Scoring", "run-1"),
    )
    cm._get_connection_with_retry.assert_called_once()  # a FRESH conn, not the scope's
    cm._release_connection.assert_called_once_with(conn)


def test_execute_autonomous_resets_autocommit_before_release():
    # The pooled connection must not be returned to the pool in autocommit mode.
    cm, conn, _ = _manager_with_fresh_conn()
    cm.execute_autonomous("UPDATE x SET y = 1", ())
    assert conn.autocommit is False


def test_execute_autonomous_releases_connection_on_error():
    cm, conn, cursor = _manager_with_fresh_conn()
    cursor.execute.side_effect = RuntimeError("boom")

    try:
        cm.execute_autonomous("UPDATE x SET y = 1", ())
    except RuntimeError:
        pass

    cm._release_connection.assert_called_once_with(conn)  # no leak on failure
