# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixtures for tests/unit/teams_settings/.

Primes the JWT user cache so user_lookup_loader returns an admin user
without hitting the database (mirrors tests/unit/api/conftest.py).
"""

import pytest

from src.api.utils.caching import cache
from tests.factories import make_jwt_user


@pytest.fixture(autouse=True)
def _prime_user_cache(blueprint_app):
    """Put an admin user dict in the JWT cache so user_lookup_loader returns it."""
    with blueprint_app.app_context():
        cache.set("jwt_user:test-user-id", make_jwt_user("admin"), timeout=300)
    yield
    with blueprint_app.app_context():
        cache.delete("jwt_user:test-user-id")
