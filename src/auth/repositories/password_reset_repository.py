# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Password reset token database repository.

Handles password reset token creation, validation, and cleanup.
"""
from typing import Optional, Dict
from datetime import datetime, timedelta
from uuid import UUID
import logging

from psycopg2 import extras

logger = logging.getLogger(__name__)

# Token-type discriminator values — must mirror the CHECK constraint in migration 085.
TOKEN_TYPE_RESET = "reset"
TOKEN_TYPE_INVITE = "invite"
ALLOWED_TOKEN_TYPES = (TOKEN_TYPE_RESET, TOKEN_TYPE_INVITE)


class PasswordResetRepository:
    """Handles all password reset token database operations."""

    def __init__(self, conn_manager):
        """
        Initialize password reset repository.

        Args:
            conn_manager: PostgreSQL connection manager instance
        """
        self.conn_manager = conn_manager

    _ALLOWED_TOKEN_TYPES = ALLOWED_TOKEN_TYPES

    def create_reset_token(
        self,
        user_id: UUID,
        token_hash: str,
        expires_at: Optional[datetime] = None,
        token_type: str = "reset",
    ) -> UUID:
        """
        Create a new password reset / invite token.

        Args:
            user_id: User UUID.
            token_hash: SHA-256 hash of the token.
            expires_at: Token expiration. Defaults to 1 hour from now.
            token_type: 'reset' (default, password reset) or 'invite' (onboarding).

        Returns:
            Token UUID.
        """
        if token_type not in self._ALLOWED_TOKEN_TYPES:
            raise ValueError(
                f"token_type must be one of {self._ALLOWED_TOKEN_TYPES}, got {token_type!r}"
            )

        if not expires_at:
            expires_at = datetime.utcnow() + timedelta(hours=1)

        query = """
            INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, token_type)
            VALUES (%(user_id)s, %(token_hash)s, %(expires_at)s, %(token_type)s)
            RETURNING id
        """

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, {
                    'user_id': str(user_id),
                    'token_hash': token_hash,
                    'expires_at': expires_at,
                    'token_type': token_type,
                })
                result = cursor.fetchone()
                token_id = result['id']
                conn.commit()
                logger.debug(f"Reset token created: {token_id} for user {user_id} (type={token_type})")
                return UUID(token_id)

        except Exception as e:
            logger.error(f"Error creating reset token for user {user_id}: {e}")
            raise

    def get_reset_token_by_hash(self, token_hash: str) -> Optional[Dict]:
        """
        Get password reset token by hash.

        Args:
            token_hash: SHA-256 hash of reset token

        Returns:
            Token dictionary if found and valid, None otherwise
        """
        query = """
            SELECT id, user_id, token_hash, expires_at, created_at, used_at, token_type
            FROM password_reset_tokens
            WHERE token_hash = %(token_hash)s
              AND used_at IS NULL
              AND expires_at > NOW()
        """

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, {'token_hash': token_hash})
                result = cursor.fetchone()
                return dict(result) if result else None

        except Exception as e:
            logger.error(f"Error getting password reset token by hash: {e}")
            raise

    def mark_token_as_used(self, token_id: UUID) -> None:
        """
        Mark password reset token as used.

        Args:
            token_id: Reset token UUID
        """
        query = """
            UPDATE password_reset_tokens
            SET used_at = NOW()
            WHERE id = %(token_id)s
        """

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, {'token_id': str(token_id)})
                conn.commit()
                logger.debug(f"Password reset token {token_id} marked as used")

        except Exception as e:
            logger.error(f"Error marking password reset token {token_id} as used: {e}")
            raise

    def invalidate_all_user_tokens(
        self,
        user_id: UUID,
        token_type: Optional[str] = None,
    ) -> int:
        """
        Invalidate all unused tokens for a user.

        Args:
            user_id: User UUID.
            token_type: Optional filter — invalidate only tokens of this type
                ('reset' or 'invite'). If None, invalidate all types.

        Returns:
            Number of tokens invalidated.
        """
        if token_type is not None and token_type not in self._ALLOWED_TOKEN_TYPES:
            raise ValueError(
                f"token_type must be one of {self._ALLOWED_TOKEN_TYPES}, got {token_type!r}"
            )

        params = {'user_id': str(user_id)}
        query = """
            UPDATE password_reset_tokens
            SET used_at = NOW()
            WHERE user_id = %(user_id)s
              AND used_at IS NULL
        """
        if token_type is not None:
            query += " AND token_type = %(token_type)s"
            params['token_type'] = token_type

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query, params)
                count = cursor.rowcount
                conn.commit()
                logger.debug(
                    f"Invalidated {count} reset tokens for user {user_id} "
                    f"(type={token_type or 'all'})"
                )
                return count

        except Exception as e:
            logger.error(f"Error invalidating reset tokens for user {user_id}: {e}")
            raise

    def cleanup_expired_tokens(self) -> int:
        """
        Delete expired or used password reset tokens.

        Tokens are deleted if:
        - Expired (expires_at < NOW()), OR
        - Used more than 7 days ago

        Returns:
            Number of tokens deleted
        """
        query = """
            DELETE FROM password_reset_tokens
            WHERE expires_at < NOW()
               OR (used_at IS NOT NULL AND used_at < NOW() - INTERVAL '7 days')
        """

        try:
            with self.conn_manager.get_cursor() as (cursor, conn):
                cursor.execute(query)
                count = cursor.rowcount
                conn.commit()

                if count > 0:
                    logger.info(f"Cleaned up {count} expired/used password reset tokens")
                else:
                    logger.debug("No expired password reset tokens to clean up")

                return count

        except Exception as e:
            logger.error(f"Error cleaning up password reset tokens: {e}")
            raise

    def get_user_active_tokens_count(self, user_id: UUID) -> int:
        """
        Get count of active (unused, non-expired) reset tokens for a user.

        Args:
            user_id: User UUID

        Returns:
            Count of active tokens
        """
        query = """
            SELECT COUNT(*) as count
            FROM password_reset_tokens
            WHERE user_id = %(user_id)s
              AND used_at IS NULL
              AND expires_at > NOW()
        """

        try:
            with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(query, {'user_id': str(user_id)})
                result = cursor.fetchone()
                return result['count']

        except Exception as e:
            logger.error(f"Error getting active token count for user {user_id}: {e}")
            raise
