# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared fixtures for ``tests/unit/*`` subdirectories.

Hoisting ``authed_headers`` here prevents the blueprint and security
conftests from drifting on JWT header construction. The autouse
``_prime_user_cache`` fixture is deliberately NOT hoisted — keeping it
in each subdir conftest avoids firing against sibling directories
(e.g. ``tests/unit/modules/``, ``tests/unit/services/``) that have no
Flask app context to cache into.
"""

import pytest

from src.api.utils.caching import cache
from tests.factories import make_jwt_user


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
