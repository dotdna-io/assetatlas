# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from unittest.mock import MagicMock
from src.database.postgres_connection import PostgreSQLConnectionManager


def _mgr():
    # Lazy pool — no DB touched. Patch release so we never hit a real pool.
    m = PostgreSQLConnectionManager(database_url="postgresql://u:p@localhost:1/x")
    m._release_connection = MagicMock()
    return m


def test_outermost_end_scope_commits_once_on_success():
    m = _mgr()
    conn = MagicMock()
    m.begin_scope()                       # depth 1
    m.begin_scope()                       # depth 2 (nested)
    m._scope_state.connection = conn      # simulate the lazily-acquired conn
    m.end_scope()                         # inner closes -> NO finalize yet
    conn.commit.assert_not_called()
    assert m.is_scope_active() is True
    m.end_scope()                         # outermost closes -> commit once + release
    conn.commit.assert_called_once()
    conn.rollback.assert_not_called()
    m._release_connection.assert_called_once_with(conn)
    assert m.is_scope_active() is False


def test_end_scope_with_error_rolls_back_and_does_not_commit():
    m = _mgr()
    conn = MagicMock()
    m.begin_scope()
    m._scope_state.connection = conn
    m.end_scope(error=RuntimeError("boom"))
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()
    m._release_connection.assert_called_once_with(conn)


def test_inner_error_rolls_back_the_whole_scope():
    # A nested stage failing must abort the OUTER transaction, not let it commit.
    m = _mgr()
    conn = MagicMock()
    m.begin_scope()                       # outer
    m.begin_scope()                       # inner
    m._scope_state.connection = conn
    m.end_scope(error=RuntimeError("inner failed"))   # inner closes, marks errored
    m.end_scope()                         # outer closes WITHOUT error arg...
    conn.rollback.assert_called_once()    # ...but still rolls back (errored)
    conn.commit.assert_not_called()


def test_end_scope_without_active_scope_is_noop():
    m = _mgr()
    m.end_scope()                         # must not raise
    m.end_scope(error=RuntimeError("x"))  # must not raise


def test_failure_inside_scope_forces_rollback_even_if_not_reported():
    # A query failing inside an active scope must poison the whole scope so the
    # outermost end_scope rolls back — even when the caller swallows the error
    # and does NOT pass it to end_scope (e.g. a handler that logs-and-continues).
    m = _mgr()
    conn = MagicMock()
    m._get_connection_with_retry = MagicMock(return_value=conn)
    m.begin_scope()
    try:
        with m.get_connection():
            raise RuntimeError("query blew up inside the scope")
    except RuntimeError:
        pass
    m.end_scope()                         # error=None, but the scope was poisoned
    conn.commit.assert_not_called()       # must NOT commit a torn-down transaction
    conn.rollback.assert_called()         # rolled back instead


def test_end_scope_propagates_commit_failure_on_success_path():
    # The outermost commit is the run's ONLY commit; if it fails the work is
    # lost, so the failure MUST propagate rather than be swallowed as a false
    # success — while the connection is still released and the scope torn down.
    m = _mgr()
    conn = MagicMock()
    conn.commit.side_effect = RuntimeError("commit failed")
    m.begin_scope()
    m._scope_state.connection = conn
    with pytest.raises(RuntimeError, match="commit failed"):
        m.end_scope()
    conn.commit.assert_called_once()
    m._release_connection.assert_called_once_with(conn)
    assert m.is_scope_active() is False


def test_scope_connection_disables_idle_in_transaction_timeout():
    # The whole pipeline runs as ONE long transaction (the scope). A CPU-bound
    # stage (e.g. UnionFind id_assignment, ~56s on real production data) can sit far
    # longer than the pool's 30s idle_in_transaction_session_timeout without
    # issuing SQL — Postgres would then terminate the connection mid-run and the
    # next query raises "connection already closed". So the scope connection must
    # disable that guard the moment it's acquired.
    m = _mgr()
    conn = MagicMock()
    conn.closed = False
    m._get_connection_with_retry = MagicMock(return_value=conn)
    m.begin_scope()
    with m.get_connection():
        pass
    cur = conn.cursor.return_value.__enter__.return_value
    executed = [c.args[0] for c in cur.execute.call_args_list if c.args]
    assert any(
        "idle_in_transaction_session_timeout = 0" in s for s in executed
    ), executed


