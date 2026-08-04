# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Invite service for admin-issued user-onboarding tokens.

Reuses the password_reset_tokens table (with token_type='invite') and
PasswordResetRepository, but with a longer default TTL (7 days) and a
distinct flow: validate → user sets their own password → token consumed.

Kept separate from PasswordResetService because the lifecycle, audit
semantics, and email template are different.
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from uuid import UUID

from src.auth.repositories.password_reset_repository import TOKEN_TYPE_INVITE
from src.auth.services.password_reset_service import PasswordResetService
from src.auth.services.password_service import PasswordService

logger = logging.getLogger(__name__)


class InviteService:
    """Service for admin-issued user-onboarding (invite) tokens."""

    DEFAULT_TTL_HOURS = 168  # 7 days

    # Sentinel prefix on `users.password_hash` for invite-only accounts.
    # Anything not in the bcrypt format (`$2a$`, `$2b$`, `$2y$`) reliably
    # fails verification — the prefix makes the row's state grep-friendly
    # for operators inspecting the table.
    UNUSABLE_HASH_PREFIX = "INVITE:"

    @staticmethod
    def make_unusable_password_hash() -> str:
        """Return a non-bcrypt password_hash for an invite-only user.

        bcrypt verification rejects anything that isn't a valid bcrypt hash,
        so the account cannot log in until the invite is consumed and a real
        bcrypt hash is written.
        """
        import secrets as _secrets
        return InviteService.UNUSABLE_HASH_PREFIX + _secrets.token_urlsafe(48)

    @staticmethod
    def create_invite_token(
        user_id: UUID,
        reset_repo,
        user_repo,
        ttl_hours: int = DEFAULT_TTL_HOURS,
    ) -> Dict[str, Any]:
        """
        Create an invite token for a user. Invalidates any prior unused invite
        token for that user first (a fresh resend supersedes the old link).

        Returns dict with `token` (plaintext, send via email or copy-link),
        `token_id`, `expires_at`, `user_email`, `username`.
        """
        user = user_repo.get_user_by_id(user_id)
        if not user:
            raise ValueError("User not found")
        if not user.get("is_active", False):
            raise ValueError("Cannot send invite to inactive account")

        # Supersede any prior unused invite token for this user.
        reset_repo.invalidate_all_user_tokens(user_id, token_type=TOKEN_TYPE_INVITE)

        plain_token = PasswordResetService.generate_reset_token()
        token_hash = PasswordResetService.hash_token(plain_token)
        expires_at = datetime.utcnow() + timedelta(hours=ttl_hours)

        token_id = reset_repo.create_reset_token(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            token_type=TOKEN_TYPE_INVITE,
        )

        logger.info(
            "Invite token created for user %s, expires %s", user_id, expires_at
        )
        return {
            "token": plain_token,
            "token_id": token_id,
            "expires_at": expires_at,
            "user_email": user.get("email"),
            "username": user.get("username"),
        }

    @staticmethod
    def validate_invite_token(
        token: str,
        reset_repo,
        user_repo,
    ) -> Dict[str, Any]:
        """
        Validate (without consuming) an invite token. Used by the wizard's
        first step to greet the user with their name before they set a password.

        Returns dict with `valid` (bool) and, when valid, `username`, `email`,
        `requires_mfa`. `requires_mfa` is always True for invited users because
        invited users are always local accounts.
        """
        token_hash = PasswordResetService.hash_token(token)
        row = reset_repo.get_reset_token_by_hash(token_hash)
        if not row or row.get("token_type") != TOKEN_TYPE_INVITE:
            return {"valid": False}

        user = user_repo.get_user_by_id(UUID(row["user_id"]))
        if not user or not user.get("is_active", False):
            return {"valid": False}

        return {
            "valid": True,
            "username": user.get("username"),
            "email": user.get("email"),
            "requires_mfa": True,
        }

    @staticmethod
    def consume_invite_token(
        token: str,
        new_password: str,
        reset_repo,
        user_repo,
    ) -> Dict[str, Any]:
        """
        Validate an invite token and set the user's password. The user account
        becomes usable for login once this completes.

        Returns dict with `success`, `user_id`, `username`.
        """
        token_hash = PasswordResetService.hash_token(token)
        row = reset_repo.get_reset_token_by_hash(token_hash)
        if not row or row.get("token_type") != TOKEN_TYPE_INVITE:
            raise ValueError("Invalid or expired invite token")

        user_id = UUID(row["user_id"])
        token_id = UUID(row["id"])
        user = user_repo.get_user_by_id(user_id)
        if not user or not user.get("is_active", False):
            raise ValueError("User no longer eligible to complete onboarding")

        strength = PasswordService.validate_password_strength(new_password)
        if not strength["valid"]:
            raise ValueError(
                f"Password does not meet requirements: {'; '.join(strength['errors'])}"
            )

        password_hash = PasswordService.hash_password(new_password)
        user_repo.update_password(user_id, password_hash)
        reset_repo.mark_token_as_used(token_id)

        logger.info(
            "Onboarding completed for user %s (%s)", user_id, user["username"]
        )
        return {
            "success": True,
            "user_id": str(user_id),
            "username": user["username"],
        }
