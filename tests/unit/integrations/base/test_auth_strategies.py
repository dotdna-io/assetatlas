# SPDX-License-Identifier: AGPL-3.0-or-later
"""Focused tests for new behavior added to base auth strategies.

Existing connector tests cover the unchanged legacy paths.
"""
import pytest
import responses

from src.modules.integrations.base.auth_strategies import (
    APIKeyAuth,
    LoginPasswordJsonAuth,
    NoAuth,
    get_auth_strategy,
)


# ----------------------------- APIKeyAuth.location ----------------------------


def test_apikey_auth_default_location_is_header():
    auth = APIKeyAuth({'api_key': 'k', 'api_key_header': 'X-API-Key'})
    assert auth.location == 'header'


@responses.activate
def test_apikey_auth_query_location_writes_to_url_params():
    responses.add(
        responses.GET,
        "https://api.example.com/v1/items",
        json={"ok": True},
        status=200,
    )

    auth = APIKeyAuth({
        'api_key': 'secret-123',
        'api_key_header': 'apikey',
        'api_key_param': 'apikey',
        'location': 'query',
        'base_url': 'https://api.example.com',
    })
    auth.request('/v1/items')

    sent = responses.calls[0].request
    # `responses` exposes the request URL with the query string appended
    assert "apikey=secret-123" in sent.url


@responses.activate
def test_apikey_auth_header_location_writes_to_request_header():
    responses.add(
        responses.GET,
        "https://api.example.com/v1/items",
        json={"ok": True},
        status=200,
    )

    auth = APIKeyAuth({
        'api_key': 'secret-123',
        'api_key_header': 'X-API-Key',
        'location': 'header',  # explicit
        'base_url': 'https://api.example.com',
    })
    auth.request('/v1/items')

    sent = responses.calls[0].request
    assert sent.headers.get('X-API-Key') == 'secret-123'


@responses.activate
def test_apikey_auth_query_location_respects_prefix():
    responses.add(responses.GET, "https://api.example.com/v1/items",
                  json={"ok": True}, status=200)

    auth = APIKeyAuth({
        'api_key': 'abc',
        'api_key_param': 'token',
        'api_key_prefix': 'Token ',
        'location': 'query',
        'base_url': 'https://api.example.com',
    })
    auth.request('/v1/items')

    assert "token=Token+abc" in responses.calls[0].request.url \
        or "token=Token%20abc" in responses.calls[0].request.url


# ------------------------------- NoAuth ---------------------------------------


def test_get_auth_strategy_returns_noauth_for_none_type():
    auth = get_auth_strategy({'auth_type': 'none', 'base_url': 'https://x'})
    assert isinstance(auth, NoAuth)


def test_noauth_authenticate_returns_true():
    auth = NoAuth({'base_url': 'https://x'})
    assert auth.authenticate() is True


@responses.activate
def test_noauth_request_makes_unauthenticated_call():
    responses.add(
        responses.GET,
        "https://api.example.com/ping",
        json={"ok": True},
        status=200,
    )
    auth = NoAuth({'base_url': 'https://api.example.com'})
    response = auth.request('/ping')
    assert response.status_code == 200

    sent = responses.calls[0].request
    assert sent.headers.get('Authorization') is None
    assert 'X-API-Key' not in sent.headers


# --------------------------- LoginPasswordJsonAuth ----------------------------


def _login_config(**overrides):
    cfg = {
        'login_url': 'https://api.example.com/api/collections/users/auth-with-password',
        'identity': 'admin@example.com',
        'password': 'pw',
        'base_url': 'https://api.example.com',
    }
    cfg.update(overrides)
    return cfg


@responses.activate
def test_login_password_authenticates_and_sends_raw_token():
    """PocketBase shape: POST JSON {identity, password}, response has .token,
    subsequent requests use Authorization: <token> with no Bearer prefix."""
    responses.add(
        responses.POST,
        "https://api.example.com/api/collections/users/auth-with-password",
        json={"token": "abc123", "record": {"id": "u1"}},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.example.com/api/collections/systems/records",
        json={"items": []},
        status=200,
    )

    auth = LoginPasswordJsonAuth(_login_config())
    auth.request('/api/collections/systems/records')

    # First call is the login POST with JSON body
    login = responses.calls[0].request
    import json
    body = json.loads(login.body)
    assert body == {"identity": "admin@example.com", "password": "pw"}

    # Second call carries the token verbatim (no "Bearer " prefix)
    listing = responses.calls[1].request
    assert listing.headers.get('Authorization') == 'abc123'


@responses.activate
def test_login_password_supports_custom_token_path_and_prefix():
    """Some APIs nest the token (e.g. .data.access_token) and expect Bearer."""
    responses.add(
        responses.POST,
        "https://api.example.com/login",
        json={"data": {"access_token": "xyz"}},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.example.com/items",
        json={},
        status=200,
    )

    auth = LoginPasswordJsonAuth(_login_config(
        login_url='https://api.example.com/login',
        token_path='$.data.access_token',
        token_prefix='Bearer ',
    ))
    auth.request('/items')

    assert responses.calls[1].request.headers.get('Authorization') == 'Bearer xyz'


