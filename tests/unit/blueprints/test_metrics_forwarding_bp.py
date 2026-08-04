# SPDX-License-Identifier: AGPL-3.0-or-later
"""Blueprint tests for the metrics forwarding runtime config admin API (#922 P2b).

Mirrors test_alert_channels_bp.py: `client`, `auth_headers` callable, `blueprint_app`
with per-test-reset MagicMock services, and `_prime_regular_user` for 403 checks (the
autouse `_prime_user_cache` fixture always seeds an admin, so a non-admin assertion
requires overriding that cache entry). Response envelope is {success, data}; a Pydantic
validation failure returns 400. The remote_write secret is never returned in plaintext.
"""

import pytest


class TestMetricsForwardingAuth:
    """Every endpoint is admin / super_admin only."""

    PATHS = [
        ("GET", "/api/metrics-forwarding/config"),
        ("PUT", "/api/metrics-forwarding/config"),
        ("POST", "/api/metrics-forwarding/config"),
        ("POST", "/api/metrics-forwarding/config/test"),
    ]

    @pytest.mark.parametrize("method,path", PATHS)
    def test_no_auth_returns_401(self, client, method, path):
        resp = getattr(client, method.lower())(path)
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path", PATHS)
    def test_non_admin_returns_403(
        self, client, auth_headers, _prime_regular_user, method, path
    ):
        # require_admin runs before validate_request, so a non-admin is 403 regardless
        # of body — send an empty JSON body for the write endpoints all the same.
        kwargs = {"headers": auth_headers("user")}
        if method != "GET":
            kwargs["json"] = {}
        resp = getattr(client, method.lower())(path, **kwargs)
        assert resp.status_code == 403


def test_get_config_returns_masked_envelope(client, auth_headers, blueprint_app):
    blueprint_app.metrics_forwarding_service.get_config.return_value = {
        "endpoint_url": "https://x/push",
        "auth_type": "bearer",
        "username": None,
        "external_labels": {},
        "enabled": True,
        "has_secret": True,
        "updated_at": None,
        "updated_by": "admin",
    }
    resp = client.get("/api/metrics-forwarding/config", headers=auth_headers("admin"))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    data = body["data"]
    assert data["has_secret"] is True
    # The secret itself is never present in the response — neither key nor value.
    assert "auth_secret" not in data
    assert "auth_secret" not in resp.get_data(as_text=True)


def test_set_config_ok_and_masks_secret(client, auth_headers, blueprint_app):
    blueprint_app.metrics_forwarding_service.set_config.return_value = {
        "endpoint_url": "https://x/push",
        "auth_type": "bearer",
        "username": None,
        "external_labels": {},
        "enabled": True,
        "has_secret": True,
        "updated_at": None,
        "updated_by": "testuser",
        "apply": {
            "enabled": True,
            "config_written": True,
            "reload": {"attempted": True, "ok": True},
        },
    }
    resp = client.put(
        "/api/metrics-forwarding/config",
        headers=auth_headers("admin"),
        json={
            "endpoint_url": "https://x/push",
            "auth_type": "bearer",
            "auth_secret": "plaintext-token",
            "enabled": True,
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["apply"]["reload"]["ok"] is True
    # The plaintext secret sent in must never be echoed back in the response.
    assert "plaintext-token" not in resp.get_data(as_text=True)
    # updated_by is taken from the JWT identity, not the request body.
    kwargs = blueprint_app.metrics_forwarding_service.set_config.call_args.kwargs
    assert kwargs["updated_by"] == "testuser"
    assert kwargs["auth_secret"] == "plaintext-token"


def test_set_config_via_post_is_allowed(client, auth_headers, blueprint_app):
    blueprint_app.metrics_forwarding_service.set_config.return_value = {
        "enabled": False
    }
    resp = client.post(
        "/api/metrics-forwarding/config",
        headers=auth_headers("admin"),
        json={"auth_type": "none", "enabled": False},
    )
    assert resp.status_code == 200
    blueprint_app.metrics_forwarding_service.set_config.assert_called_once()


def test_set_config_invalid_auth_type_400(client, auth_headers, blueprint_app):
    resp = client.put(
        "/api/metrics-forwarding/config",
        headers=auth_headers("admin"),
        json={"auth_type": "mtls", "enabled": False},
    )
    assert resp.status_code == 400
    blueprint_app.metrics_forwarding_service.set_config.assert_not_called()


def test_set_config_service_value_error_is_400(client, auth_headers, blueprint_app):
    blueprint_app.metrics_forwarding_service.set_config.side_effect = ValueError(
        "endpoint_url is required when forwarding is enabled"
    )
    resp = client.put(
        "/api/metrics-forwarding/config",
        headers=auth_headers("admin"),
        json={"auth_type": "none", "enabled": True},
    )
    assert resp.status_code == 400


def test_test_connection_returns_result(client, auth_headers, blueprint_app):
    blueprint_app.metrics_forwarding_service.test_connection.return_value = {
        "reachable": True,
        "status_code": 405,
        "detail": "endpoint responded with HTTP 405",
    }
    resp = client.post(
        "/api/metrics-forwarding/config/test", headers=auth_headers("admin")
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["reachable"] is True
