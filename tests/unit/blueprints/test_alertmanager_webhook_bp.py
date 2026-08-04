# SPDX-License-Identifier: AGPL-3.0-or-later
"""POST /api/alerts/webhook/alertmanager — Bearer-auth + payload validation.

Fixtures used (provided by tests/conftest.py):
- client: function-scoped Flask test client; resets service mocks between tests.
- blueprint_app: session-scoped Flask app with MagicMocks installed for every
  attr in `_SERVICE_ATTRS` (we add `prometheus_webhook_service` in Step 1).

The webhook route skips `@jwt_required` — so we never send JWT headers here.
"""
import pytest


VALID_SECRET = "s" * 48


@pytest.fixture(autouse=True)
def _set_secret_and_default_return(client, blueprint_app):
    """Set the shared secret and default the service mock to a standard return.

    Depends on `client` (not just `blueprint_app`) so this runs AFTER the
    `client` fixture resets all service mocks — otherwise the reset would wipe
    the return_value we set here.
    """
    blueprint_app.config["ALERTMANAGER_WEBHOOK_SECRET"] = VALID_SECRET
    blueprint_app.prometheus_webhook_service.handle_alertmanager_payload.return_value = {
        "processed": 1, "firing": 1, "resolved": 0, "unknown_alertname": 0,
    }


def _payload():
    return {
        "version": "4",
        "status": "firing",
        "alerts": [{
            "status": "firing",
            "labels": {"alertname": "redis-down", "severity": "critical"},
            "annotations": {"summary": "Redis", "description": "d"},
            "startsAt": "2026-04-24T12:00:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "fingerprint": "fp",
        }],
    }


def test_missing_authorization_header_is_401(client):
    resp = client.post("/api/alerts/webhook/alertmanager", json=_payload())
    assert resp.status_code == 401


def test_bearer_with_wrong_secret_is_401(client):
    resp = client.post(
        "/api/alerts/webhook/alertmanager",
        headers={"Authorization": "Bearer wrong-secret"},
        json=_payload(),
    )
    assert resp.status_code == 401


def test_non_bearer_scheme_is_401(client):
    resp = client.post(
        "/api/alerts/webhook/alertmanager",
        headers={"Authorization": "Basic " + "s" * 48},
        json=_payload(),
    )
    assert resp.status_code == 401


def test_correct_bearer_and_valid_body_returns_200(client, blueprint_app):
    resp = client.post(
        "/api/alerts/webhook/alertmanager",
        headers={"Authorization": f"Bearer {VALID_SECRET}"},
        json=_payload(),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    data = body.get("data", body)
    assert data["processed"] == 1
    assert data["firing"] == 1
    blueprint_app.prometheus_webhook_service.handle_alertmanager_payload.assert_called_once()


def test_correct_bearer_but_malformed_body_returns_400(client):
    resp = client.post(
        "/api/alerts/webhook/alertmanager",
        headers={"Authorization": f"Bearer {VALID_SECRET}"},
        data="not-json",
        content_type="text/plain",
    )
    assert resp.status_code == 400


def test_endpoint_is_not_gated_by_jwt(client):
    """Webhook explicitly skips @jwt_required — works with no JWT header."""
    resp = client.post(
        "/api/alerts/webhook/alertmanager",
        headers={"Authorization": f"Bearer {VALID_SECRET}"},
        json=_payload(),
    )
    assert resp.status_code == 200
