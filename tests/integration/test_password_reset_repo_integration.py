# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Integration tests for PasswordResetRepository.

Round-trips reset and invite tokens through the real database to verify
that token_type is persisted and returned by get_reset_token_by_hash.
Wrapped in SAVEPOINT/ROLLBACK so no data persists.
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest

from src.auth.repositories.password_reset_repository import PasswordResetRepository
from src.auth.repositories.user_repository import UserRepository


# ---------------------------------------------------------------------------
# Helper: thin connection-manager wrapper around a raw psycopg2 connection
# ---------------------------------------------------------------------------

class _SavepointConnManager:
    """Mimics PostgreSQLConnectionManager for repositories that expect one."""

    def __init__(self, conn):
        self._conn = conn

    def begin_scope(self):
        pass

    def end_scope(self, error=None):
        pass

    def is_scope_active(self):
        return True

    @contextmanager
    def get_connection(self, autocommit=False):
        yield self._conn

    @contextmanager
    def get_cursor(self, cursor_factory=None, autocommit=False):
        if cursor_factory is not None:
            cur = self._conn.cursor(cursor_factory=cursor_factory)
        else:
            cur = self._conn.cursor()
        try:
            yield cur, self._conn
        finally:
            cur.close()


@pytest.fixture()
def conn_manager(db_transaction):
    """Wrap the savepointed connection in a mock connection manager."""
    return _SavepointConnManager(db_transaction)


@pytest.fixture()
def user_repo(conn_manager):
    return UserRepository(conn_manager)


@pytest.fixture()
def reset_repo(conn_manager):
    return PasswordResetRepository(conn_manager)


def _unique_username():
    return f"testuser_{uuid.uuid4().hex[:8]}"


def _unique_email():
    return f"test_{uuid.uuid4().hex[:8]}@example.com"


def _create_user(user_repo):
    """Insert a user (FK target for password_reset_tokens) and return its UUID."""
    user = user_repo.create_user(
        username=_unique_username(),
        email=_unique_email(),
        password_hash="$2b$12$fakehashforintegrationtest000000000000000000000000",
    )
    return uuid.UUID(str(user["id"]))


# =====================================================================
# Round-trip tests
# =====================================================================

@pytest.mark.integration
def test_get_reset_token_by_hash_round_trips_invite_type(user_repo, reset_repo):
    """An invite token must come back with token_type='invite' so
    InviteService.validate_invite_token can discriminate it."""
    user_id = _create_user(user_repo)
    token_hash = "a" * 64

    reset_repo.create_reset_token(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=datetime.utcnow() + timedelta(days=7),
        token_type="invite",
    )

    row = reset_repo.get_reset_token_by_hash(token_hash)

    assert row is not None
    assert row["token_type"] == "invite"
    assert str(row["user_id"]) == str(user_id)


@pytest.mark.integration
def test_get_reset_token_by_hash_round_trips_default_reset_type(user_repo, reset_repo):
    """The default token_type='reset' must round-trip too — confirms the
    column is selected for both flows."""
    user_id = _create_user(user_repo)
    token_hash = "b" * 64

    reset_repo.create_reset_token(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )

    row = reset_repo.get_reset_token_by_hash(token_hash)

    assert row is not None
    assert row["token_type"] == "reset"
    assert str(row["user_id"]) == str(user_id)
