# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for PasswordResetRepository. Mocks the connection manager."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from src.auth.repositories.password_reset_repository import PasswordResetRepository


def _make_conn_manager(returned_id="11111111-1111-1111-1111-111111111111"):
    """Build a connection manager whose cursor returns the given id from fetchone."""
    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": returned_id}
    conn = MagicMock()
    cm = MagicMock()
    cm.get_cursor.return_value.__enter__.return_value = (cursor, conn)
    cm.get_cursor.return_value.__exit__.return_value = False
    return cm, cursor, conn


def test_create_reset_token_defaults_token_type_to_reset():
    cm, cursor, _ = _make_conn_manager()
    repo = PasswordResetRepository(cm)

    repo.create_reset_token(
        user_id=uuid4(),
        token_hash="a" * 64,
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )

    args, kwargs = cursor.execute.call_args
    sql, params = args
    assert "token_type" in sql, "INSERT must reference token_type column"
    assert params["token_type"] == "reset"


def test_create_reset_token_accepts_invite_type():
    cm, cursor, _ = _make_conn_manager()
    repo = PasswordResetRepository(cm)

    repo.create_reset_token(
        user_id=uuid4(),
        token_hash="b" * 64,
        expires_at=datetime.utcnow() + timedelta(hours=1),
        token_type="invite",
    )

    args, _kwargs = cursor.execute.call_args
    _sql, params = args
    assert params["token_type"] == "invite"


def test_create_reset_token_rejects_unknown_type():
    cm, _cursor, _conn = _make_conn_manager()
    repo = PasswordResetRepository(cm)

    with pytest.raises(ValueError, match="token_type"):
        repo.create_reset_token(
            user_id=uuid4(),
            token_hash="c" * 64,
            expires_at=datetime.utcnow() + timedelta(hours=1),
            token_type="bogus",
        )


def test_invalidate_all_user_tokens_default_invalidates_all_types():
    cm, cursor, _ = _make_conn_manager()
    cursor.rowcount = 3
    repo = PasswordResetRepository(cm)

    repo.invalidate_all_user_tokens(uuid4())

    args, _ = cursor.execute.call_args
    sql, params = args
    assert "token_type" not in sql
    assert "token_type" not in params


def test_invalidate_all_user_tokens_filters_by_token_type():
    cm, cursor, _ = _make_conn_manager()
    cursor.rowcount = 1
    repo = PasswordResetRepository(cm)

    repo.invalidate_all_user_tokens(uuid4(), token_type="invite")

    args, _ = cursor.execute.call_args
    sql, params = args
    assert "token_type" in sql
    assert params["token_type"] == "invite"


def test_get_reset_token_by_hash_select_includes_token_type():
    """Regression guard: SELECT must include token_type so InviteService can
    discriminate invite tokens from password-reset tokens."""
    cm, cursor, _ = _make_conn_manager()
    cursor.fetchone.return_value = None
    repo = PasswordResetRepository(cm)

    repo.get_reset_token_by_hash("d" * 64)

    args, _ = cursor.execute.call_args
    sql, _params = args
    assert "token_type" in sql, "SELECT must include token_type column"


def test_get_reset_token_by_hash_returns_token_type_in_dict():
    cm, cursor, _ = _make_conn_manager()
    cursor.fetchone.return_value = {
        "id": "11111111-1111-1111-1111-111111111111",
        "user_id": "22222222-2222-2222-2222-222222222222",
        "token_hash": "e" * 64,
        "expires_at": datetime.utcnow() + timedelta(hours=1),
        "created_at": datetime.utcnow(),
        "used_at": None,
        "token_type": "invite",
    }
    repo = PasswordResetRepository(cm)

    result = repo.get_reset_token_by_hash("e" * 64)

    assert result is not None
    assert result["token_type"] == "invite"
