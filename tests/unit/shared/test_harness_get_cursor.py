# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shared harness's ``connection_manager.get_cursor`` must stay a working
context manager yielding ``(cursor, conn)`` (#1298).

Repositories open cursors via
``with conn_manager.get_cursor(...) as (cursor, conn):`` — notably
``SessionRepository.get_session_by_access_jti`` in the JWT
``token_in_blocklist_loader``.  A bare MagicMock raises on that unpack, the
loader fails closed (token treated as revoked), and every authed request
401s.  Two mocks must stay wired: the one ``_fake_init_database`` installs —
captured by ``_init_jwt``'s ``SessionRepository`` at app-creation time — and
the ``_SERVICE_ATTRS`` bulk-override one on ``app.connection_manager``, the
latter across the ``client`` fixture's per-test
``reset_mock(return_value=True)``, which cascades into child mocks and wipes
the wiring.
"""

from unittest.mock import MagicMock

from flask import Flask

from tests.conftest import _fake_init_database


def test_shared_harness_get_cursor_yields_cursor_conn(client, blueprint_app):
    # The mock _init_jwt's SessionRepository captures (the blocklist-loader
    # path), exercised directly on a throwaway app.
    fresh_app = Flask("harness_get_cursor_check")
    _fake_init_database(fresh_app)
    with fresh_app.connection_manager.get_cursor() as (cursor, conn):
        assert isinstance(cursor, MagicMock)
        assert isinstance(conn, MagicMock)

    # The bulk-override mock on the shared app — after client's per-test
    # reset, which is exactly where the wiring used to be lost.
    with blueprint_app.connection_manager.get_cursor() as (cursor, conn):
        assert isinstance(cursor, MagicMock)
        assert isinstance(conn, MagicMock)
