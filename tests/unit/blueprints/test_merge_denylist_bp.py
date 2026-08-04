# SPDX-License-Identifier: AGPL-3.0-or-later
"""Blueprint tests for the admin merge-denylist CRUD API (#973).

Mirrors test_metrics_forwarding_bp.py: `client` (function-scoped, resets
MagicMock services between tests), `auth_headers` callable, `blueprint_app`
session-scoped app. RBAC 403 requires `_prime_regular_user` — the autouse
`_prime_user_cache` fixture always seeds an admin at `jwt_user:test-user-id`,
and `require_admin()` reads the role from that cached user (via
`user_lookup_loader`), not from the JWT's `role` claim — so `auth_headers`
alone cannot produce a non-admin identity. A Pydantic validation failure
(bad `identifier_type`) returns 400, same as a service-raised `ValueError`
(bad identifier value) caught and re-raised as `ValidationError` by the
route.
"""

import pytest


def _entry(**overrides):
    entry = {
        "id": "11111111-1111-1111-1111-111111111111",
        "identifier_type": "serial",
        "match_type": "exact",
        "identifier_value": "ABC123",
        "raw_value": "ABC123",
        "reason": "dup",
        "created_by": "test-user-id",
        "enabled": True,
        "created_at": "2026-07-13T00:00:00+00:00",
    }
    entry.update(overrides)
    return entry


def test_add_requires_admin(client, auth_headers, _prime_regular_user):
    resp = client.post(
        "/api/merge-denylist",
        json={"identifier_type": "hostname", "raw_value": "web01"},
        headers=auth_headers("user"),
    )
    assert resp.status_code == 403


def test_add_validates_type(client, auth_headers, blueprint_app):
    resp = client.post(
        "/api/merge-denylist",
        json={"identifier_type": "ip", "raw_value": "1.2.3.4"},
        headers=auth_headers("admin"),
    )
    assert resp.status_code == 400  # Literal rejects 'ip'
    blueprint_app.merge_denylist_service.add_entry.assert_not_called()


def test_add_service_value_error_is_400(client, auth_headers, blueprint_app):
    blueprint_app.merge_denylist_service.add_entry.side_effect = ValueError(
        "not a valid MAC address"
    )
    resp = client.post(
        "/api/merge-denylist",
        json={"identifier_type": "mac", "raw_value": "not-a-mac"},
        headers=auth_headers("admin"),
    )
    assert resp.status_code == 400


