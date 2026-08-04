# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test that dead connections are properly returned to the pool."""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock


class TestReleaseDeadConnection:
    """Verify _release_connection handles dead (closed) connections."""

    def _make_manager(self):
        """Create a PostgreSQLConnectionManager with a mocked pool."""
        with patch(
            "src.database.postgres_connection.PostgreSQLConnectionManager._create_pool"
        ):
            from src.database.postgres_connection import PostgreSQLConnectionManager

            mgr = PostgreSQLConnectionManager.__new__(PostgreSQLConnectionManager)
            mgr.min_connections = 2
            mgr.max_connections = 5
            mgr._pool_lock = __import__("threading").Lock()
            mgr._scope_state = __import__("threading").local()

            mock_pool = MagicMock()
            mock_pool.closed = False
            mgr._pool = mock_pool
            return mgr, mock_pool

    def test_live_connection_returned_normally(self):
        mgr, mock_pool = self._make_manager()
        conn = MagicMock()
        conn.closed = 0  # psycopg2 uses 0 for open

        mgr._release_connection(conn)

        mock_pool.putconn.assert_called_once_with(conn)

    def test_dead_connection_returned_with_close_flag(self):
        """Dead connections must be returned with close=True so the pool
        frees the slot. Without this, pool slots leak permanently."""
        mgr, mock_pool = self._make_manager()
        conn = MagicMock()
        conn.closed = 1  # psycopg2 uses non-zero for closed

        mgr._release_connection(conn)

        mock_pool.putconn.assert_called_once_with(conn, close=True)
