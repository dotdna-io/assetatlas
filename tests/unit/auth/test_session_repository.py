# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for SessionRepository — uses unittest.mock for cursor."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.auth.repositories.session_repository import SessionRepository


class TestCreateSession:
    """create_session inserts a placeholder row and returns its UUID."""

    def test_inserts_session_and_returns_uuid(self, mock_conn_manager):
        cm, cursor, conn = mock_conn_manager
        new_id = uuid4()
        cursor.fetchone.return_value = {'id': str(new_id)}

        repo = SessionRepository(cm)
        user_id = uuid4()
        result = repo.create_session(
            user_id=user_id,
            ip_address='10.0.0.1',
            user_agent='test-agent/1.0',
        )

        assert result == new_id
        # Verify INSERT SQL + params
        sql, params = cursor.execute.call_args.args
        assert 'INSERT INTO user_sessions' in sql
        assert 'RETURNING id' in sql
        assert params['user_id'] == str(user_id)
        assert params['ip_address'] == '10.0.0.1'
        assert params['user_agent'] == 'test-agent/1.0'
        conn.commit.assert_called_once()

    def test_inserts_with_none_ip_and_agent(self, mock_conn_manager):
        cm, cursor, conn = mock_conn_manager
        new_id = uuid4()
        cursor.fetchone.return_value = {'id': str(new_id)}

        repo = SessionRepository(cm)
        result = repo.create_session(user_id=uuid4())

        assert result == new_id
        params = cursor.execute.call_args.args[1]
        assert params['ip_address'] is None
        assert params['user_agent'] is None
        conn.commit.assert_called_once()

    def test_db_error_propagates(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.execute.side_effect = RuntimeError('db down')
        repo = SessionRepository(cm)
        with pytest.raises(RuntimeError, match='db down'):
            repo.create_session(user_id=uuid4())


class TestStoreRefreshToken:
    """store_refresh_token UPDATEs a session with hash + jti + expiry."""

    def test_updates_with_explicit_expiry(self, mock_conn_manager):
        cm, cursor, conn = mock_conn_manager
        repo = SessionRepository(cm)
        session_id = uuid4()
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)

        repo.store_refresh_token(
            session_id=session_id,
            refresh_token_hash='hash-abc',
            access_token_jti='jti-123',
            expires_at=expires_at,
        )

        sql, params = cursor.execute.call_args.args
        assert 'UPDATE user_sessions' in sql
        assert params['session_id'] == str(session_id)
        assert params['refresh_token_hash'] == 'hash-abc'
        assert params['access_token_jti'] == 'jti-123'
        assert params['expires_at'] == expires_at
        conn.commit.assert_called_once()

    def test_defaults_expiry_via_token_service(self, mock_conn_manager, monkeypatch):
        """When expires_at is None, falls back to TokenService.calculate_token_expiration."""
        cm, cursor, conn = mock_conn_manager
        default_expiry = datetime.now(timezone.utc) + timedelta(days=7)

        from src.auth.services import token_service as ts_module
        monkeypatch.setattr(
            ts_module.TokenService,
            'calculate_token_expiration',
            staticmethod(lambda token_type: default_expiry),
        )

        repo = SessionRepository(cm)
        repo.store_refresh_token(
            session_id=uuid4(),
            refresh_token_hash='h',
            access_token_jti='j',
        )

        params = cursor.execute.call_args.args[1]
        assert params['expires_at'] == default_expiry
        conn.commit.assert_called_once()

    def test_db_error_propagates(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.execute.side_effect = RuntimeError('boom')
        repo = SessionRepository(cm)
        with pytest.raises(RuntimeError):
            repo.store_refresh_token(
                session_id=uuid4(),
                refresh_token_hash='h',
                access_token_jti='j',
                expires_at=datetime.now(timezone.utc),
            )


class TestGetSession:
    """get_session selects by id and returns dict-or-None."""

    def test_returns_dict_when_found(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        row = {
            'id': 'sid', 'user_id': 'uid', 'refresh_token_hash': 'h',
            'access_token_jti': 'j', 'expires_at': datetime.now(timezone.utc),
            'ip_address': '10.0.0.1', 'user_agent': 'ua', 'is_active': True,
            'created_at': datetime.now(timezone.utc),
            'last_used_at': datetime.now(timezone.utc),
        }
        cursor.fetchone.return_value = row

        repo = SessionRepository(cm)
        sid = uuid4()
        result = repo.get_session(sid)

        assert result == row
        sql, params = cursor.execute.call_args.args
        assert 'SELECT' in sql and 'FROM user_sessions' in sql
        assert 'WHERE id = %(session_id)s' in sql
        assert params['session_id'] == str(sid)

    def test_returns_none_when_not_found(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.fetchone.return_value = None
        repo = SessionRepository(cm)
        assert repo.get_session(uuid4()) is None

    def test_db_error_propagates(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.execute.side_effect = RuntimeError('x')
        repo = SessionRepository(cm)
        with pytest.raises(RuntimeError):
            repo.get_session(uuid4())


class TestGetSessionByRefreshTokenHash:
    """get_session_by_refresh_token_hash filters by hash AND is_active=true."""

    def test_returns_dict_when_found(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        row = {'id': 'sid', 'user_id': 'uid', 'refresh_token_hash': 'h',
               'access_token_jti': 'j', 'is_active': True}
        cursor.fetchone.return_value = row

        repo = SessionRepository(cm)
        result = repo.get_session_by_refresh_token_hash('hash-xyz')

        assert result == row
        sql, params = cursor.execute.call_args.args
        assert 'refresh_token_hash = %(refresh_token_hash)s' in sql
        assert 'is_active = true' in sql
        assert params['refresh_token_hash'] == 'hash-xyz'

    def test_returns_none_when_not_found(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.fetchone.return_value = None
        repo = SessionRepository(cm)
        assert repo.get_session_by_refresh_token_hash('miss') is None

    def test_db_error_propagates(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.execute.side_effect = RuntimeError('x')
        repo = SessionRepository(cm)
        with pytest.raises(RuntimeError):
            repo.get_session_by_refresh_token_hash('h')


class TestGetSessionByAccessJti:
    """get_session_by_access_jti is used for revocation checks."""

    def test_returns_dict_when_found(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        row = {'id': 'sid', 'user_id': 'uid', 'access_token_jti': 'jti-1',
               'is_active': True}
        cursor.fetchone.return_value = row

        repo = SessionRepository(cm)
        result = repo.get_session_by_access_jti('jti-1')

        assert result == row
        sql, params = cursor.execute.call_args.args
        assert 'access_token_jti = %(access_token_jti)s' in sql
        assert params['access_token_jti'] == 'jti-1'

    def test_returns_none_when_not_found(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.fetchone.return_value = None
        repo = SessionRepository(cm)
        assert repo.get_session_by_access_jti('miss') is None

    def test_db_error_propagates(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.execute.side_effect = RuntimeError('x')
        repo = SessionRepository(cm)
        with pytest.raises(RuntimeError):
            repo.get_session_by_access_jti('j')


class TestUpdateAccessTokenJti:
    """update_access_token_jti rotates only the JTI."""

    def test_updates_jti_and_commits(self, mock_conn_manager):
        cm, cursor, conn = mock_conn_manager
        repo = SessionRepository(cm)
        sid = uuid4()
        repo.update_access_token_jti(sid, 'new-jti')

        sql, params = cursor.execute.call_args.args
        assert 'UPDATE user_sessions' in sql
        assert 'access_token_jti = %(access_token_jti)s' in sql
        assert params['session_id'] == str(sid)
        assert params['access_token_jti'] == 'new-jti'
        conn.commit.assert_called_once()

    def test_db_error_propagates(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.execute.side_effect = RuntimeError('x')
        repo = SessionRepository(cm)
        with pytest.raises(RuntimeError):
            repo.update_access_token_jti(uuid4(), 'j')


class TestUpdateLastUsed:
    """update_last_used touches last_used_at."""

    def test_updates_and_commits(self, mock_conn_manager):
        cm, cursor, conn = mock_conn_manager
        repo = SessionRepository(cm)
        sid = uuid4()
        repo.update_last_used(sid)

        sql, params = cursor.execute.call_args.args
        assert 'UPDATE user_sessions' in sql
        assert 'last_used_at = NOW()' in sql
        assert params['session_id'] == str(sid)
        conn.commit.assert_called_once()

    def test_db_error_propagates(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.execute.side_effect = RuntimeError('x')
        repo = SessionRepository(cm)
        with pytest.raises(RuntimeError):
            repo.update_last_used(uuid4())


class TestUpdateSessionTokens:
    """update_session_tokens rotates both refresh hash + access JTI atomically."""

    def test_updates_both_tokens_and_last_used(self, mock_conn_manager):
        cm, cursor, conn = mock_conn_manager
        repo = SessionRepository(cm)
        sid = uuid4()
        repo.update_session_tokens(sid, 'new-hash', 'new-jti')

        sql, params = cursor.execute.call_args.args
        assert 'UPDATE user_sessions' in sql
        assert 'refresh_token_hash = %(refresh_token_hash)s' in sql
        assert 'access_token_jti = %(access_token_jti)s' in sql
        assert 'last_used_at = NOW()' in sql
        assert params['session_id'] == str(sid)
        assert params['refresh_token_hash'] == 'new-hash'
        assert params['access_token_jti'] == 'new-jti'
        conn.commit.assert_called_once()

    def test_db_error_propagates(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.execute.side_effect = RuntimeError('x')
        repo = SessionRepository(cm)
        with pytest.raises(RuntimeError):
            repo.update_session_tokens(uuid4(), 'h', 'j')


class TestGetSessionByIdAlias:
    """get_session_by_id is a thin alias of get_session."""

    def test_delegates_to_get_session(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        row = {'id': 'sid'}
        cursor.fetchone.return_value = row
        repo = SessionRepository(cm)
        sid = uuid4()
        assert repo.get_session_by_id(sid) == row
        # Underlying get_session ran exactly one execute
        assert cursor.execute.call_count == 1


class TestDeactivateSession:
    """deactivate_session sets is_active=false on a single session."""

    def test_deactivates_and_commits(self, mock_conn_manager):
        cm, cursor, conn = mock_conn_manager
        repo = SessionRepository(cm)
        sid = uuid4()
        repo.deactivate_session(sid)

        sql, params = cursor.execute.call_args.args
        assert 'UPDATE user_sessions' in sql
        assert 'is_active = false' in sql
        assert 'WHERE id = %(session_id)s' in sql
        assert params['session_id'] == str(sid)
        conn.commit.assert_called_once()

    def test_db_error_propagates(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.execute.side_effect = RuntimeError('x')
        repo = SessionRepository(cm)
        with pytest.raises(RuntimeError):
            repo.deactivate_session(uuid4())


class TestDeactivateAllUserSessions:
    """deactivate_all_user_sessions returns rowcount of sessions deactivated."""

    def test_returns_rowcount(self, mock_conn_manager):
        cm, cursor, conn = mock_conn_manager
        cursor.rowcount = 3
        repo = SessionRepository(cm)
        uid = uuid4()
        result = repo.deactivate_all_user_sessions(uid)

        assert result == 3
        sql, params = cursor.execute.call_args.args
        assert 'UPDATE user_sessions' in sql
        assert 'is_active = false' in sql
        assert 'user_id = %(user_id)s' in sql
        assert 'is_active = true' in sql  # only deactivate the active ones
        assert params['user_id'] == str(uid)
        conn.commit.assert_called_once()

    def test_returns_zero_when_no_active_sessions(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.rowcount = 0
        repo = SessionRepository(cm)
        assert repo.deactivate_all_user_sessions(uuid4()) == 0

    def test_db_error_propagates(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.execute.side_effect = RuntimeError('x')
        repo = SessionRepository(cm)
        with pytest.raises(RuntimeError):
            repo.deactivate_all_user_sessions(uuid4())


class TestGetUserSessions:
    """get_user_sessions returns a list of session dicts, optionally active-only."""

    def test_active_only_default_filters(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        rows = [{'id': 's1', 'is_active': True}, {'id': 's2', 'is_active': True}]
        cursor.fetchall.return_value = rows
        repo = SessionRepository(cm)
        uid = uuid4()
        result = repo.get_user_sessions(uid)

        assert result == rows
        sql, params = cursor.execute.call_args.args
        assert 'user_id = %(user_id)s' in sql
        assert 'is_active = true' in sql
        assert 'ORDER BY last_used_at DESC' in sql
        assert params['user_id'] == str(uid)

    def test_active_only_false_omits_filter(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.fetchall.return_value = []
        repo = SessionRepository(cm)
        repo.get_user_sessions(uuid4(), active_only=False)
        sql, _ = cursor.execute.call_args.args
        assert 'is_active = true' not in sql

    def test_db_error_propagates(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.execute.side_effect = RuntimeError('x')
        repo = SessionRepository(cm)
        with pytest.raises(RuntimeError):
            repo.get_user_sessions(uuid4())


class TestCleanupExpiredSessions:
    """cleanup_expired_sessions deletes by expires_at OR (inactive + stale)."""

    def test_deletes_and_returns_rowcount(self, mock_conn_manager):
        cm, cursor, conn = mock_conn_manager
        cursor.rowcount = 5
        repo = SessionRepository(cm)
        result = repo.cleanup_expired_sessions()

        assert result == 5
        sql = cursor.execute.call_args.args[0]
        assert 'DELETE FROM user_sessions' in sql
        assert 'expires_at < NOW()' in sql
        assert 'is_active = false' in sql
        conn.commit.assert_called_once()

    def test_returns_zero_when_nothing_to_clean(self, mock_conn_manager):
        cm, cursor, conn = mock_conn_manager
        cursor.rowcount = 0
        repo = SessionRepository(cm)
        assert repo.cleanup_expired_sessions() == 0
        conn.commit.assert_called_once()

    def test_db_error_propagates(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.execute.side_effect = RuntimeError('x')
        repo = SessionRepository(cm)
        with pytest.raises(RuntimeError):
            repo.cleanup_expired_sessions()


class TestGetSessionCount:
    """get_session_count supports optional user filter and active-only flag."""

    def test_default_active_only_no_user(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.fetchone.return_value = {'count': 7}
        repo = SessionRepository(cm)
        assert repo.get_session_count() == 7
        sql, params = cursor.execute.call_args.args
        assert 'COUNT(*)' in sql
        assert 'is_active = true' in sql
        assert 'user_id' not in params

    def test_with_user_filter(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.fetchone.return_value = {'count': 2}
        repo = SessionRepository(cm)
        uid = uuid4()
        assert repo.get_session_count(user_id=uid) == 2
        sql, params = cursor.execute.call_args.args
        assert 'user_id = %(user_id)s' in sql
        assert params['user_id'] == str(uid)

    def test_active_only_false_omits_filter(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.fetchone.return_value = {'count': 9}
        repo = SessionRepository(cm)
        assert repo.get_session_count(active_only=False) == 9
        sql, _ = cursor.execute.call_args.args
        assert 'is_active = true' not in sql

    def test_db_error_propagates(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.execute.side_effect = RuntimeError('x')
        repo = SessionRepository(cm)
        with pytest.raises(RuntimeError):
            repo.get_session_count()