def test_end_scope_restores_idle_in_transaction_timeout():
    # The scope connection returns to a pool that also serves ordinary API
    # requests, which DO want the 30s guard. So the outermost end_scope must
    # restore the default before releasing the connection.
    m = _mgr()
    conn = MagicMock()
    conn.closed = False
    m._get_connection_with_retry = MagicMock(return_value=conn)
    m.begin_scope()
    with m.get_connection():
        pass
    m.end_scope()
    cur = conn.cursor.return_value.__enter__.return_value
    executed = [c.args[0] for c in cur.execute.call_args_list if c.args]
    assert any(
        "idle_in_transaction_session_timeout = 30000" in s for s in executed
    ), executed


def test_end_scope_raises_on_aborted_txn_only_when_flag_set():
    # #813: a caught, non-critical stage error can leave the shared txn ABORTED
    # without marking the scope errored (the exception never propagated through
    # get_connection().__exit__). The success-path commit must NOT be issued —
    # on an aborted txn Postgres turns COMMIT into ROLLBACK, silently discarding
    # the ENTIRE run while it reports success. With raise_on_aborted=True (the
    # pipeline write scope) end_scope rolls back AND raises so the run fails.
    import psycopg2.extensions as ext
    m = _mgr()
    conn = MagicMock()
    conn.info.transaction_status = ext.TRANSACTION_STATUS_INERROR
    m.begin_scope()
    m._scope_state.connection = conn
    with pytest.raises(Exception):
        m.end_scope(raise_on_aborted=True)   # error=None, but the txn is aborted
    conn.commit.assert_not_called()       # must NOT commit-as-rollback silently
    conn.rollback.assert_called_once()    # rolled back explicitly instead
    m._release_connection.assert_called_once_with(conn)
    assert m.is_scope_active() is False    # scope still torn down


def test_end_scope_aborted_txn_default_rolls_back_without_raising():
    # Request-scope callers (HTTP teardown, chat) use the default
    # raise_on_aborted=False: an aborted txn must still be rolled back (never
    # commit-as-rollback), but end_scope must NOT raise — a raise inside Flask
    # teardown_request would turn a successful response into a 500.
    import psycopg2.extensions as ext
    m = _mgr()
    conn = MagicMock()
    conn.info.transaction_status = ext.TRANSACTION_STATUS_INERROR
    m.begin_scope()
    m._scope_state.connection = conn
    m.end_scope()                          # default flag: must NOT raise
    conn.commit.assert_not_called()
    conn.rollback.assert_called_once()
    m._release_connection.assert_called_once_with(conn)
    assert m.is_scope_active() is False


def test_end_scope_commits_when_transaction_status_clean():
    # Guard against false positives: a healthy in-transaction connection on the
    # success path must still commit exactly once (no spurious rollback/raise).
    import psycopg2.extensions as ext
    m = _mgr()
    conn = MagicMock()
    conn.info.transaction_status = ext.TRANSACTION_STATUS_INTRANS
    m.begin_scope()
    m._scope_state.connection = conn
    m.end_scope()
    conn.commit.assert_called_once()
    conn.rollback.assert_not_called()


def test_get_connection_refuses_autocommit_flip_inside_scope():
    # Flipping session autocommit on the shared scope connection would force a
    # premature commit/rollback of its in-flight transaction; refuse it instead.
    m = _mgr()
    conn = MagicMock()
    conn.autocommit = False
    m._get_connection_with_retry = MagicMock(return_value=conn)
    m.begin_scope()
    with m.get_connection():              # caches the connection (autocommit=False)
        pass
    with pytest.raises(RuntimeError):
        with m.get_connection(autocommit=True):   # mismatch -> must be refused
            pass
    conn.commit.assert_not_called()       # scope's transaction was not committed
