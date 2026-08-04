# SPDX-License-Identifier: AGPL-3.0-or-later
"""Blueprint-specific fixtures.

Owns the autouse ``_prime_user_cache`` fixture (kept narrow so it doesn't
fire for unrelated unit tests) and the ``_prime_regular_user`` override
used by RBAC 403 tests. The shared ``authed_headers`` fixture lives in
``tests/unit/conftest.py``.
"""

from unittest.mock import patch

import pytest

from src.api.utils.caching import cache
from tests.factories import make_jwt_user


@pytest.fixture(autouse=True)
def _stub_jwt_session_lookup():
    """Make the JWT-revocation lookup return an active fake session.

    factory.py's ``check_if_token_revoked`` calls
    ``SessionRepository.get_session_by_access_jti(jti)``, which tries to
    open a real cursor on the mocked connection_manager — that mock can't
    unpack to ``(cursor, conn)`` so the lookup raises and the JWT loader
    fails closed (401). Stubbing the repo method directly returns a valid
    session dict so authed routes work in unit tests.
    """
    fake_session = {
        "id": "fake-session",
        "is_active": True,
        "user_id": "test-user-id",
    }
    with patch(
        "src.auth.repositories.session_repository.SessionRepository."
        "get_session_by_access_jti",
        return_value=fake_session,
    ):
        yield


@pytest.fixture(autouse=True)
def _prime_user_cache(blueprint_app):
    """Put an admin user dict in the JWT cache so user_lookup_loader returns it.

    The user_lookup_loader registered in factory.py checks
    ``cache.get(f"jwt_user:{user_id}")`` before hitting the DB.
    By priming this cache key the tests avoid DB calls entirely.
    """
    with blueprint_app.app_context():
        cache.set("jwt_user:test-user-id", make_jwt_user("admin"), timeout=300)
    yield
    with blueprint_app.app_context():
        cache.delete("jwt_user:test-user-id")


@pytest.fixture()
def _prime_regular_user(blueprint_app):
    """Override the cache with a regular (non-admin) user for RBAC tests."""
    with blueprint_app.app_context():
        cache.set("jwt_user:test-user-id", make_jwt_user("user"), timeout=300)
    yield
    with blueprint_app.app_context():
        cache.set("jwt_user:test-user-id", make_jwt_user("admin"), timeout=300)
