# SPDX-License-Identifier: AGPL-3.0-or-later
"""Token revocation fails closed: backend errors during session lookup
reject the token rather than letting it through."""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _clear_session_cache():
    from src.api.utils.caching import cache
    cache.clear()


def _force_db_error(monkeypatch):
    from src.auth.repositories import session_repository as repo_mod

    def _boom(self, *_args, **_kwargs):
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(
        repo_mod.SessionRepository, "get_session_by_access_jti", _boom,
    )


@pytest.mark.parametrize("patch_cache_get", [False, True])
def test_protected_endpoint_returns_401_when_session_lookup_raises(
    client, auth_headers, monkeypatch, patch_cache_get,
):
    _force_db_error(monkeypatch)
    from src.api.utils import caching as caching_mod

    if patch_cache_get:
        # Also raise from cache.get so the closure's outer except path
        # (cache miss → DB raises) is exercised, not just the cache hit.
        with patch.object(caching_mod.cache, "get", side_effect=RuntimeError("cache outage")):
            response = client.get("/api/auth/me", headers=auth_headers())
    else:
        response = client.get("/api/auth/me", headers=auth_headers())

    assert response.status_code == 401, (
        f"fail-closed regressed (patch_cache_get={patch_cache_get}): "
        f"got {response.status_code} body={response.get_data(as_text=True)!r}"
    )
