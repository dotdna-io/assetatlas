# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared fixtures for ``tests/unit/*`` subdirectories.

Holds the suite-wide JWT test plumbing so authenticated route tests can
live in ANY directory under ``tests/unit/`` (#1298):

- ``_stub_jwt_session_lookup`` (autouse) patches
  ``SessionRepository.get_session_by_access_jti`` to return an active fake
  session.  Without it, factory.py's ``check_if_token_revoked`` runs the
  real repo method against the mocked connection manager, the lookup
  returns no session, and the JWT loader fails closed — every authed
  request 401s with ``token_revoked`` (#1292).
- ``_prime_user_cache`` (autouse) seeds ``jwt_user:test-user-id`` with an
  admin user dict so the JWT ``user_lookup_loader`` resolves
  ``current_user`` without hitting the (mocked) DB.
- ``authed_headers(role)`` mints a JWT and primes the user cache for that
  role in one call.

Tests whose POINT is revocation behaviour must not see the always-active
stub.  They opt out either by overriding ``_stub_jwt_session_lookup``
with a no-op fixture (see ``tests/unit/auth/test_session_repository.py``,
which exercises the real repo method) or by re-patching the repo method
inside the test body, which wins over the fixture's outer ``patch`` (see
``tests/unit/api/test_token_revocation_fail_closed.py``).
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
def authed_headers(blueprint_app, auth_headers):
    """Create JWT auth headers and prime the user cache for RBAC.

    Usage::

        resp = client.get("/api/tag-rules", headers=authed_headers("admin"))
        resp = client.post("/api/tag-rules", headers=authed_headers("user"))
    """
    def _make(role="admin", username="testuser", user_id="test-user-id"):
        with blueprint_app.app_context():
            cache.set(
                f"jwt_user:{user_id}",
                make_jwt_user(role=role, username=username, user_id=user_id),
            )
        return auth_headers(role=role, username=username, user_id=user_id)

    return _make
