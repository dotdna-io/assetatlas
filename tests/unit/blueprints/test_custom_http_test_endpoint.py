# SPDX-License-Identifier: AGPL-3.0-or-later
"""Blueprint test for POST /api/integrations/connectors/custom_http/test."""
import json
from types import SimpleNamespace

import responses


URL = "/api/integrations/connectors/custom_http/test"


def _config(**overrides):
    base = {
        "method": "GET",
        "url": "https://api.example.com/v1/items",
        "auth_type": "none",
        "pagination_type": "none",
        "records_root": "$.items",
        "field_bindings": [{"source_path": "$.id", "source_name": "id"}],
    }
    base.update(overrides)
    return base


# ----- Auth ------------------------------------------------------------------


def test_endpoint_requires_auth(client):
    resp = client.post(URL, json=_config())
    assert resp.status_code == 401


def test_endpoint_requires_admin(client, auth_headers, _prime_regular_user):
    resp = client.post(URL, json=_config(), headers=auth_headers("user"))
    assert resp.status_code == 403


# ----- Happy path ------------------------------------------------------------


@responses.activate
def test_happy_path_returns_status_and_body_preview(client, auth_headers):
    responses.add(
        responses.GET,
        "https://api.example.com/v1/items",
        json={"items": [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]},
        status=200,
    )
    resp = client.post(URL, json=_config(), headers=auth_headers("admin"))

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["status_code"] == 200
    assert body["body_truncated"] is False
    assert body["records_root_match"]["valid_path"] is True
    assert body["records_root_match"]["matched_count"] == 4
    assert len(body["records_root_match"]["sample_records"]) == 3


# ----- Error paths -----------------------------------------------------------


@responses.activate
def test_auth_failure_returns_error_type_auth(client, auth_headers):
    responses.add(
        responses.GET,
        "https://api.example.com/v1/items",
        json={"err": "unauthorized"},
        status=401,
    )
    resp = client.post(URL, json=_config(), headers=auth_headers("admin"))
    body = resp.get_json()
    assert body["success"] is False
    assert body["error_type"] == "auth"
    assert body["status_code"] == 401


@responses.activate
def test_error_messages_redact_url_query_string(client, auth_headers):
    """An api_key carried as a query param (location='query') would otherwise
    leak back to the wizard via the HTTPError "for url: ..." message."""
    responses.add(
        responses.GET,
        "https://api.example.com/v1/items",
        json={"err": "unauthorized"},
        status=401,
    )
    cfg = _config(
        url="https://api.example.com/v1/items",
        auth_type="api_key",
        api_key="super-secret-key-do-not-leak",
        api_key_name="apikey",
        api_key_location="query",
    )
    resp = client.post(URL, json=cfg, headers=auth_headers("admin"))
    body = resp.get_json()
    msg = body.get("message", "")
    assert "super-secret-key-do-not-leak" not in msg
    assert "?[redacted]" in msg or "?" not in msg


def test_config_validation_failure_returns_error_type_config(
    client, auth_headers
):
    bad = _config(url="")  # missing url
    resp = client.post(URL, json=bad, headers=auth_headers("admin"))
    body = resp.get_json()
    assert body["success"] is False
    assert body["error_type"] == "config"


# ----- Auto-detected records_root --------------------------------------------


@responses.activate
def test_auto_detect_records_root_returns_detected_path(client, auth_headers):
    """When records_root is empty, the endpoint runs auto-detection and
    surfaces the picked path so the wizard can show "Auto-detected $.items"."""
    responses.add(
        responses.GET,
        "https://api.example.com/v1/items",
        json={"items": [{"id": 1}, {"id": 2}], "totalItems": 2, "page": 1},
        status=200,
    )
    cfg = _config(records_root="")
    resp = client.post(URL, json=cfg, headers=auth_headers("admin"))

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    match = body["records_root_match"]
    assert match["valid_path"] is True
    assert match["auto_detected"] is True
    assert match["detected_path"] == "$.items"
    assert match["matched_count"] == 2
    assert len(match["sample_records"]) == 2


