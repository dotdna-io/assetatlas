# SPDX-License-Identifier: AGPL-3.0-or-later
"""Wiring guards for the MCP Streamable HTTP app (#1158).

The API-key branch of `MCPJWTTokenVerifier` reads the caller's address from a
context variable that only `ClientAddressMiddleware` sets, and the nginx fix
depends on the app mounting `/mcp` with no trailing slash. Neither is visible
from the verifier's own tests.
"""

from pydantic import AnyHttpUrl
from mcp.server.auth.settings import AuthSettings
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from src.mcp.auth import ClientAddressMiddleware, MCPJWTTokenVerifier
from src.mcp.server import _AssetAtlasFastMCP, mcp

SECRET = 'test-secret-key-of-at-least-32-bytes'


def test_create_server_returns_the_subclass_that_installs_the_middleware():
    # `mcp` is the module-level stdio instance, built by create_server().
    assert isinstance(mcp, _AssetAtlasFastMCP)


def test_client_address_middleware_runs_outside_the_sdk_auth_middleware():
    url = AnyHttpUrl('http://localhost:8100')
    server = _AssetAtlasFastMCP(
        name='test',
        token_verifier=MCPJWTTokenVerifier(secret_key=SECRET),
        auth=AuthSettings(
            issuer_url=url, resource_server_url=url, required_scopes=[]
        ),
    )

    names = [m.cls.__name__ for m in server.streamable_http_app().user_middleware]

    # Starlette applies user_middleware outermost-first, so ours has to sit at
    # index 0: the address must be recorded before verify_token is called.
    assert names[0] == ClientAddressMiddleware.__name__
    assert 'AuthenticationMiddleware' in names


def test_proxy_headers_middleware_runs_inside_client_address_and_trusts_nginx():
    # X-Forwarded-Proto must be applied before Starlette's redirect_slashes
    # builds the `/mcp/` 307 (#1448), but after ClientAddressMiddleware has
    # read the untouched scope.
    url = AnyHttpUrl('http://localhost:8100')
    server = _AssetAtlasFastMCP(
        name='test',
        token_verifier=MCPJWTTokenVerifier(secret_key=SECRET),
        auth=AuthSettings(
            issuer_url=url, resource_server_url=url, required_scopes=[]
        ),
    )

    middleware = server.streamable_http_app().user_middleware
    names = [m.cls.__name__ for m in middleware]

    assert names[:2] == [ClientAddressMiddleware.__name__,
                         ProxyHeadersMiddleware.__name__]
    assert middleware[1].kwargs == {'trusted_hosts': '*'}


def test_the_app_mounts_mcp_without_a_trailing_slash():
    # Why nginx needs `location = /mcp`: the app answers `/mcp` exactly, and
    # `/mcp/` gets Starlette's own 307 redirect_slashes bounce, rebuilt from
    # the Host header and X-Forwarded-Proto (#1158, #1448).
    #
    # Built WITH auth, because that is the only configuration nginx ever
    # fronts: `streamable_http_app()` takes a different branch when a token
    # verifier is configured, and it then also mounts
    # `/.well-known/oauth-protected-resource`. An unauthenticated instance
    # would assert a route table the deployed server never actually has.
    url = AnyHttpUrl('http://localhost:8100')
    app = _AssetAtlasFastMCP(
        name='test',
        token_verifier=MCPJWTTokenVerifier(secret_key=SECRET),
        auth=AuthSettings(
            issuer_url=url, resource_server_url=url, required_scopes=[]
        ),
    ).streamable_http_app()

    paths = [r.path for r in app.routes]

    assert '/mcp' in paths
    assert '/mcp/' not in paths
