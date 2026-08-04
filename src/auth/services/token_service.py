# SPDX-License-Identifier: AGPL-3.0-or-later
"""
JWT token generation and validation service.

Provides secure token generation with JWT, refresh token management,
and password reset token generation.
"""
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Dict, Optional


class TokenService:
    """Handles JWT access and refresh token operations."""

    # Token expiration times
    ACCESS_TOKEN_EXPIRES = timedelta(minutes=60)
    REFRESH_TOKEN_EXPIRES = timedelta(days=7)
    PASSWORD_RESET_TOKEN_EXPIRES = timedelta(hours=1)

    @staticmethod
    def generate_token_pair(
        user_id: str,
        username: str,
        email: str,
        role: str,
        session_id: str
    ) -> Dict[str, str]:
        """
        Generate access and refresh token pair.

        Note: Actual JWT encoding will be handled by Flask-JWT-Extended
        in the API layer. This service prepares the claims and generates
        the necessary identifiers.

        Args:
            user_id: User UUID
            username: Username
            email: Email address
            role: User role (user/admin/super_admin)
            session_id: Session UUID

        Returns:
            Dictionary with token metadata:
            {
                'access_token_jti': str (JWT ID for blacklist tracking),
                'user_claims': dict (claims to include in JWT)
            }
        """
        # Generate unique JTI for access token (for revocation tracking)
        access_jti = secrets.token_urlsafe(32)

        # Claims to be included in JWT
        user_claims = {
            'username': username,
            'email': email,
            'role': role,
            'session_id': session_id,
            'jti': access_jti
        }

        return {
            'access_token_jti': access_jti,
            'user_claims': user_claims
        }

    @staticmethod
    def generate_refresh_token() -> str:
        """
        Generate cryptographically secure refresh token.

        Returns:
            URL-safe base64 random string (256 bits of entropy)
        """
        return secrets.token_urlsafe(32)

    @staticmethod
    def hash_refresh_token(refresh_token: str) -> str:
        """
        Hash refresh token for secure storage in database.

        Uses SHA-256 for hashing. Refresh tokens should never be
        stored in plaintext.

        Args:
            refresh_token: Raw refresh token

        Returns:
            Hexadecimal SHA-256 hash

        Raises:
            ValueError: If refresh_token is empty
        """
        if not refresh_token:
            raise ValueError("Refresh token cannot be empty")

        return hashlib.sha256(refresh_token.encode('utf-8')).hexdigest()

    @staticmethod
    def verify_refresh_token_hash(refresh_token: str, stored_hash: str) -> bool:
        """
        Verify refresh token against stored hash.

        Args:
            refresh_token: Raw refresh token to verify
            stored_hash: SHA-256 hash from database

        Returns:
            True if token matches hash, False otherwise
        """
        if not refresh_token or not stored_hash:
            return False

        try:
            token_hash = TokenService.hash_refresh_token(refresh_token)
            return secrets.compare_digest(token_hash, stored_hash)
        except Exception:
            return False

    @staticmethod
    def generate_password_reset_token() -> str:
        """
        Generate secure random token for password reset.

        Returns:
            URL-safe base64 random string (256 bits of entropy)
        """
        return secrets.token_urlsafe(32)

    @staticmethod
    def hash_password_reset_token(token: str) -> str:
        """
        Hash password reset token for secure storage in database.

        Uses SHA-256 for hashing. Reset tokens should never be
        stored in plaintext.

        Args:
            token: Raw reset token

        Returns:
            Hexadecimal SHA-256 hash

        Raises:
            ValueError: If token is empty
        """
        if not token:
            raise ValueError("Reset token cannot be empty")

        return hashlib.sha256(token.encode('utf-8')).hexdigest()

    @staticmethod
    def verify_password_reset_token(token: str, stored_hash: str) -> bool:
        """
        Verify password reset token against stored hash.

        Args:
            token: Raw reset token to verify
            stored_hash: SHA-256 hash from database

        Returns:
            True if token matches hash, False otherwise
        """
        if not token or not stored_hash:
            return False

        try:
            token_hash = TokenService.hash_password_reset_token(token)
            return secrets.compare_digest(token_hash, stored_hash)
        except Exception:
            return False

    @staticmethod
    def generate_jti() -> str:
        """
        Generate unique JWT ID (jti) for token revocation tracking.

        Returns:
            URL-safe base64 random string (256 bits of entropy)
        """
        return secrets.token_urlsafe(32)

    @staticmethod
    def is_token_expired(expires_at: datetime) -> bool:
        """
        Check if a token has expired based on expiration timestamp.

        Args:
            expires_at: Expiration timestamp (timezone-aware)

        Returns:
            True if token has expired, False otherwise
        """
        if not expires_at:
            return True

        # Ensure we're comparing timezone-aware datetimes
        now = datetime.now(expires_at.tzinfo) if expires_at.tzinfo else datetime.utcnow()
        return now >= expires_at

    @staticmethod
    def calculate_token_expiration(
        token_type: str = 'access'
    ) -> datetime:
        """
        Calculate expiration datetime for a given token type.

        Args:
            token_type: Type of token ('access', 'refresh', 'password_reset')

        Returns:
            Expiration datetime (UTC)

        Raises:
            ValueError: If token_type is invalid
        """
        now = datetime.utcnow()

        if token_type == 'access':
            return now + TokenService.ACCESS_TOKEN_EXPIRES
        elif token_type == 'refresh':
            return now + TokenService.REFRESH_TOKEN_EXPIRES
        elif token_type == 'password_reset':
            return now + TokenService.PASSWORD_RESET_TOKEN_EXPIRES
        else:
            raise ValueError(f"Invalid token type: {token_type}")

    @staticmethod
    def get_token_expiration_seconds(token_type: str = 'access') -> int:
        """
        Get token expiration time in seconds.

        Args:
            token_type: Type of token ('access', 'refresh', 'password_reset')

        Returns:
            Expiration time in seconds

        Raises:
            ValueError: If token_type is invalid
        """
        if token_type == 'access':
            return int(TokenService.ACCESS_TOKEN_EXPIRES.total_seconds())
        elif token_type == 'refresh':
            return int(TokenService.REFRESH_TOKEN_EXPIRES.total_seconds())
        elif token_type == 'password_reset':
            return int(TokenService.PASSWORD_RESET_TOKEN_EXPIRES.total_seconds())
        else:
            raise ValueError(f"Invalid token type: {token_type}")
