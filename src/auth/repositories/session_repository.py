# SPDX-License-Identifier: AGPL-3.0-or-later
"""
User session database repository.

Handles session management for JWT authentication including refresh token
storage, session tracking, and cleanup.
"""
from typing import Optional, List, Dict
from datetime import datetime
from uuid import UUID
import logging

from psycopg2 import extras

logger = logging.getLogger(__name__)


class SessionRepository:
    """Handles all session database operations."""

    def __init__(self, conn_manager):
        """
        Initialize session repository.

        Args:
            conn_manager: PostgreSQL connection manager instance
        """
        self.conn_manager = conn_manager

    def create_session(
        self,
        user_id: UUID,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> UUID:
        """
        Create new session (without tokens initially).

        Args:
            user_id: User UUID
            ip_address: Client IP address
            user_agent: Client user agent string

        Returns:
            Session UUID
        """
        query = """
            INSERT INTO user_sessions (user_id, refresh_token_hash, access_token_jti, expires_at, ip_address, user_agent)
            VALUES (%(user_id)s, 'pending', 'pending', NOW() + INTERVAL '7 days', %(ip_address)s, %(user_agent)s)
            RETURNING id
        """

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, {
                    'user_id': str(user_id),
                    'ip_address': ip_address,
                    'user_agent': user_agent
                })
                result = cursor.fetchone()
                session_id = result['id']
                conn.commit()
                logger.debug(f"Session created: {session_id} for user {user_id}")
                return UUID(session_id)

        except Exception as e:
            logger.error(f"Error creating session for user {user_id}: {e}")
            raise

    def store_refresh_token(
        self,
        session_id: UUID,
        refresh_token_hash: str,
        access_token_jti: str,
        expires_at: Optional[datetime] = None
    ) -> None:
        """
        Store refresh token hash and access token JTI in session.

        Args:
            session_id: Session UUID
            refresh_token_hash: SHA-256 hash of refresh token
            access_token_jti: JWT ID of current access token
            expires_at: Session expiration (defaults to 7 days from now)
        """
        query = """
            UPDATE user_sessions
            SET refresh_token_hash = %(refresh_token_hash)s,
                access_token_jti = %(access_token_jti)s,
                expires_at = %(expires_at)s
            WHERE id = %(session_id)s
        """

        if not expires_at:
            from src.auth.services.token_service import TokenService
            expires_at = TokenService.calculate_token_expiration('refresh')

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, {
                    'session_id': str(session_id),
                    'refresh_token_hash': refresh_token_hash,
                    'access_token_jti': access_token_jti,
                    'expires_at': expires_at
                })
                conn.commit()
                logger.debug(f"Stored refresh token for session {session_id}")

        except Exception as e:
            logger.error(f"Error storing refresh token for session {session_id}: {e}")
            raise

    def get_session(self, session_id: UUID) -> Optional[Dict]:
        """
        Get session by ID.

        Args:
            session_id: Session UUID

        Returns:
            Session dictionary if found, None otherwise
        """
        query = """
            SELECT id, user_id, refresh_token_hash, access_token_jti,
                   expires_at, ip_address, user_agent, is_active,
                   created_at, last_used_at
            FROM user_sessions
            WHERE id = %(session_id)s
        """

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, {'session_id': str(session_id)})
                result = cursor.fetchone()
                return dict(result) if result else None

        except Exception as e:
            logger.error(f"Error getting session {session_id}: {e}")
            raise

    def get_session_by_refresh_token_hash(self, refresh_token_hash: str) -> Optional[Dict]:
        """
        Get session by refresh token hash.

        Args:
            refresh_token_hash: SHA-256 hash of refresh token

        Returns:
            Session dictionary if found, None otherwise
        """
        query = """
            SELECT id, user_id, refresh_token_hash, access_token_jti,
                   expires_at, ip_address, user_agent, is_active,
                   created_at, last_used_at
            FROM user_sessions
            WHERE refresh_token_hash = %(refresh_token_hash)s
              AND is_active = true
        """

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, {'refresh_token_hash': refresh_token_hash})
                result = cursor.fetchone()
                return dict(result) if result else None

        except Exception as e:
            logger.error(f"Error getting session by refresh token hash: {e}")
            raise

    def get_session_by_access_jti(self, access_token_jti: str) -> Optional[Dict]:
        """
        Get session by access token JTI.

        Used for token revocation checking - if session is not found or inactive,
        the access token is considered revoked.

        Args:
            access_token_jti: JWT ID (jti) of access token

        Returns:
            Session dictionary if found, None otherwise
        """
        query = """
            SELECT id, user_id, refresh_token_hash, access_token_jti,
                   expires_at, ip_address, user_agent, is_active,
                   created_at, last_used_at
            FROM user_sessions
            WHERE access_token_jti = %(access_token_jti)s
        """

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, {'access_token_jti': access_token_jti})
                result = cursor.fetchone()
                return dict(result) if result else None

        except Exception as e:
            logger.error(f"Error getting session by access token JTI: {e}")
            raise

    def update_access_token_jti(self, session_id: UUID, access_token_jti: str) -> None:
        """
        Update access token JTI after token refresh.

        Args:
            session_id: Session UUID
            access_token_jti: New access token JTI
        """
        query = """
            UPDATE user_sessions
            SET access_token_jti = %(access_token_jti)s
            WHERE id = %(session_id)s
        """

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, {
                    'session_id': str(session_id),
                    'access_token_jti': access_token_jti
                })
                conn.commit()
                logger.debug(f"Updated access token JTI for session {session_id}")

        except Exception as e:
            logger.error(f"Error updating access token JTI for session {session_id}: {e}")
            raise

    def update_last_used(self, session_id: UUID) -> None:
        """
        Update session last_used_at timestamp.

        Args:
            session_id: Session UUID
        """
        query = """
            UPDATE user_sessions
            SET last_used_at = NOW()
            WHERE id = %(session_id)s
        """

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, {'session_id': str(session_id)})
                conn.commit()

        except Exception as e:
            logger.error(f"Error updating last_used for session {session_id}: {e}")
            raise

    def update_session_tokens(
        self,
        session_id: UUID,
        refresh_token_hash: str,
        access_token_jti: str
    ) -> None:
        """
        Update session with new token information after token refresh.

        This is used when rotating tokens - both refresh and access tokens are replaced.

        Args:
            session_id: Session UUID
            refresh_token_hash: New hashed refresh token
            access_token_jti: New access token JTI
        """
        query = """
            UPDATE user_sessions
            SET
                refresh_token_hash = %(refresh_token_hash)s,
                access_token_jti = %(access_token_jti)s,
                last_used_at = NOW()
            WHERE id = %(session_id)s
        """

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, {
                    'session_id': str(session_id),
                    'refresh_token_hash': refresh_token_hash,
                    'access_token_jti': access_token_jti
                })
                conn.commit()
                logger.debug(f"Updated tokens for session {session_id}")

        except Exception as e:
            logger.error(f"Error updating tokens for session {session_id}: {e}")
            raise

    def get_session_by_id(self, session_id: UUID) -> Optional[Dict]:
        """
        Get session by ID (alias for get_session for clarity).

        Args:
            session_id: Session UUID

        Returns:
            Session dict or None if not found
        """
        return self.get_session(session_id)

    def deactivate_session(self, session_id: UUID) -> None:
        """
        Deactivate a session (logout).

        Args:
            session_id: Session UUID
        """
        query = """
            UPDATE user_sessions
            SET is_active = false
            WHERE id = %(session_id)s
        """

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, {'session_id': str(session_id)})
                conn.commit()
                logger.info(f"Session {session_id} deactivated")

        except Exception as e:
            logger.error(f"Error deactivating session {session_id}: {e}")
            raise

    def deactivate_all_user_sessions(self, user_id: UUID) -> int:
        """
        Deactivate all sessions for a user.

        Useful for password reset, account compromise, or forced logout.

        Args:
            user_id: User UUID

        Returns:
            Number of sessions deactivated
        """
        query = """
            UPDATE user_sessions
            SET is_active = false
            WHERE user_id = %(user_id)s
              AND is_active = true
        """

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, {'user_id': str(user_id)})
                count = cursor.rowcount
                conn.commit()
                logger.info(f"Deactivated {count} sessions for user {user_id}")
                return count

        except Exception as e:
            logger.error(f"Error deactivating all sessions for user {user_id}: {e}")
            raise

    def get_user_sessions(
        self,
        user_id: UUID,
        active_only: bool = True
    ) -> List[Dict]:
        """
        Get all sessions for a user.

        Args:
            user_id: User UUID
            active_only: Only return active sessions (default: True)

        Returns:
            List of session dictionaries
        """
        query = """
            SELECT id, user_id, expires_at, ip_address, user_agent,
                   is_active, created_at, last_used_at
            FROM user_sessions
            WHERE user_id = %(user_id)s
        """

        if active_only:
            query += " AND is_active = true"

        query += " ORDER BY last_used_at DESC"

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, {'user_id': str(user_id)})
                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            logger.error(f"Error getting sessions for user {user_id}: {e}")
            raise

    def cleanup_expired_sessions(self) -> int:
        """
        Delete expired sessions from database.

        Sessions are considered expired if:
        - expires_at is in the past, OR
        - Not active and last_used_at is more than 30 days ago

        Returns:
            Number of sessions deleted
        """
        query = """
            DELETE FROM user_sessions
            WHERE expires_at < NOW()
               OR (is_active = false AND last_used_at < NOW() - INTERVAL '30 days')
        """

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query)
                count = cursor.rowcount
                conn.commit()

                if count > 0:
                    logger.info(f"Cleaned up {count} expired sessions")
                else:
                    logger.debug("No expired sessions to clean up")

                return count

        except Exception as e:
            logger.error(f"Error cleaning up expired sessions: {e}")
            raise

    def get_session_count(self, user_id: Optional[UUID] = None, active_only: bool = True) -> int:
        """
        Get session count (optionally filtered by user).

        Args:
            user_id: Optional user UUID filter
            active_only: Only count active sessions (default: True)

        Returns:
            Session count
        """
        query = "SELECT COUNT(*) as count FROM user_sessions WHERE 1=1"
        params = {}

        if user_id:
            query += " AND user_id = %(user_id)s"
            params['user_id'] = str(user_id)

        if active_only:
            query += " AND is_active = true"

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, params)
                result = cursor.fetchone()
                return result['count']

        except Exception as e:
            logger.error(f"Error getting session count: {e}")
            raise
