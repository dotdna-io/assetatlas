# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared fixtures for src/auth/* unit tests."""
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_conn_manager():
    """Mock connection_manager whose get_cursor() yields (cursor, conn).

    Used by every repository / service test that exercises
    `with self.conn_manager.get_cursor(...) as (cursor, conn)`.
    Returns the (cm, cursor, conn) triple so tests can drive both
    `cursor.execute.call_args` assertions and `conn.commit` checks.
    """
    cm = MagicMock()
    cursor = MagicMock()
    conn = MagicMock()
    cm.get_cursor.return_value.__enter__.return_value = (cursor, conn)
    cm.get_cursor.return_value.__exit__.return_value = False
    return cm, cursor, conn
