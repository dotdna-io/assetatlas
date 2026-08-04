# SPDX-License-Identifier: AGPL-3.0-or-later
"""Blueprint tests for the merge-keys viewer endpoint (read-only).

GET /api/assets/<asset_id>/merge-keys — JWT only, no @require_admin (unlike
the merge-denylist CRUD in test_merge_denylist_bp.py). This is a diagnostic
view: it exposes WHICH asset_id_mappings component glued an asset together,
so any authenticated user can see the bad merge key before the (separate,
admin-only) "Don't trust" denylist action acts on it.
"""

import pytest

_UUID = "11111111-1111-1111-1111-111111111111"


def test_merge_keys_requires_auth(client):
    resp = client.get("/api/assets/some-id/merge-keys")
    # Malformed on purpose: @jwt_required() runs outside @handle_errors, so an
    # anonymous caller gets 401 rather than the 400 the id has earned.
    assert resp.status_code == 401


def test_merge_keys_returns_decoded(client, auth_headers, blueprint_app):
    blueprint_app.asset_service.get_merge_keys.return_value = {
        "keys": [
            {
                "type": "serial", "value": "abc123",
                "first_seen": "2026-01-01T00:00:00+00:00",
                "last_seen": "2026-01-02T00:00:00+00:00",
            },
            {
                "type": "hostname", "value": "web01",
                "first_seen": "2026-01-01T00:00:00+00:00",
                "last_seen": "2026-01-02T00:00:00+00:00",
            },
        ],
        "tell": {"serial_count": 1, "over_merged": False, "concurrent": {}},
    }

    # role="user" (not "admin") proves this view has no admin gate.
    resp = client.get(
        f"/api/assets/{_UUID}/merge-keys", headers=auth_headers("user"),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["keys"][0]["type"] == "serial"
    assert body["data"]["tell"] == {
        "serial_count": 1, "over_merged": False, "concurrent": {},
    }
    blueprint_app.asset_service.get_merge_keys.assert_called_once_with(_UUID)


# ---------------------------------------------------------------------------
# Malformed asset_id (#973)
#
# This file's happy path passed "some-id" until #973 — green here, because the
# service is mocked, while the same request was a 500 in production: the id
# reaches asset_repository.get_merge_keys, which casts `%(asset_id)s::uuid`.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("asset_id", [
    "some-id",
    "11111111-1111-1111-1111-11111111111",  # 31 hex digits — the near miss
    "1111111.-1111-1111-1111-111111111111",
    "0",
])
def test_malformed_asset_id_is_400_not_500(
    client, auth_headers, blueprint_app, asset_id,
):
    # Postgres raises on a cast it cannot parse, and psycopg2's error is not a
    # ValueError, so handle_errors' catch-all answered a client typo with a 500.
    # The mocked service here cannot raise that DB error, so "rejected before
    # the service" is what stands in for it.
    resp = client.get(
        f"/api/assets/{asset_id}/merge-keys", headers=auth_headers("user"),
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "validation_error"
    blueprint_app.asset_service.get_merge_keys.assert_not_called()


def test_canonicalises_an_id_postgres_would_reject(
    client, auth_headers, blueprint_app,
):
    # Python's UUID() parses spellings Postgres's ::uuid does not, so passing
    # the caller's string on would move the 500 to a rarer input rather than
    # remove it. The service sees the canonical form, which Postgres accepts.
    blueprint_app.asset_service.get_merge_keys.return_value = {
        "keys": [], "tell": {"serial_count": 0, "over_merged": False, "concurrent": {}},
    }

    resp = client.get(
        f"/api/assets/urn:uuid:{_UUID}/merge-keys", headers=auth_headers("user"),
    )
    assert resp.status_code == 200
    blueprint_app.asset_service.get_merge_keys.assert_called_once_with(_UUID)
