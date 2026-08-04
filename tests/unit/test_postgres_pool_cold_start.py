# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Cold-start retry behaviour for `PostgreSQLConnectionManager._create_pool`.

Regression: on a fresh `compose up`, postgres briefly closes its TCP
listener between initdb finishing and accepting clients. All four
gunicorn workers then race to create their pools and hit "Connection
refused", exhausting the per-request retry budget. The api logs a wall
of ERRORs before things eventually settle.

The fix retries transient connect errors during pool creation only.
Hard errors (auth failure, missing db) still raise immediately.
"""
import threading
from unittest.mock import patch

import psycopg2
import pytest


def _make_manager():
    """Build a manager bypassing __init__ (which would call _create_pool)."""
    from src.database.postgres_connection import PostgreSQLConnectionManager

    mgr = PostgreSQLConnectionManager.__new__(PostgreSQLConnectionManager)
    mgr.database_url = "postgresql://test:test@localhost:5432/test"
    mgr.min_connections = 2
    mgr.max_connections = 5
    mgr.max_retries = 3
    mgr.retry_delay = 0.01
    mgr._pool = None
    mgr._pool_lock = threading.Lock()
    mgr._scope_state = threading.local()
    return mgr


def test_create_pool_retries_until_postgres_accepts_connections():
    """Pool create succeeds after N transient `Connection refused`s."""
    mgr = _make_manager()
    fake_pool = object()
    fail_then_succeed = [
        psycopg2.OperationalError(
            'connection to server at "timescaledb" (172.18.0.9), port 5432 '
            'failed: Connection refused'
        ),
        psycopg2.OperationalError("could not connect to server: Connection refused"),
        fake_pool,
    ]

    def _fake_pool_ctor(*args, **kwargs):
        result = fail_then_succeed.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    with patch("src.database.postgres_connection.pool.ThreadedConnectionPool", side_effect=_fake_pool_ctor), \
         patch("src.database.postgres_connection.time.sleep"):
        result = mgr._create_pool()

    assert result is fake_pool
    assert fail_then_succeed == []


def test_create_pool_raises_immediately_on_non_transient_error():
    """Auth failures must not be retried — they will never succeed."""
    mgr = _make_manager()
    auth_err = psycopg2.OperationalError(
        'FATAL:  password authentication failed for user "assetatlas"'
    )
    call_count = {"n": 0}

    def _always_auth_fail(*_args, **_kwargs):
        call_count["n"] += 1
        raise auth_err

    with patch("src.database.postgres_connection.pool.ThreadedConnectionPool", side_effect=_always_auth_fail), \
         patch("src.database.postgres_connection.time.sleep"):
        with pytest.raises(psycopg2.OperationalError):
            mgr._create_pool()

    assert call_count["n"] == 1, "auth failure should not be retried"


def test_create_pool_gives_up_after_max_attempts():
    """Persistent transient failure raises after exhausting the budget."""
    mgr = _make_manager()
    refused = psycopg2.OperationalError(
        'connection to server at "timescaledb" (172.18.0.9), port 5432 '
        'failed: Connection refused'
    )
    call_count = {"n": 0}

    def _always_refuse(*_args, **_kwargs):
        call_count["n"] += 1
        raise refused

    with patch("src.database.postgres_connection.pool.ThreadedConnectionPool", side_effect=_always_refuse), \
         patch("src.database.postgres_connection.time.sleep"):
        with pytest.raises(psycopg2.OperationalError):
            mgr._create_pool()

    from src.database.postgres_connection import PostgreSQLConnectionManager
    assert call_count["n"] == PostgreSQLConnectionManager._POOL_CREATE_MAX_ATTEMPTS


@pytest.mark.parametrize("msg,expected", [
    ("connection to server at \"db\" port 5432 failed: Connection refused", True),
    ("could not connect to server: Connection refused", True),
    ("could not translate host name \"db\" to address", True),
    ("server closed the connection unexpectedly", True),
    ("FATAL:  password authentication failed for user \"x\"", False),
    ("FATAL:  database \"x\" does not exist", False),
    ("invalid dsn: missing \"=\" after \"foo\"", False),
])
def test_is_transient_connect_error_classification(msg, expected):
    from src.database.postgres_connection import PostgreSQLConnectionManager
    err = psycopg2.OperationalError(msg)
    assert PostgreSQLConnectionManager._is_transient_connect_error(err) is expected
