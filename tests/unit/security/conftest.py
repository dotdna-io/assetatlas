# SPDX-License-Identifier: AGPL-3.0-or-later
"""Security-regression-specific fixtures.

Security tests live outside ``tests/unit/blueprints/`` so they do NOT
inherit the blueprints' autouse ``_prime_user_cache`` fixture. Mimic
it here so RBAC-protected endpoints resolve the admin user from the
JWT cache and return the real 4xx validation errors we care about,
rather than 401 auth errors.
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
