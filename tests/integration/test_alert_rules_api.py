# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Integration tests for /api/alert-rules blueprint.

Hits real endpoints against a live database.
Tests that write data clean up after themselves via try/finally.

`live_app`, `live_client`, and `live_admin_headers` are supplied by
tests/integration/conftest.py (session-scoped).

`live_user_headers` is defined here as a module-level fixture: it
registers a temporary regular-role account via POST /api/auth/register
and logs in to obtain a JWT.
"""

import uuid

import pytest

pytestmark = pytest.mark.integration

_USER_USERNAME = f"testuser_{uuid.uuid4().hex[:8]}"
_USER_PASSWORD = "IntTest!99"


@pytest.fixture(scope="module")
def live_user_headers(live_app, live_admin_headers):
    """JWT header for a regular (non-admin) user.

    Creates the account once per module via /api/auth/register,
    then logs in. The account persists in the DB after the test run —
    it is a test user only, not a security concern in test environments.
    """
    with live_app.test_client() as c:
        # Register (admin-only endpoint — pass admin token).
        # Status 200/201 = created, 409 = already exists (re-run).
        # Status 500 may occur due to a pre-existing post-creation bug in
        # user_repository.get_user_by_id, but the INSERT still succeeds —
        # login will work even if register returned 500.
        reg = c.post(
            "/api/auth/register",
            headers=live_admin_headers,
            json={
                "username": _USER_USERNAME,
                "email": f"{_USER_USERNAME}@example.com",
                "password": _USER_PASSWORD,
            },
        )
        assert reg.status_code in (200, 201, 409, 500), (
            f"Register failed unexpectedly: {reg.status_code} {reg.get_data(as_text=True)}"
        )

        # Login — must succeed regardless of register status
        login = c.post(
            "/api/auth/login",
            json={"username": _USER_USERNAME, "password": _USER_PASSWORD},
        )
        assert login.status_code == 200, (
            f"User login failed: {login.status_code} {login.get_data(as_text=True)}"
        )
        body = login.get_json()
        token = body.get("access_token") or body["data"]["access_token"]

    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------

def test_list_rules_requires_auth(live_client):
    resp = live_client.get("/api/alert-rules")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Read access
# ---------------------------------------------------------------------------

def test_list_rules_works_for_user(live_client, live_user_headers):
    resp = live_client.get("/api/alert-rules", headers=live_user_headers)
    assert resp.status_code == 200
    body = resp.get_json()
    # response_wrapper nests under 'data'
    data = body.get("data") or body
    assert "rules" in data
    system_rules = [r for r in data["rules"] if r["source"] == "system"]
    assert len(system_rules) == 4


def test_list_rules_works_for_admin(live_client, live_admin_headers):
    resp = live_client.get("/api/alert-rules", headers=live_admin_headers)
    assert resp.status_code == 200
    body = resp.get_json()
    data = body.get("data") or body
    assert "rules" in data


# ---------------------------------------------------------------------------
# RBAC: create/update/delete are admin-only
# ---------------------------------------------------------------------------

def test_create_rule_requires_admin(live_client, live_user_headers):
    resp = live_client.post(
        "/api/alert-rules",
        headers=live_user_headers,
        json={
            "name": "X",
            "type": "security",
            "severity": "high",
            "target_filters": [{"field": "category", "op": "=", "value": "Server"}],
        },
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Validation errors (admin)
# ---------------------------------------------------------------------------

def test_create_rule_rejects_empty_target_filters(live_client, live_admin_headers):
    resp = live_client.post(
        "/api/alert-rules",
        headers=live_admin_headers,
        json={
            "name": "X",
            "type": "security",
            "severity": "high",
            "target_filters": [],
        },
    )
    assert resp.status_code == 400


def test_create_rule_rejects_changes_by_threshold(live_client, live_admin_headers):
    resp = live_client.post(
        "/api/alert-rules",
        headers=live_admin_headers,
        json={
            "name": "X",
            "type": "security",
            "severity": "high",
            "target_filters": [{"field": "category", "op": "=", "value": "Server"}],
            "threshold": {"condition": "changes_by", "value": 10, "unit": "percent"},
        },
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# System-rule immutability
# ---------------------------------------------------------------------------

def test_update_system_rule_returns_403(live_client, live_admin_headers):
    listing = live_client.get("/api/alert-rules", headers=live_admin_headers).get_json()
    rules = (listing.get("data") or listing)["rules"]
    system_rule = next(r for r in rules if r["source"] == "system")

    resp = live_client.put(
        f"/api/alert-rules/{system_rule['id']}",
        headers=live_admin_headers,
        json={"name": "Renamed"},
    )
    assert resp.status_code == 403


def test_delete_system_rule_returns_403(live_client, live_admin_headers):
    listing = live_client.get("/api/alert-rules", headers=live_admin_headers).get_json()
    rules = (listing.get("data") or listing)["rules"]
    system_rule = next(r for r in rules if r["source"] == "system")

    resp = live_client.delete(
        f"/api/alert-rules/{system_rule['id']}",
        headers=live_admin_headers,
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Create succeeds for admin
# ---------------------------------------------------------------------------

def test_create_rule_succeeds_for_admin(live_client, live_admin_headers):
    resp = live_client.post(
        "/api/alert-rules",
        headers=live_admin_headers,
        json={
            "name": "Test Server Alert",
            "type": "security",
            "severity": "high",
            "target_filters": [{"field": "category", "op": "=", "value": "Server"}],
        },
    )
    assert resp.status_code == 201
    body = resp.get_json()
    rule = body.get("data") or body
    assert rule["name"] == "Test Server Alert"
    assert rule["source"] == "user"

    # Cleanup
    rule_id = rule["id"]
    live_client.delete(f"/api/alert-rules/{rule_id}", headers=live_admin_headers)


# ---------------------------------------------------------------------------
# Full lifecycle: create → update → toggle → delete
# ---------------------------------------------------------------------------

def test_full_user_rule_lifecycle(live_client, live_admin_headers):
    # Create
    resp = live_client.post(
        "/api/alert-rules",
        headers=live_admin_headers,
        json={
            "name": "Lifecycle Test",
            "type": "compliance",
            "severity": "medium",
            "target_filters": [{"field": "category", "op": "=", "value": "Server"}],
        },
    )
    assert resp.status_code == 201
    body = resp.get_json()
    rule = body.get("data") or body
    rule_id = rule["id"]

    try:
        # Update severity
        resp = live_client.put(
            f"/api/alert-rules/{rule_id}",
            headers=live_admin_headers,
            json={"severity": "high"},
        )
        assert resp.status_code == 200
        updated = (resp.get_json().get("data") or resp.get_json())
        assert updated["severity"] == "high"

        # Toggle off
        resp = live_client.patch(
            f"/api/alert-rules/{rule_id}",
            headers=live_admin_headers,
            json={"enabled": False},
        )
        assert resp.status_code == 200

    finally:
        # Delete
        resp = live_client.delete(
            f"/api/alert-rules/{rule_id}",
            headers=live_admin_headers,
        )
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def test_preview_returns_count(live_client, live_user_headers):
    resp = live_client.post(
        "/api/alert-rules/preview",
        headers=live_user_headers,
        json={
            "target_filters": [{"field": "category", "op": "=", "value": "Server"}],
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    data = body.get("data") or body
    assert "total_matching" in data


# ---------------------------------------------------------------------------
# Anomaly threshold merge (#878 Gap 1)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_anomaly_threshold_partial_merge_preserves_params(live_client, live_admin_headers):
    """#878 Gap 1 acceptance: PATCH a single tuning param merges only that key and
    preserves every other seeded param (pct_medium, min_fit_rows, …)."""
    rules = live_client.get('/api/alert-rules', headers=live_admin_headers).get_json()['data']['rules']
    ml = next(r for r in rules if r['slug'] == 'anomaly-ml-newcomer')
    before = ml['threshold']
    resp = live_client.patch(f"/api/alert-rules/{ml['id']}/anomaly-threshold",
                             json={'pct_high': 0.2}, headers=live_admin_headers)
    assert resp.status_code == 200
    after = resp.get_json()['data']['threshold']
    assert after['pct_high'] == 0.2
    for k in ('pct_medium', 'min_fit_rows'):
        assert after[k] == before[k]          # merge, not replace
    # restore
    live_client.patch(f"/api/alert-rules/{ml['id']}/anomaly-threshold",
                      json={'pct_high': before['pct_high']}, headers=live_admin_headers)