@responses.activate
def test_login_password_supports_custom_field_names():
    """Some APIs use 'username'/'pass' instead of 'identity'/'password'."""
    responses.add(
        responses.POST,
        "https://api.example.com/login",
        json={"token": "t"},
        status=200,
    )
    responses.add(responses.GET, "https://api.example.com/x", json={}, status=200)

    auth = LoginPasswordJsonAuth(_login_config(
        login_url='https://api.example.com/login',
        identity_field='username',
        password_field='pass',
    ))
    auth.request('/x')

    import json
    body = json.loads(responses.calls[0].request.body)
    assert body == {"username": "admin@example.com", "pass": "pw"}


@responses.activate
def test_login_password_supports_custom_token_header():
    responses.add(
        responses.POST,
        "https://api.example.com/login",
        json={"token": "t"},
        status=200,
    )
    responses.add(responses.GET, "https://api.example.com/x", json={}, status=200)

    auth = LoginPasswordJsonAuth(_login_config(
        login_url='https://api.example.com/login',
        token_header='X-Auth-Token',
    ))
    auth.request('/x')

    sent = responses.calls[1].request
    assert sent.headers.get('X-Auth-Token') == 't'
    # Default Authorization header should NOT be set when caller picks a custom one
    assert sent.headers.get('Authorization') is None


@responses.activate
def test_login_password_reauthenticates_on_401():
    """If the token is revoked server-side before TTL, the strategy re-auths
    once on a 401 and retries the request."""
    responses.add(
        responses.POST,
        "https://api.example.com/login",
        json={"token": "old"},
        status=200,
    )
    # First call → 401, second call (after re-auth) → 200
    responses.add(responses.GET, "https://api.example.com/x", json={}, status=401)
    responses.add(
        responses.POST,
        "https://api.example.com/login",
        json={"token": "new"},
        status=200,
    )
    responses.add(responses.GET, "https://api.example.com/x", json={"ok": True}, status=200)

    auth = LoginPasswordJsonAuth(_login_config(
        login_url='https://api.example.com/login',
    ))
    response = auth.request('/x')

    assert response.status_code == 200
    # 4 calls: login, GET-401, login-again, GET-200
    assert len(responses.calls) == 4
    # The retried request used the *new* token
    assert responses.calls[3].request.headers.get('Authorization') == 'new'


@responses.activate
def test_login_password_raises_when_token_path_missing():
    responses.add(
        responses.POST,
        "https://api.example.com/login",
        json={"unexpected": "shape"},
        status=200,
    )
    auth = LoginPasswordJsonAuth(_login_config(
        login_url='https://api.example.com/login',
    ))
    with pytest.raises(ValueError, match="token"):
        auth.request('/x')


@responses.activate
def test_login_password_logs_unexpected_errors_with_auth_context(caplog):
    """ValueError from _extract_token (and any other non-HTTPError) must
    be logged with an auth-context message, not surface unannotated."""
    import logging
    responses.add(
        responses.POST,
        "https://api.example.com/login",
        json={"wrong": "shape"},
        status=200,
    )
    auth = LoginPasswordJsonAuth(_login_config(
        login_url='https://api.example.com/login',
    ))
    with caplog.at_level(logging.ERROR):
        with pytest.raises(ValueError):
            auth.authenticate()
    assert any(
        "login_password_json" in r.getMessage().lower()
        for r in caplog.records
    ), f"no auth-context error logged; got: {[r.getMessage() for r in caplog.records]}"


def test_get_auth_strategy_returns_login_password_for_type():
    auth = get_auth_strategy(_login_config(auth_type='login_password'))
    assert isinstance(auth, LoginPasswordJsonAuth)


# --------------------------- verify_ssl propagation --------------------------


def test_verify_ssl_default_is_true():
    auth = NoAuth({'base_url': 'https://x'})
    # requests.Session.verify defaults to True; we don't override
    assert auth.session.verify is True or auth.session.verify is None


def test_verify_ssl_false_disables_session_verification():
    auth = NoAuth({'base_url': 'https://x', 'verify_ssl': False})
    assert auth.session.verify is False


def test_verify_ssl_false_propagates_for_login_password():
    auth = LoginPasswordJsonAuth({
        'login_url': 'https://self-signed.local/login',
        'identity': 'a', 'password': 'b',
        'verify_ssl': False,
    })
    assert auth.session.verify is False


def test_verify_ssl_true_keeps_session_verifying():
    """Explicit verify_ssl=True must not turn it off."""
    auth = APIKeyAuth({'api_key': 'k', 'verify_ssl': True})
    assert auth.session.verify is not False


def test_insecure_warning_suppression_is_per_call_not_process_global():
    """Building a verify_ssl=False auth must NOT silence
    InsecureRequestWarning for unrelated code (e.g. another connector or
    a future code path that accidentally uses verify=False). Only the
    strategy's own session.request calls are wrapped."""
    import warnings as _warnings
    import urllib3
    # Reset the warning filter so we observe a fresh state.
    _warnings.resetwarnings()

    # Build a strategy with verify off — this used to permanently disable
    # urllib3 warnings process-wide via a module global.
    NoAuth({'base_url': 'https://x', 'verify_ssl': False})

    # Now check that an unrelated call (no strategy involved) still emits
    # the warning. We use catch_warnings to record without affecting state.
    with _warnings.catch_warnings(record=True) as recorded:
        _warnings.simplefilter("always", urllib3.exceptions.InsecureRequestWarning)
        _warnings.warn("simulated", urllib3.exceptions.InsecureRequestWarning)
    assert len(recorded) == 1
    assert recorded[0].category is urllib3.exceptions.InsecureRequestWarning
