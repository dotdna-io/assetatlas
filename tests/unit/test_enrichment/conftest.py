# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared fixtures for enrichment route tests.

Provides user cache priming so that JWT-protected + RBAC-protected
endpoints resolve ``current_user`` correctly via the in-memory cache.
"""

import pytest

from src.api.utils.caching import cache


def _make_user(role="admin", user_id="test-user-id", username="testuser"):
    """Build a user dict suitable for the JWT user cache."""
    return {
        "id": user_id,
        "username": username,
        "role": role,
        "is_active": True,
        "email": f"{username}@test.local",
        "full_name": "Test User",
    }


@pytest.fixture(autouse=True)
def _prime_user_cache(blueprint_app):
    """Put an admin user dict in the JWT cache before every test."""
    with blueprint_app.app_context():
        cache.set("jwt_user:test-user-id", _make_user("admin"), timeout=300)
    yield
    with blueprint_app.app_context():
        cache.delete("jwt_user:test-user-id")


@pytest.fixture()
def _prime_regular_user(blueprint_app):
    """Override the cache with a regular (non-admin) user for RBAC tests."""
    with blueprint_app.app_context():
        cache.set("jwt_user:test-user-id", _make_user("user"), timeout=300)
    yield
    with blueprint_app.app_context():
        cache.set("jwt_user:test-user-id", _make_user("admin"), timeout=300)
