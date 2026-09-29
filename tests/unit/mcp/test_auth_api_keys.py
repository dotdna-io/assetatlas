# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the MCP HTTP transport's bearer credential verification.

Covers the API-key (`aa_…`) branch added in #1158 and guards the JWT branch
against regression.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest

from src.mcp.auth import (
    UNKNOWN_CLIENT_ADDRESS,
    ClientAddressMiddleware,
    MCPJWTTokenVerifier,
    scope_client_address,
)

SECRET = 'test-secret-key-of-at-least-32-bytes'
VALID_KEY = 'aa_' + 'a' * 40
KEY_HASH = hashlib.sha256(VALID_KEY.encode()).hexdigest()

FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)
PAST = datetime(2020, 1, 1, tzinfo=timezone.utc)


def key_row(**overrides):
    """A row as `ApiKeyRepository.get_by_hash` returns it (key JOIN user)."""
    row = {
        'id': 'key-uuid',
        'user_id': 'user-uuid',
        'name': 'MCP client',
        'key_prefix': VALID_KEY[:8],
        'key_hash': KEY_HASH,
        'expires_at': FUTURE,
        'is_active': True,
        'last_used_at': None,
        'last_used_ip': None,
        'created_at': PAST,
        'username': 'admin',
        'role': 'admin',
        'user_is_active': True,
    }
    row.update(overrides)
    return row


@pytest.fixture
def verifier():
    return MCPJWTTokenVerifier(secret_key=SECRET)


@pytest.fixture
def db():
    """A DB adapter whose `execute_query` answers the repository's SELECT."""
    return MagicMock()


def app_context(db):
    """A stand-in for the lifespan-published AppContext."""
    ctx = MagicMock()
    ctx.db = db
    return ctx


def ready(ctx):
    """Patch the verifier's context lookup to hand back `ctx`."""
    return patch('src.mcp.auth.ensure_app_context',
                 new=AsyncMock(return_value=ctx))


def rows_for(row):
    """execute_query side effect: the lookup returns `row`, the stamp nothing."""
    def _execute(sql, params=None, **kwargs):
        if 'UPDATE api_keys' in sql:
            return None
        return [row] if row is not None else []
    return _execute


class TestApiKeyBranch:
    async def test_valid_key_returns_access_token_with_owner_role(self, verifier, db):
        db.execute_query.side_effect = rows_for(key_row())

        with ready(app_context(db)):
            token = await verifier.verify_token(VALID_KEY)

        assert token is not None
        assert token.token == VALID_KEY
        assert token.client_id == 'user-uuid'
        assert token.scopes == ['admin']

    async def test_valid_key_carries_the_keys_own_expiry(self, verifier, db):
        expires = datetime(2030, 6, 1, 12, 0, tzinfo=timezone.utc)
        db.execute_query.side_effect = rows_for(key_row(expires_at=expires))

        with ready(app_context(db)):
            token = await verifier.verify_token(VALID_KEY)

        # Epoch seconds, not None — the SDK drops the session once it passes.
        assert token.expires_at == int(expires.timestamp())

    async def test_naive_expiry_is_read_as_utc(self, verifier, db):
        naive = datetime(2030, 6, 1, 12, 0)
        db.execute_query.side_effect = rows_for(key_row(expires_at=naive))

        with ready(app_context(db)):
            token = await verifier.verify_token(VALID_KEY)

        assert token.expires_at == int(
            naive.replace(tzinfo=timezone.utc).timestamp()
        )

    async def test_valid_key_stamps_last_used(self, verifier, db):
        db.execute_query.side_effect = rows_for(key_row())

        with ready(app_context(db)):
            await verifier.verify_token(VALID_KEY)

        stamps = [
            call for call in db.execute_query.call_args_list
            if 'UPDATE api_keys' in call.args[0]
        ]
        assert len(stamps) == 1, "last_used_at was not stamped"
        assert 'last_used_at = NOW()' in stamps[0].args[0]
        assert stamps[0].args[1]['key_id'] == 'key-uuid'

    async def test_a_failed_stamp_does_not_reject_the_key(self, verifier, db):
        def _execute(sql, params=None, **kwargs):
            if 'UPDATE api_keys' in sql:
                raise RuntimeError('last_used_ip write failed')
            return [key_row()]

        db.execute_query.side_effect = _execute

        with ready(app_context(db)):
            token = await verifier.verify_token(VALID_KEY)

        assert token is not None

    async def test_a_fresh_last_used_at_skips_the_stamp(self, verifier, db):
        """Stamped 10 s ago: inside the 60 s throttle window, so the request
        costs only the SELECT — no UPDATE at all."""
        fresh = datetime.now(timezone.utc) - timedelta(seconds=10)
        db.execute_query.side_effect = rows_for(key_row(last_used_at=fresh))

        with ready(app_context(db)):
            token = await verifier.verify_token(VALID_KEY)

        assert token is not None
        stamps = [
            call for call in db.execute_query.call_args_list
            if 'UPDATE api_keys' in call.args[0]
        ]
        assert stamps == []

    async def test_a_stale_last_used_at_stamps_again(self, verifier, db):
        stale = datetime.now(timezone.utc) - timedelta(seconds=120)
        db.execute_query.side_effect = rows_for(key_row(last_used_at=stale))

        with ready(app_context(db)):
            token = await verifier.verify_token(VALID_KEY)

        assert token is not None
        stamps = [
            call for call in db.execute_query.call_args_list
            if 'UPDATE api_keys' in call.args[0]
        ]
        assert len(stamps) == 1

    async def test_revoked_key_returns_none(self, verifier, db):
        db.execute_query.side_effect = rows_for(key_row(is_active=False))

        with ready(app_context(db)):
            assert await verifier.verify_token(VALID_KEY) is None

    async def test_expired_key_returns_none(self, verifier, db):
        expired = datetime.now(timezone.utc) - timedelta(days=1)
        db.execute_query.side_effect = rows_for(key_row(expires_at=expired))

        with ready(app_context(db)):
            assert await verifier.verify_token(VALID_KEY) is None

    async def test_key_of_a_deactivated_user_returns_none(self, verifier, db):
        db.execute_query.side_effect = rows_for(key_row(user_is_active=False))

        with ready(app_context(db)):
            assert await verifier.verify_token(VALID_KEY) is None

    async def test_unknown_key_returns_none(self, verifier, db):
        db.execute_query.side_effect = rows_for(None)

        with ready(app_context(db)):
            assert await verifier.verify_token(VALID_KEY) is None

    async def test_missing_app_context_returns_none_not_an_exception(self, verifier):
        # The context cannot be built (DB still down, or no process scope):
        # a 401, never a raise the SDK would surface as a 500.
        unavailable = AsyncMock(side_effect=ConnectionError('DB down'))
        with patch('src.mcp.auth.ensure_app_context', new=unavailable):
            assert await verifier.verify_token(VALID_KEY) is None

    async def test_app_context_without_a_db_returns_none(self, verifier):
        ctx = MagicMock()
        ctx.db = None

        with ready(ctx):
            assert await verifier.verify_token(VALID_KEY) is None

    async def test_db_failure_returns_none_not_an_exception(self, verifier, db):
        db.execute_query.side_effect = RuntimeError('connection pool exhausted')

        with ready(app_context(db)):
            assert await verifier.verify_token(VALID_KEY) is None

    async def test_non_key_garbage_never_reaches_the_database(self, verifier, db):
        with ready(app_context(db)):
            assert await verifier.verify_token('not-a-jwt-and-not-a-key') is None

        db.execute_query.assert_not_called()


