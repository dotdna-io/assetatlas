# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end test of the Teams bot link flow against the live test DB.

Exercises:
  POST /api/auth/teams-link/initiate     (service key)
  TeamsLinkService.consume_link_token    (simulating user-confirm)
  POST /api/auth/teams-bridge/exchange   (service key)
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.integration


_BRIDGE_KEY = "test-bridge-key-padding-to-pass-min-length"


@pytest.fixture(autouse=True)
def _bridge_key(monkeypatch):
    monkeypatch.setenv("ASSETATLAS_BRIDGE_API_KEY", _BRIDGE_KEY)


@pytest.fixture
def admin_user_id(live_db):
    rows = live_db.execute_query(
        "SELECT id FROM users WHERE username = %(u)s LIMIT 1",
        {"u": "admin"},
    )
    assert rows, "admin user must exist in the live dev DB"
    return rows[0]["id"]


@pytest.fixture
def cleanup_link_rows(live_db):
    """Wipe any test rows on entry and exit so this test is repeatable."""
    aad = f"aad-int-{uuid.uuid4()}"
    tnt = f"tnt-int-{uuid.uuid4()}"

    def _wipe():
        live_db.execute_query(
            "DELETE FROM teams_account_links "
            "WHERE aad_object_id = %(aad)s",
            {"aad": aad},
            fetch=False,
        )
        # JTIs are uniquely random per mint; no targeted delete needed.

    _wipe()
    yield aad, tnt
    _wipe()


def test_link_then_exchange_round_trip(
    live_app, live_client, live_db, admin_user_id, cleanup_link_rows,
):
    aad, tnt = cleanup_link_rows

    # 1. Initiate
    r1 = live_client.post(
        "/api/auth/teams-link/initiate",
        json={"aad_object_id": aad, "tenant_id": tnt,
              "display_name": "Int Test"},
        headers={"X-Service-Key": _BRIDGE_KEY},
    )
    assert r1.status_code == 200, r1.get_data(as_text=True)
    body1 = r1.get_json()["data"]
    link_url = body1["link_url"]
    token = link_url.rsplit("=", 1)[1]
    assert token

    # 2. Confirm — drive the service directly (the real flow involves the
    #    browser POSTing /api/auth/teams-link/confirm with a user JWT;
    #    that's covered by the unit tests already).
    live_app.teams_link_service.consume_link_token(
        token, user_id=admin_user_id, client_ip="127.0.0.1",
    )

    # 3. Exchange — bridge should now get a JWT
    r3 = live_client.post(
        "/api/auth/teams-bridge/exchange",
        json={"aad_object_id": aad, "tenant_id": tnt},
        headers={"X-Service-Key": _BRIDGE_KEY},
    )
    assert r3.status_code == 200, r3.get_data(as_text=True)
    body3 = r3.get_json()["data"]
    assert body3["access_token"]
    assert body3["expires_in"] == 300
    assert body3["user_id"] == str(admin_user_id)

    # 4. Re-exchange after deleting the link — should 404
    live_db.execute_query(
        "DELETE FROM teams_account_links "
        "WHERE aad_object_id = %(aad)s",
        {"aad": aad},
        fetch=False,
    )
    r4 = live_client.post(
        "/api/auth/teams-bridge/exchange",
        json={"aad_object_id": aad, "tenant_id": tnt},
        headers={"X-Service-Key": _BRIDGE_KEY},
    )
    assert r4.status_code == 404
