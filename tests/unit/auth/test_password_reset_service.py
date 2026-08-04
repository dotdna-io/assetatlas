# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for PasswordResetService.

Focused on the side effects of validate_and_use_token — primarily the cache
invalidation path that drops the cached jwt_user record so old sessions stop
validating against the pre-reset state inside the cache TTL.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.auth.services.password_reset_service import PasswordResetService


def _make_repos(user_id, token_id, username="alice"):
    """Build the three repository mocks validate_and_use_token needs."""
    reset_repo = MagicMock()
    reset_repo.get_reset_token_by_hash.return_value = {
        "id": str(token_id),
        "user_id": str(user_id),
        "token_hash": "h" * 64,
        "expires_at": datetime.utcnow() + timedelta(hours=1),
        "created_at": datetime.utcnow(),
        "used_at": None,
    }

    user_repo = MagicMock()
    user_repo.get_user_by_id.return_value = {
        "id": str(user_id),
        "username": username,
        "email": f"{username}@test.com",
        "is_active": True,
    }

    session_repo = MagicMock()
    session_repo.deactivate_all_user_sessions.return_value = 1

    return reset_repo, user_repo, session_repo


def test_validate_and_use_token_invalidates_jwt_user_cache():
    """After a successful password reset, the jwt_user:<id> cache must be
    dropped so old sessions stop validating against the pre-reset state."""
    user_id = uuid4()
    token_id = uuid4()
    reset_repo, user_repo, session_repo = _make_repos(user_id, token_id)

    with patch("src.auth.services.password_service.PasswordService.validate_password_strength",
               return_value={"valid": True, "errors": []}), \
         patch("src.auth.services.password_service.PasswordService.hash_password",
               return_value="$2b$12$fakehash"), \
         patch("src.api.utils.caching.cache") as mock_cache:
        result = PasswordResetService.validate_and_use_token(
            token="plain-token",
            new_password="NewStr0ngPassw0rd!",
            reset_repo=reset_repo,
            user_repo=user_repo,
            session_repo=session_repo,
        )

    assert result["success"] is True
    mock_cache.delete.assert_any_call(f"jwt_user:{user_id}")


def test_validate_and_use_token_swallows_cache_errors():
    """Cache failures must not block a successful password reset — the
    password update has already committed at this point."""
    user_id = uuid4()
    token_id = uuid4()
    reset_repo, user_repo, session_repo = _make_repos(user_id, token_id)

    with patch("src.auth.services.password_service.PasswordService.validate_password_strength",
               return_value={"valid": True, "errors": []}), \
         patch("src.auth.services.password_service.PasswordService.hash_password",
               return_value="$2b$12$fakehash"), \
         patch("src.api.utils.caching.cache") as mock_cache:
        mock_cache.delete.side_effect = RuntimeError("redis down")

        # Must not raise
        result = PasswordResetService.validate_and_use_token(
            token="plain-token",
            new_password="NewStr0ngPassw0rd!",
            reset_repo=reset_repo,
            user_repo=user_repo,
            session_repo=session_repo,
        )

    assert result["success"] is True