class TestJWTBranchUnchanged:
    async def test_valid_jwt_still_verifies(self, verifier):
        token = jwt.encode(
            {'sub': 'user-uuid', 'username': 'alice', 'role': 'user'},
            SECRET,
            algorithm='HS256',
        )

        access = await verifier.verify_token(token)

        assert access.client_id == 'user-uuid'
        assert access.scopes == ['user']
        assert access.expires_at is None

    async def test_jwt_signed_with_another_secret_returns_none(self, verifier):
        token = jwt.encode(
            {'sub': 'user-uuid'},
            'another-secret-of-at-least-32-bytes-long',
            algorithm='HS256',
        )

        assert await verifier.verify_token(token) is None

    async def test_expired_jwt_returns_none(self, verifier):
        token = jwt.encode(
            {'sub': 'user-uuid', 'exp': datetime(2020, 1, 1, tzinfo=timezone.utc)},
            SECRET,
            algorithm='HS256',
        )

        assert await verifier.verify_token(token) is None


class TestClientAddress:
    def test_prefers_nginx_x_real_ip(self):
        scope = {
            'type': 'http',
            'headers': [(b'x-real-ip', b'10.1.2.3')],
            'client': ('172.18.0.5', 51234),
        }

        assert scope_client_address(scope) == '10.1.2.3'

    def test_falls_back_to_the_socket_peer(self):
        scope = {'type': 'http', 'headers': [], 'client': ('172.18.0.5', 51234)}

        assert scope_client_address(scope) == '172.18.0.5'

    def test_unparseable_header_falls_back_to_the_socket_peer(self):
        scope = {
            'type': 'http',
            'headers': [(b'x-real-ip', b'not-an-ip')],
            'client': ('172.18.0.5', 51234),
        }

        assert scope_client_address(scope) == '172.18.0.5'

    def test_no_client_at_all_yields_the_placeholder(self):
        assert scope_client_address({'type': 'http'}) == UNKNOWN_CLIENT_ADDRESS

    async def test_middleware_makes_the_address_reach_record_usage(self, verifier, db):
        db.execute_query.side_effect = rows_for(key_row())
        scope = {
            'type': 'http',
            'headers': [(b'x-real-ip', b'10.1.2.3')],
            'client': ('172.18.0.5', 51234),
        }

        async def inner_app(_scope, _receive, _send):
            with ready(app_context(db)):
                await verifier.verify_token(VALID_KEY)

        await ClientAddressMiddleware(inner_app)(scope, None, None)

        stamp = next(
            call for call in db.execute_query.call_args_list
            if 'UPDATE api_keys' in call.args[0]
        )
        assert stamp.args[1]['ip'] == '10.1.2.3'

    async def test_middleware_passes_non_http_scopes_straight_through(self):
        seen = {}

        async def inner_app(scope, _receive, _send):
            seen['type'] = scope['type']

        await ClientAddressMiddleware(inner_app)({'type': 'lifespan'}, None, None)

        assert seen['type'] == 'lifespan'

    async def test_address_does_not_leak_between_requests(self, verifier, db):
        db.execute_query.side_effect = rows_for(key_row())

        async def inner_app(_scope, _receive, _send):
            return None

        await ClientAddressMiddleware(inner_app)(
            {'type': 'http', 'headers': [(b'x-real-ip', b'10.1.2.3')]}, None, None
        )

        # Outside any request the contextvar is back at its default, so a key
        # verified off-request stamps the placeholder rather than the last
        # caller's address.
        with ready(app_context(db)):
            await verifier.verify_token(VALID_KEY)

        stamp = next(
            call for call in db.execute_query.call_args_list
            if 'UPDATE api_keys' in call.args[0]
        )
        assert stamp.args[1]['ip'] == UNKNOWN_CLIENT_ADDRESS
