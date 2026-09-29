# SPDX-License-Identifier: AGPL-3.0-or-later
"""The MCP Streamable HTTP app as nginx fronts it (#1448).

Every test drives the real app from ``create_server(transport="streamable-http")``
through Starlette's ``TestClient`` — lifespan included — with the public
``Host`` nginx forwards. The DB is a mock: ``PostgreSQLConnectionManager`` and
``PostgreSQLAdapter`` are patched in ``src.mcp.context``.
"""

import logging
from unittest.mock import MagicMock

import jwt
import pytest
from starlette.testclient import TestClient

import src.mcp.auth as auth_mod
import src.mcp.context as ctx_mod
from src.mcp.server import create_server

SECRET = 'test-secret-key-of-at-least-32-bytes'
VALID_KEY = 'aa_' + 'k' * 40
PUBLIC_HOST = 'assetatlas.example.com:8443'


def jwt_token():
    return jwt.encode({'sub': 'user-1', 'role': 'admin'}, SECRET, algorithm='HS256')


def rpc(method, params=None, id_=1):
    body = {'jsonrpc': '2.0', 'id': id_, 'method': method}
    if params is not None:
        body['params'] = params
    return body


INITIALIZE = rpc('initialize', {
    'protocolVersion': '2025-06-18',
    'capabilities': {},
    'clientInfo': {'name': 'proxy-test', 'version': '0'},
})


def headers(token, **extra):
    h = {
        'Host': PUBLIC_HOST,
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream',
    }
    h.update(extra)
    return h


class FakeDB:
    """Answers the one tool query the tests call (`list_tag_rules`)."""

    def execute_query(self, query, params=None, fetch=True):
        if 'FROM tag_rules' in query:
            return [{'id': 1, 'name': 'proxy-rule', 'type': 'static',
                     'enabled': True, 'description': None,
                     'execution_count': 0, 'last_executed_at': None,
                     'last_execution_duration_ms': None,
                     'last_execution_assets_matched': None,
                     'last_execution_assets_passed': None,
                     'last_execution_assets_failed': None}]
        return []


class FakeDatabase:
    """A patched-in pool: counts constructions, closes, and can be down."""

    def __init__(self):
        self.healthy = True
        self.managers = []
        self.conn_cls = MagicMock(name='PostgreSQLConnectionManager',
                                  side_effect=self._new_manager)

    def _new_manager(self, **kwargs):
        manager = MagicMock(name=f'conn_manager_{len(self.managers)}')
        manager.health_check.side_effect = lambda: (
            {'healthy': True, 'database_version': 'test'} if self.healthy
            else {'healthy': False, 'error': 'Connection refused'})
        self.managers.append(manager)
        return manager

    @property
    def closes(self):
        return sum(m.close_all_connections.call_count for m in self.managers)


@pytest.fixture
def database(monkeypatch):
    """A mocked DB, one accepted `aa_` key, an isolated app context."""
    monkeypatch.setattr(ctx_mod, '_app_context', None)
    monkeypatch.setattr(ctx_mod, '_build_lock', None)
    monkeypatch.setattr(ctx_mod, '_RuntimeSuggestionService', None)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://mcp-test')
    monkeypatch.delenv('REDIS_URL', raising=False)
    monkeypatch.setenv('JWT_SECRET_KEY', SECRET)

    fake = FakeDatabase()
    monkeypatch.setattr(ctx_mod, 'PostgreSQLConnectionManager', fake.conn_cls)
    monkeypatch.setattr(ctx_mod, 'PostgreSQLAdapter',
                        MagicMock(return_value=FakeDB()))

    def validate(db, plaintext_key, client_address):
        if plaintext_key != VALID_KEY:
            return None
        return {'id': 'key-1', 'user_id': 'user-1', 'role': 'admin',
                'expires_at': None}

    monkeypatch.setattr(auth_mod, '_validate_and_stamp', validate)
    return fake


def http_app():
    """A fresh server per app: `session_manager.run()` runs once per instance."""
    server = create_server(transport='streamable-http', port=8100)
    return server, server.streamable_http_app()