@responses.activate
def test_auto_detect_failure_returns_error_message(client, auth_headers):
    """If the body has no clear records list, the endpoint reports the
    detection failure (instead of silently succeeding with 0 records)."""
    responses.add(
        responses.GET,
        "https://api.example.com/v1/items",
        json={"foo": {"nested": "object"}, "bar": "string"},  # no array
        status=200,
    )
    resp = client.post(URL, json=_config(records_root=""),
                       headers=auth_headers("admin"))
    body = resp.get_json()
    match = body["records_root_match"]
    assert match["valid_path"] is False
    assert match["auto_detected"] is True
    assert match["detected_path"] is None
    assert match["matched_count"] == 0
    assert "auto-detect" in (match["error"] or "").lower()


# ----- Response header redaction ---------------------------------------------


# ----- Edit-mode credential merge --------------------------------------------


@responses.activate
def test_edit_mode_falls_back_to_saved_secret_when_candidate_encrypted(
    client, auth_headers, blueprint_app
):
    """Edit-mode preview ("Preview response") sends the still-encrypted secret
    (``ENC:...``) because the wizard never decrypts it on load. With an
    ``integration_id`` present the endpoint must merge against the saved
    decrypted config and use the REAL secret — not the ciphertext, which the
    upstream API rejects as 401 (the reported bug)."""
    captured = {}

    def _cb(req):
        captured["auth"] = req.headers.get("Authorization")
        return (200, {}, json.dumps({"items": [{"id": 1}]}))

    responses.add_callback(
        responses.GET,
        "https://api.example.com/v1/items",
        callback=_cb,
        content_type="application/json",
    )

    blueprint_app.integration_service.repository.get_by_id.return_value = (
        SimpleNamespace(
            config={
                "auth_type": "bearer",
                "bearer_token": "real-token",
                "url": "https://api.example.com/v1/items",
            }
        )
    )

    cfg = _config(
        auth_type="bearer",
        bearer_token="ENC:Z2liYmVyaXNo",  # still-encrypted, as the wizard sees
        integration_id="2c9fbf9d-b6ac-4ada-8d21-5d0707d89017",
    )
    resp = client.post(URL, json=cfg, headers=auth_headers("admin"))

    assert resp.status_code == 200
    assert resp.get_json()["success"] is True
    blueprint_app.integration_service.repository.get_by_id.assert_called_once()
    # The connector must authenticate with the saved decrypted token.
    assert captured["auth"] == "Bearer real-token"


@responses.activate
def test_edit_mode_db_error_preserves_inline_200_contract(
    client, auth_headers, blueprint_app
):
    """A DB/decrypt failure while loading the saved integration must not break
    the endpoint's always-200 inline-error contract — it would otherwise
    escape to @handle_errors and return 500."""
    responses.add(
        responses.GET,
        "https://api.example.com/v1/items",
        json={"items": [{"id": 1}]},
        status=200,
    )
    blueprint_app.integration_service.repository.get_by_id.side_effect = (
        RuntimeError("db unavailable")
    )

    cfg = _config(integration_id="2c9fbf9d-b6ac-4ada-8d21-5d0707d89017")
    resp = client.post(URL, json=cfg, headers=auth_headers("admin"))

    assert resp.status_code == 200
    assert resp.get_json()["success"] is True


@responses.activate
def test_create_mode_does_not_consult_saved_config(
    client, auth_headers, blueprint_app
):
    """Without an integration_id (create mode) no saved row is looked up."""
    responses.add(
        responses.GET,
        "https://api.example.com/v1/items",
        json={"items": [{"id": 1}]},
        status=200,
    )
    resp = client.post(URL, json=_config(), headers=auth_headers("admin"))

    assert resp.status_code == 200
    blueprint_app.integration_service.repository.get_by_id.assert_not_called()


@responses.activate
def test_response_headers_are_redacted(client, auth_headers):
    responses.add(
        responses.GET,
        "https://api.example.com/v1/items",
        json={"items": []},
        status=200,
        headers={
            "Set-Cookie": "session=abc; HttpOnly",
            "X-Auth-Token": "leak-me",
            "X-Total": "0",
        },
    )
    resp = client.post(URL, json=_config(), headers=auth_headers("admin"))
    body = resp.get_json()
    headers = body["response_headers"]
    assert "Set-Cookie" not in headers
    assert "X-Auth-Token" not in headers
    assert headers.get("X-Total") == "0"
