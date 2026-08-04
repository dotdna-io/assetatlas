# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Integration tests for the authentication data layer.

Tests UserRepository and SessionRepository against the real database,
wrapped in SAVEPOINT/ROLLBACK so no data persists.
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from psycopg2 import extras

from src.auth.repositories.user_repository import UserRepository
from src.auth.repositories.session_repository import SessionRepository


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
def session_repo(conn_manager):
    return SessionRepository(conn_manager)


def _unique_username():
    """Generate a unique username to avoid collisions."""
    return f"testuser_{uuid.uuid4().hex[:8]}"


def _unique_email():
    """Generate a unique email to avoid collisions."""
    return f"test_{uuid.uuid4().hex[:8]}@example.com"


# =====================================================================
# User Repository Tests
# =====================================================================

@pytest.mark.integration
class TestUserRepository:
    """Tests for UserRepository against the real database."""

    def test_create_and_get_user_round_trip(self, user_repo):
        """create_user() -> get_user_by_id() should return the same user."""
        username = _unique_username()
        email = _unique_email()

        created = user_repo.create_user(
            username=username,
            email=email,
            password_hash="$2b$12$fakehashforintegrationtest000000000000000000000000",
            full_name="Integration Test User",
            role="user",
        )

        assert created["username"] == username
        assert created["email"] == email
        assert created["role"] == "user"
        assert "id" in created

        # Read back
        fetched = user_repo.get_user_by_id(uuid.UUID(str(created["id"])))
        assert fetched is not None
        assert fetched["username"] == username
        assert fetched["full_name"] == "Integration Test User"

    def test_duplicate_username_rejected(self, user_repo):
        """Creating two users with the same username should raise ValueError."""
        username = _unique_username()
        email1 = _unique_email()
        email2 = _unique_email()

        user_repo.create_user(
            username=username,
            email=email1,
            password_hash="$2b$12$fakehashforintegrationtest000000000000000000000000",
        )

        with pytest.raises(ValueError, match="already exists"):
            user_repo.create_user(
                username=username,
                email=email2,
                password_hash="$2b$12$fakehashforintegrationtest000000000000000000000000",
            )

    def test_duplicate_email_rejected(self, user_repo):
        """Creating two users with the same email should raise ValueError."""
        email = _unique_email()

        user_repo.create_user(
            username=_unique_username(),
            email=email,
            password_hash="$2b$12$fakehashforintegrationtest000000000000000000000000",
        )

        with pytest.raises(ValueError, match="already exists"):
            user_repo.create_user(
                username=_unique_username(),
                email=email,
                password_hash="$2b$12$fakehashforintegrationtest000000000000000000000000",
            )

    def test_get_user_by_username(self, user_repo):
        """get_user_by_username() should find user case-insensitively."""
        username = _unique_username()

        user_repo.create_user(
            username=username,
            email=_unique_email(),
            password_hash="$2b$12$fakehashforintegrationtest000000000000000000000000",
        )

        fetched = user_repo.get_user_by_username(username.upper())
        assert fetched is not None
        assert fetched["username"] == username

    def test_get_nonexistent_user_returns_none(self, user_repo):
        """get_user_by_id() with a random UUID should return None."""
        result = user_repo.get_user_by_id(uuid.uuid4())
        assert result is None


# =====================================================================
# Session Repository Tests
# =====================================================================

@pytest.mark.integration
class TestSessionRepository:
    """Tests for SessionRepository against the real database."""

    def test_create_session_round_trip(self, user_repo, session_repo):
        """create_session() -> get_session() should return session data."""
        # First create a user (sessions have FK to users)
        user = user_repo.create_user(
            username=_unique_username(),
            email=_unique_email(),
            password_hash="$2b$12$fakehashforintegrationtest000000000000000000000000",
        )
        user_id = uuid.UUID(str(user["id"]))

        session_id = session_repo.create_session(
            user_id=user_id,
            ip_address="127.0.0.1",
            user_agent="IntegrationTest/1.0",
        )

        assert session_id is not None
        assert isinstance(session_id, uuid.UUID)

        # Read back
        session = session_repo.get_session(session_id)
        assert session is not None
        assert str(session["user_id"]) == str(user_id)
        assert session["ip_address"] == "127.0.0.1"
        assert session["is_active"] is True

    def test_store_refresh_token(self, user_repo, session_repo):
        """store_refresh_token() should update the session row."""
        user = user_repo.create_user(
            username=_unique_username(),
            email=_unique_email(),
            password_hash="$2b$12$fakehashforintegrationtest000000000000000000000000",
        )
        user_id = uuid.UUID(str(user["id"]))

        session_id = session_repo.create_session(user_id=user_id)

        expires = datetime.now(timezone.utc) + timedelta(days=7)
        session_repo.store_refresh_token(
            session_id=session_id,
            refresh_token_hash="sha256_fake_hash_for_test",
            access_token_jti="test-jti-12345",
            expires_at=expires,
        )

        session = session_repo.get_session(session_id)
        assert session["refresh_token_hash"] == "sha256_fake_hash_for_test"
        assert session["access_token_jti"] == "test-jti-12345"

    def test_deactivate_session(self, user_repo, session_repo):
        """deactivate_session() should set is_active=False."""
        user = user_repo.create_user(
            username=_unique_username(),
            email=_unique_email(),
            password_hash="$2b$12$fakehashforintegrationtest000000000000000000000000",
        )
        user_id = uuid.UUID(str(user["id"]))

        session_id = session_repo.create_session(user_id=user_id)
        session_repo.deactivate_session(session_id)

        session = session_repo.get_session(session_id)
        assert session["is_active"] is False

    def test_get_nonexistent_session_returns_none(self, session_repo):
        """get_session() with random UUID should return None."""
        result = session_repo.get_session(uuid.uuid4())
        assert result is None