def test_add_and_list_happy_path(client, auth_headers, blueprint_app):
    blueprint_app.merge_denylist_service.add_entry.return_value = _entry()

    resp = client.post(
        "/api/merge-denylist",
        json={"identifier_type": "serial", "raw_value": "ABC123", "reason": "dup"},
        headers=auth_headers("admin"),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["identifier_value"] == "ABC123"

    # created_by comes from the JWT identity (sub), not the request body. The
    # pattern fields (#991) are forwarded on every call, so a body that omits
    # them pins the defaults: an exact entry with no range bounds.
    kwargs = blueprint_app.merge_denylist_service.add_entry.call_args.kwargs
    assert kwargs == {
        "identifier_type": "serial",
        "match_type": "exact",
        "raw_value": "ABC123",
        "range_start": None,
        "range_end": None,
        "reason": "dup",
        "created_by": "test-user-id",
    }

    blueprint_app.merge_denylist_service.list_entries.return_value = [_entry()]
    resp = client.get("/api/merge-denylist", headers=auth_headers("admin"))
    assert resp.status_code == 200
    assert resp.get_json()["data"]["entries"] == [_entry()]


def test_add_pattern_forwards_match_type(client, auth_headers, blueprint_app):
    blueprint_app.merge_denylist_service.add_entry.return_value = _entry(
        identifier_type="mac",
        match_type="prefix",
        identifier_value="bc:24:11:*",
        raw_value="BC-24-11-*",
    )
    resp = client.post(
        "/api/merge-denylist",
        json={
            "identifier_type": "mac",
            "match_type": "prefix",
            "raw_value": "BC-24-11-*",
            "reason": "proxmox guest veth",
        },
        headers=auth_headers("admin"),
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["match_type"] == "prefix"
    kwargs = blueprint_app.merge_denylist_service.add_entry.call_args.kwargs
    assert kwargs["match_type"] == "prefix"
    assert kwargs["raw_value"] == "BC-24-11-*"


def test_add_range_forwards_bounds(client, auth_headers, blueprint_app):
    blueprint_app.merge_denylist_service.add_entry.return_value = _entry(
        identifier_type="mac",
        match_type="range",
        identifier_value="bc:24:11:00:00:00-bc:24:11:ff:ff:ff",
        raw_value="BC:24:11:00:00:00 - BC:24:11:FF:FF:FF",
    )
    resp = client.post(
        "/api/merge-denylist",
        json={
            "identifier_type": "mac",
            "match_type": "range",
            "range_start": "BC:24:11:00:00:00",
            "range_end": "BC:24:11:FF:FF:FF",
        },
        headers=auth_headers("admin"),
    )
    assert resp.status_code == 200
    kwargs = blueprint_app.merge_denylist_service.add_entry.call_args.kwargs
    assert kwargs["range_start"] == "BC:24:11:00:00:00"
    assert kwargs["range_end"] == "BC:24:11:FF:FF:FF"
    assert kwargs["raw_value"] is None


@pytest.mark.parametrize(
    "body",
    [
        # serial is exact-only, and 'regex' is not a match type at all (#991).
        {"identifier_type": "serial", "match_type": "prefix", "raw_value": "abc*"},
        {"identifier_type": "hostname", "match_type": "regex", "raw_value": "web.*"},
        # A range needs bounds, not a raw_value.
        {"identifier_type": "mac", "match_type": "range", "raw_value": "bc:24:11:*"},
        {"identifier_type": "mac", "match_type": "range",
         "range_start": "bc:24:11:00:00:00"},
    ],
)
def test_add_rejects_illegal_pattern_bodies(client, auth_headers, blueprint_app, body):
    resp = client.post(
        "/api/merge-denylist", json=body, headers=auth_headers("admin")
    )
    assert resp.status_code == 400  # Pydantic rejects it before the service
    blueprint_app.merge_denylist_service.add_entry.assert_not_called()


def test_delete_removes_entry(client, auth_headers, blueprint_app):
    blueprint_app.merge_denylist_service.remove_entry.return_value = True
    resp = client.delete(
        "/api/merge-denylist/11111111-1111-1111-1111-111111111111",
        headers=auth_headers("admin"),
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"] == {"ok": True}


def test_delete_not_found_returns_404(client, auth_headers, blueprint_app):
    # A well-formed id that matches no row. Spelling it as a real UUID is the
    # point: an id has to be castable to reach the DELETE at all, so only then
    # can "not found" mean what it says. This read "does-not-exist" until #973 —
    # green here (the service is mocked) while the same request was a 500 in
    # production, where the id reaches `%(id)s::uuid`.
    blueprint_app.merge_denylist_service.remove_entry.return_value = False
    resp = client.delete(
        "/api/merge-denylist/22222222-2222-2222-2222-222222222222",
        headers=auth_headers("admin"),
    )
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "entry_id",
    [
        "does-not-exist",
        "11111111-1111-1111-1111-11111111111",  # 31 hex digits — the near miss
        "1111111.-1111-1111-1111-111111111111",
        "0",
    ],
)
def test_delete_malformed_id_is_400_not_500(
    client, auth_headers, blueprint_app, entry_id
):
    # Postgres raises on a `%(id)s::uuid` cast it cannot parse, and psycopg2's
    # error is not a ValueError, so handle_errors' catch-all answered a typo
    # with a 500 (#973). The service must not be reached at all — the mocked
    # harness cannot raise the DB error, so "rejected before the service" is
    # what stands in for it.
    resp = client.delete(
        f"/api/merge-denylist/{entry_id}", headers=auth_headers("admin")
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "validation_error"
    blueprint_app.merge_denylist_service.remove_entry.assert_not_called()


def test_delete_canonicalises_an_id_postgres_would_reject(client, auth_headers, blueprint_app):
    # Python's UUID() parses spellings Postgres's ::uuid does not, so forwarding
    # the caller's string would move the 500 to a rarer input rather than remove
    # it. The service sees the canonical form, which Postgres always accepts.
    blueprint_app.merge_denylist_service.remove_entry.return_value = True
    resp = client.delete(
        "/api/merge-denylist/urn:uuid:11111111-1111-1111-1111-111111111111",
        headers=auth_headers("admin"),
    )
    assert resp.status_code == 200
    blueprint_app.merge_denylist_service.remove_entry.assert_called_once_with(
        "11111111-1111-1111-1111-111111111111"
    )


def test_delete_requires_admin(client, auth_headers, _prime_regular_user, blueprint_app):
    # RBAC still precedes validation: a non-admin must not learn whether an id
    # is well-formed, so a malformed one is a 403 rather than a 400.
    resp = client.delete(
        "/api/merge-denylist/does-not-exist", headers=auth_headers("user")
    )
    assert resp.status_code == 403
    blueprint_app.merge_denylist_service.remove_entry.assert_not_called()