def test_initialize_with_the_public_host_is_not_rejected(database):
    # FastMCP enables a localhost-only Host allowlist when it is built for a
    # loopback host with no transport_security; nginx forwards the public
    # Host, so this used to be a 421 "Invalid Host header".
    _, app = http_app()
    with TestClient(app) as client:
        resp = client.post('/mcp', json=INITIALIZE, headers=headers(jwt_token()))

    assert resp.status_code == 200, resp.text
    assert resp.json()['result']['serverInfo']['name'] == 'AssetAtlas'


def test_http_server_disables_the_host_check_explicitly():
    server = create_server(transport='streamable-http', port=8100)

    security = server.settings.transport_security
    assert security is not None
    assert security.enable_dns_rebinding_protection is False
    assert server.settings.host == '0.0.0.0'
    assert server.settings.port == 8100


# -- one app context per process ----------------------------------------------


def test_first_request_of_a_fresh_process_authenticates_an_api_key(database):
    # No other client, no other session: the context was built at startup,
    # not by some earlier session's lifespan.
    _, app = http_app()
    with TestClient(app) as client:
        assert ctx_mod.get_app_context() is not None
        resp = client.post('/mcp', json=INITIALIZE, headers=headers(VALID_KEY))

    assert resp.status_code == 200, resp.text
    assert database.conn_cls.call_count == 1


@pytest.mark.parametrize('authorization', [
    None, 'Bearer aa_' + 'x' * 40, 'Bearer not-a-token', 'Basic abc',
])
def test_missing_or_invalid_bearer_token_is_a_401(database, authorization):
    h = headers(VALID_KEY)
    del h['Authorization']
    if authorization is not None:
        h['Authorization'] = authorization

    _, app = http_app()
    with TestClient(app) as client:
        resp = client.post('/mcp', json=INITIALIZE, headers=h)

    assert resp.status_code == 401


def test_db_down_at_startup_serves_401s_then_recovers_without_a_restart(
        database, caplog):
    database.healthy = False
    _, app = http_app()
    with caplog.at_level(logging.ERROR, logger='src.mcp.context'):
        with TestClient(app) as client:
            # The failed startup build did not stop the server...
            assert ctx_mod.get_app_context() is None
            assert any('could not be built at startup' in r.getMessage()
                       for r in caplog.records)

            # ...a key request while the DB is still down is refused cleanly,
            down = client.post('/mcp', json=INITIALIZE,
                               headers=headers(VALID_KEY))
            assert down.status_code == 401

            # ...and once it is back, the next request builds and succeeds.
            database.healthy = True
            up = client.post('/mcp', json=INITIALIZE,
                             headers=headers(VALID_KEY))
            assert up.status_code == 200, up.text
            assert ctx_mod.get_app_context() is not None

    # Startup, the 401, the recovery; each failed build closed its own pool.
    assert database.conn_cls.call_count == 3
    assert [m.close_all_connections.call_count
            for m in database.managers] == [1, 1, 1]
    assert ctx_mod.get_app_context() is None


def test_shutdown_closes_the_pool_once_and_clears_the_context(database):
    _, app = http_app()
    with TestClient(app) as client:
        for _ in range(3):
            resp = client.post('/mcp', json=INITIALIZE,
                               headers=headers(VALID_KEY))
            assert resp.status_code == 200, resp.text
        assert database.closes == 0

    assert database.conn_cls.call_count == 1
    assert database.closes == 1
    assert ctx_mod.get_app_context() is None
    assert ctx_mod._build_lock is None


def test_the_process_scope_wraps_the_sdk_lifespan_once(database):
    # Two back-to-back apps in one process, each with its own lifespan: the
    # scope is not left behind, and each app builds its own context.
    for _ in range(2):
        _, app = http_app()
        with TestClient(app) as client:
            resp = client.post('/mcp', json=INITIALIZE,
                               headers=headers(VALID_KEY))
            assert resp.status_code == 200, resp.text

    assert database.conn_cls.call_count == 2
    assert database.closes == 2


# -- stateless transport ------------------------------------------------------


def tool_names(resp):
    return {t['name'] for t in resp.json()['result']['tools']}


