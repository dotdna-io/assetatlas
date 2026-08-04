# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Password reset service.

Handles password reset token generation, validation, and password updates.
"""
import secrets
import hashlib
from typing import Dict, Optional
from uuid import UUID
from datetime import datetime, timedelta
import logging

from src.auth.services.password_service import PasswordService

logger = logging.getLogger(__name__)


class PasswordResetService:
    """Service for handling password reset operations."""

    # Token configuration
    TOKEN_LENGTH = 32  # 32 bytes = 256 bits of entropy
    TOKEN_EXPIRATION_HOURS = 1
    MAX_RESET_ATTEMPTS_PER_HOUR = 3  # Rate limiting

    @staticmethod
    def generate_reset_token() -> str:
        """
        Generate a cryptographically secure random token.

        Returns:
            URL-safe token string (64 characters hex)
        """
        return secrets.token_hex(PasswordResetService.TOKEN_LENGTH)

    @staticmethod
    def hash_token(token: str) -> str:
        """
        Hash a reset token using SHA-256.

        Args:
            token: Plain text reset token

        Returns:
            Hexadecimal hash of the token
        """
        return hashlib.sha256(token.encode('utf-8')).hexdigest()

    @staticmethod
    def create_reset_token(
        user_id: UUID,
        reset_repo,
        user_repo
    ) -> Dict[str, any]:
        """
        Create a password reset token for a user.

        This method:
        1. Validates the user exists and is active
        2. Invalidates any existing unused tokens for the user
        3. Generates a new secure token
        4. Stores the hashed token in the database

        Args:
            user_id: User UUID
            reset_repo: Password reset repository instance
            user_repo: User repository instance

        Returns:
            Dictionary with:
            - token: Plain text token (send to user via email)
            - token_id: Database token UUID
            - expires_at: Expiration timestamp

        Raises:
            ValueError: If user not found or inactive
        """
        # Verify user exists and is active
        user = user_repo.get_user_by_id(user_id)
        if not user:
            raise ValueError("User not found")

        if not user.get('is_active', False):
            raise ValueError("Cannot reset password for inactive account")

        # Invalidate any existing unused tokens for this user
        reset_repo.invalidate_all_user_tokens(user_id)

        # Generate new token
        plain_token = PasswordResetService.generate_reset_token()
        token_hash = PasswordResetService.hash_token(plain_token)

        # Calculate expiration
        expires_at = datetime.utcnow() + timedelta(
            hours=PasswordResetService.TOKEN_EXPIRATION_HOURS
        )

        # Store hashed token
        token_id = reset_repo.create_reset_token(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at
        )

        logger.info(f"Password reset token created for user {user_id}, expires at {expires_at}")

        return {
            'token': plain_token,  # Send this to user via email
            'token_id': token_id,
            'expires_at': expires_at,
            'user_email': user.get('email'),
            'username': user.get('username')
        }

    @staticmethod
    def validate_and_use_token(
        token: str,
        new_password: str,
        reset_repo,
        user_repo,
        session_repo
    ) -> Dict[str, any]:
        """
        Validate reset token and update user password.

        This method:
        1. Validates the token exists, is unused, and not expired
        2. Updates the user's password
        3. Marks the token as used
        4. Revokes all user sessions (force re-login)

        Args:
            token: Plain text reset token from user
            new_password: New password to set
            reset_repo: Password reset repository instance
            user_repo: User repository instance
            session_repo: Session repository instance

        Returns:
            Dictionary with:
            - success: True if reset successful
            - user_id: User UUID
            - username: Username

        Raises:
            ValueError: If token invalid, expired, or password validation fails
        """
        # Hash the token to look up in database
        token_hash = PasswordResetService.hash_token(token)

        # Get token from database (only returns valid, unused, non-expired tokens)
        reset_token = reset_repo.get_reset_token_by_hash(token_hash)

        if not reset_token:
            logger.warning(f"Invalid or expired password reset token attempted")
            raise ValueError("Invalid or expired reset token")

        user_id = UUID(reset_token['user_id'])
        token_id = UUID(reset_token['id'])

        # Get user
        user = user_repo.get_user_by_id(user_id)
        if not user:
            logger.error(f"User {user_id} not found during password reset")
            raise ValueError("User not found")

        if not user.get('is_active', False):
            logger.warning(f"Password reset attempted for inactive user {user_id}")
            raise ValueError("Cannot reset password for inactive account")

        # Validate new password
        strength = PasswordService.validate_password_strength(new_password)
        if not strength['valid']:
            raise ValueError(f"Password does not meet requirements: {'; '.join(strength['errors'])}")

        # Hash new password
        password_hash = PasswordService.hash_password(new_password)

        # Update password (update_password also clears must_change_password flag)
        user_repo.update_password(user_id, password_hash)

        # Mark token as used
        reset_repo.mark_token_as_used(token_id)

        # Revoke all user sessions (force re-login with new password)
        sessions_revoked = session_repo.deactivate_all_user_sessions(user_id)

        # Drop cached jwt_user record so old sessions stop validating against
        # the pre-reset state for the cache TTL (60s). Inlined rather than
        # importing a blueprint helper to avoid a service->blueprint cycle.
        try:
            from src.api.utils.caching import cache
            cache.delete(f"jwt_user:{user_id}")
        except Exception as e:
            # Cache failures must not block a successful password reset.
            logger.warning(f"Cache invalidation failed for user {user_id}: {e}")

        logger.info(
            f"Password reset successful for user {user_id} ({user['username']}), "
            f"{sessions_revoked} sessions revoked"
        )

        return {
            'success': True,
            'user_id': str(user_id),
            'username': user['username'],
            'sessions_revoked': sessions_revoked
        }

    @staticmethod
    def check_rate_limit(user_id: UUID, reset_repo) -> bool:
        """
        Check if user has exceeded password reset rate limit.

        Args:
            user_id: User UUID
            reset_repo: Password reset repository instance

        Returns:
            True if user is within rate limit, False if exceeded
        """
        # Get count of active tokens created in last hour
        # This is a simple rate limit - in production you might want
        # a more sophisticated approach using Redis or similar
        active_count = reset_repo.get_user_active_tokens_count(user_id)

        return active_count < PasswordResetService.MAX_RESET_ATTEMPTS_PER_HOUR
