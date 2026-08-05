# SPDX-License-Identifier: AGPL-3.0-or-later
"""Blueprint-specific fixtures.

Only the ``_prime_regular_user`` override used by RBAC 403 tests remains
here.  The JWT session-lookup stub and the admin user-cache prime moved
to ``tests/unit/conftest.py`` so they cover the whole unit suite (#1298);
the shared ``authed_headers`` fixture lives there too.
"""

import pytest

from src.api.utils.caching import cache
from tests.factories import make_jwt_user


@pytest.fixture()
def _prime_regular_user(blueprint_app):
    """Override the cache with a regular (non-admin) user for RBAC tests."""
    with blueprint_app.app_context():
        cache.set("jwt_user:test-user-id", make_jwt_user("user"), timeout=300)
    yield
    with blueprint_app.app_context():
        cache.set("jwt_user:test-user-id", make_jwt_user("admin"), timeout=300)
