# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for InviteService."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from src.auth.services.invite_service import InviteService


def _make_repo_with_user(user_id, *, is_active=True, email="alice@example.com",
                         username="alice"):
    user_repo = MagicMock()
    user_repo.get_user_by_id.return_value = {
        "id": str(user_id), "email": email, "username": username,
        "is_active": is_active,
    }
    reset_repo = MagicMock()
    reset_repo.create_reset_token.return_value = uuid4()
    return user_repo, reset_repo


def test_create_invite_token_default_ttl_168_hours():
    user_id = uuid4()
    user_repo, reset_repo = _make_repo_with_user(user_id)

    result = InviteService.create_invite_token(user_id, reset_repo, user_repo)

    # Repository called with token_type='invite' and ~168h expiration
    _, kwargs = reset_repo.create_reset_token.call_args
    assert kwargs["token_type"] == "invite"
    delta = kwargs["expires_at"] - datetime.utcnow()
    assert timedelta(hours=167) < delta < timedelta(hours=169)
    # Returns the plaintext token + user info (for the email template)
    assert "token" in result
    assert result["user_email"] == "alice@example.com"
    assert result["username"] == "alice"


def test_create_invite_token_custom_ttl_hours():
    user_id = uuid4()
    user_repo, reset_repo = _make_repo_with_user(user_id)

    InviteService.create_invite_token(user_id, reset_repo, user_repo, ttl_hours=24)

    _, kwargs = reset_repo.create_reset_token.call_args
    delta = kwargs["expires_at"] - datetime.utcnow()
    assert timedelta(hours=23) < delta < timedelta(hours=25)


def test_create_invite_token_invalidates_prior_invite_tokens():
    user_id = uuid4()
    user_repo, reset_repo = _make_repo_with_user(user_id)

    InviteService.create_invite_token(user_id, reset_repo, user_repo)

    reset_repo.invalidate_all_user_tokens.assert_called_once_with(
        user_id, token_type="invite"
    )


def test_create_invite_token_rejects_inactive_user():
    user_id = uuid4()
    user_repo, reset_repo = _make_repo_with_user(user_id, is_active=False)

    with pytest.raises(ValueError, match="inactive"):
        InviteService.create_invite_token(user_id, reset_repo, user_repo)


def test_create_invite_token_raises_when_user_missing():
    user_repo = MagicMock()
    user_repo.get_user_by_id.return_value = None
    reset_repo = MagicMock()

    with pytest.raises(ValueError, match="not found"):
        InviteService.create_invite_token(uuid4(), reset_repo, user_repo)


def test_validate_invite_token_returns_user_when_valid():
    reset_repo = MagicMock()
    user_repo = MagicMock()
    reset_repo.get_reset_token_by_hash.return_value = {
        "id": str(uuid4()), "user_id": str(uuid4()), "token_type": "invite",
    }
    user_repo.get_user_by_id.return_value = {
        "id": "x", "username": "alice", "email": "alice@example.com",
        "is_active": True, "role": "user",
    }

    result = InviteService.validate_invite_token("rawtoken", reset_repo, user_repo)

    assert result["valid"] is True
    assert result["username"] == "alice"
    assert result["email"] == "alice@example.com"
    assert result["requires_mfa"] is True  # Always True — invited users are always local


def test_validate_invite_token_returns_invalid_when_token_missing():
    reset_repo = MagicMock()
    user_repo = MagicMock()
    reset_repo.get_reset_token_by_hash.return_value = None

    result = InviteService.validate_invite_token("rawtoken", reset_repo, user_repo)

    assert result["valid"] is False


def test_validate_invite_token_rejects_reset_type_token():
    reset_repo = MagicMock()
    user_repo = MagicMock()
    reset_repo.get_reset_token_by_hash.return_value = {
        "id": str(uuid4()), "user_id": str(uuid4()), "token_type": "reset",
    }

    result = InviteService.validate_invite_token("rawtoken", reset_repo, user_repo)

    assert result["valid"] is False


def test_consume_invite_token_sets_password_and_marks_used():
    reset_repo = MagicMock()
    user_repo = MagicMock()
    user_id = uuid4()
    token_id = uuid4()
    reset_repo.get_reset_token_by_hash.return_value = {
        "id": str(token_id), "user_id": str(user_id), "token_type": "invite",
    }
    user_repo.get_user_by_id.return_value = {
        "id": str(user_id), "username": "alice", "is_active": True,
    }

    result = InviteService.consume_invite_token(
        "rawtoken", "NewSecurePassword123!", reset_repo, user_repo
    )

    user_repo.update_password.assert_called_once()
    reset_repo.mark_token_as_used.assert_called_once_with(token_id)
    assert result["success"] is True
    assert result["username"] == "alice"


def test_consume_invite_token_rejects_invalid_token():
    reset_repo = MagicMock()
    user_repo = MagicMock()
    reset_repo.get_reset_token_by_hash.return_value = None

    with pytest.raises(ValueError, match="Invalid"):
        InviteService.consume_invite_token(
            "rawtoken", "NewPass123!", reset_repo, user_repo
        )


def test_consume_invite_token_rejects_weak_password():
    reset_repo = MagicMock()
    user_repo = MagicMock()
    reset_repo.get_reset_token_by_hash.return_value = {
        "id": str(uuid4()), "user_id": str(uuid4()), "token_type": "invite",
    }
    user_repo.get_user_by_id.return_value = {
        "id": "x", "username": "alice", "is_active": True,
    }

    with pytest.raises(ValueError, match="requirements"):
        InviteService.consume_invite_token(
            "rawtoken", "weak", reset_repo, user_repo
        )


class TestRequiresMfaWiring:
    def test_invited_user_always_requires_mfa(self):
        from unittest.mock import MagicMock
        from src.auth.repositories.password_reset_repository import (
            TOKEN_TYPE_INVITE,
        )

        reset_repo = MagicMock()
        reset_repo.get_reset_token_by_hash.return_value = {
            'id': 'tid',
            'user_id': '00000000-0000-0000-0000-000000000001',
            'token_type': TOKEN_TYPE_INVITE,
        }
        user_repo = MagicMock()
        user_repo.get_user_by_id.return_value = {
            'username': 'alice', 'email': 'a@a.com',
            'role': 'user', 'is_active': True,
        }
        result = InviteService.validate_invite_token('sometoken', reset_repo, user_repo)
        assert result['requires_mfa'] is True