def call_text(resp):
    return resp.json()['result']['content'][0]['text']


def test_an_api_key_client_can_initialize_list_and_call_through_the_proxy(
        database):
    # The issue's acceptance, in one client with nothing else open.
    _, app = http_app()
    with TestClient(app) as client:
        init = client.post('/mcp', json=INITIALIZE, headers=headers(VALID_KEY))
        listed = client.post('/mcp', json=rpc('tools/list', id_=2),
                             headers=headers(VALID_KEY))
        called = client.post('/mcp', json=rpc('tools/call', {
            'name': 'list_tag_rules', 'arguments': {},
        }, id_=3), headers=headers(VALID_KEY))

    assert init.status_code == 200, init.text
    assert listed.status_code == 200, listed.text
    assert 'list_tag_rules' in tool_names(listed)
    assert called.status_code == 200, called.text
    assert called.json()['result']['isError'] is False
    assert 'proxy-rule' in call_text(called)


def test_http_transport_is_stateless_and_hands_out_no_session():
    server = create_server(transport='streamable-http', port=8100)
    assert server.settings.stateless_http is True
    assert server.settings.json_response is True


def test_repeated_client_cycles_grow_neither_sessions_nor_pools(
        database, monkeypatch):
    # A client that opens a "session" per tool call — initialize, call,
    # DELETE — alternating with a second client. Nothing accumulates, and
    # one client finishing never breaks the next request from another.
    server, app = http_app()
    other_key = 'aa_' + 'o' * 40
    accepted = {VALID_KEY, other_key}

    def validate(db, plaintext_key, client_address):
        if plaintext_key not in accepted:
            return None
        return {'id': plaintext_key[-1], 'user_id': 'u', 'role': 'admin',
                'expires_at': None}

    monkeypatch.setattr(auth_mod, '_validate_and_stamp', validate)
    with TestClient(app) as client:
        published = ctx_mod.get_app_context()
        for i in range(12):
            key = VALID_KEY if i % 2 == 0 else other_key
            init = client.post('/mcp', json=INITIALIZE, headers=headers(key))
            assert init.status_code == 200, init.text
            assert 'mcp-session-id' not in init.headers

            called = client.post('/mcp', json=rpc('tools/call', {
                'name': 'list_tag_rules', 'arguments': {},
            }, id_=i), headers=headers(key))
            assert called.status_code == 200, called.text
            assert 'proxy-rule' in call_text(called)

            ended = client.delete('/mcp', headers=headers(key))
            # Stateless: there is no session to terminate.
            assert ended.status_code == 405

            assert server.session_manager._server_instances == {}
            assert ctx_mod.get_app_context() is published
            assert database.closes == 0

        assert database.conn_cls.call_count == 1

    assert database.closes == 1
    assert ctx_mod.get_app_context() is None


def test_a_jwt_request_while_the_db_is_down_fails_and_recovers(database):
    # JWT auth needs no DB, so this request reaches the per-request lifespan,
    # which cannot build the context: the request fails with a JSON-RPC
    # internal error (as a per-session lifespan failure always did) rather
    # than hanging, and the next one after the DB returns succeeds.
    database.healthy = False
    _, app = http_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        down = client.post('/mcp', json=rpc('tools/list'),
                           headers=headers(jwt_token()))
        database.healthy = True
        up = client.post('/mcp', json=rpc('tools/list'),
                         headers=headers(jwt_token()))

    assert down.status_code == 500
    assert down.json()['error']['code'] == -32603
    assert up.status_code == 200, up.text
    assert 'list_tag_rules' in tool_names(up)


# -- forwarded scheme ---------------------------------------------------------


def test_trailing_slash_redirect_keeps_the_forwarded_scheme_and_port(database):
    _, app = http_app()
    with TestClient(app) as client:
        resp = client.post(
            '/mcp/', json=INITIALIZE, follow_redirects=False,
            headers=headers(VALID_KEY, **{'X-Forwarded-Proto': 'https'}),
        )

    assert resp.status_code == 307
    assert resp.headers['location'] == f'https://{PUBLIC_HOST}/mcp'
