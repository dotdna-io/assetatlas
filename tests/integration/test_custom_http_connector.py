# SPDX-License-Identifier: AGPL-3.0-or-later
"""Integration matrix for CustomHttpConnector.

5 auth × 4 pagination = 20 tests.

Marker: @pytest.mark.integration
Run:   pytest -m integration tests/integration/test_custom_http_connector.py -v
"""
import pytest
import responses

from src.modules.integrations.connectors.custom_http_connector import (
    CustomHttpConnector,
)


pytestmark = pytest.mark.integration


# -----------------------------------------------------------------------------
# Mock server fixtures: per-pagination-shape response registration
# -----------------------------------------------------------------------------


def _register_none(url):
    responses.add(responses.GET, url,
                  json={"items": [{"id": 1}, {"id": 2}]}, status=200)


def _register_offset(url):
    responses.add(
        responses.GET, url,
        json={"items": [{"id": 1}, {"id": 2}], "total": 3}, status=200,
    )
    responses.add(responses.GET, url,
                  json={"items": [{"id": 3}], "total": 3}, status=200)
    responses.add(responses.GET, url,
                  json={"items": [], "total": 3}, status=200)


def _register_cursor(url):
    responses.add(responses.GET, url,
                  json={"items": [{"id": 1}], "next": "cursor-2"}, status=200)
    responses.add(responses.GET, url,
                  json={"items": [{"id": 2}], "next": None}, status=200)


def _register_link(url):
    responses.add(
        responses.GET, url,
        json={"items": [{"id": 1}]}, status=200,
        headers={"Link": f'<{url}?page=2>; rel="next"'},
    )
    responses.add(
        responses.GET, f"{url}?page=2",
        json={"items": [{"id": 2}]}, status=200,
        headers={},
    )


_PAG_REGISTRARS = {
    "none": _register_none,
    "offset": _register_offset,
    "cursor": _register_cursor,
    "link": _register_link,
}


# -----------------------------------------------------------------------------
# Per-auth_type config + token-endpoint mocking
# -----------------------------------------------------------------------------


URL = "https://api.example.com/v1/items"
TOKEN_URL = "https://auth.example.com/oauth/token"


def _register_auth(auth_type):
    """Register any *auth-side* HTTP responses (e.g. OAuth2 token endpoint)."""
    if auth_type == "oauth2":
        responses.add(
            responses.POST, TOKEN_URL,
            json={"access_token": "tok-abc", "expires_in": 3600},
            status=200,
        )


def _config_for(auth_type, pagination_type):
    base = {
        "method": "GET",
        "url": URL,
        "auth_type": auth_type,
        "pagination_type": pagination_type,
        "records_root": "$.items",
        "field_bindings": [{"source_path": "$.id", "source_name": "id"}],
    }
    if auth_type == "basic":
        base.update({"username": "u", "password": "p"})
    elif auth_type == "bearer":
        base["bearer_token"] = "tok-xyz"
    elif auth_type == "api_key":
        base.update({
            "api_key": "k", "api_key_location": "header",
            "api_key_name": "X-API-Key",
        })
    elif auth_type == "oauth2":
        base.update({
            "token_url": TOKEN_URL,
            "client_id": "ci", "client_secret": "cs", "scope": "read",
        })
    if pagination_type == "offset":
        base.update({
            "page_size": 2, "offset_param": "offset", "limit_param": "limit",
            "total_path": "$.total",
        })
    elif pagination_type == "cursor":
        base.update({
            "cursor_request_param": "cursor",
            "cursor_response_path": "$.next",
        })
    return base


_EXPECTED_IDS = {
    "none": [1, 2],
    "offset": [1, 2, 3],
    "cursor": [1, 2],
    "link": [1, 2],
}


# -----------------------------------------------------------------------------
# The matrix
# -----------------------------------------------------------------------------


_AUTH_TYPES = ["none", "basic", "bearer", "api_key", "oauth2"]
_PAG_TYPES = ["none", "offset", "cursor", "link"]


@pytest.mark.parametrize("auth_type", _AUTH_TYPES)
@pytest.mark.parametrize("pagination_type", _PAG_TYPES)
@responses.activate
def test_matrix(auth_type, pagination_type):
    _register_auth(auth_type)
    _PAG_REGISTRARS[pagination_type](URL)

    connector = CustomHttpConnector(_config_for(auth_type, pagination_type))
    batch_id = f"batch-{auth_type}-{pagination_type}"
    records = list(connector.fetch_data(batch_id=batch_id))

    ids = [r["id"] for r in records]
    assert ids == _EXPECTED_IDS[pagination_type], (
        f"auth={auth_type} pagination={pagination_type} ids={ids}"
    )
    # Framework metadata is injected
    assert all(r.get("_source") == "custom_http" for r in records)
    assert all("_batch_id" in r for r in records)
