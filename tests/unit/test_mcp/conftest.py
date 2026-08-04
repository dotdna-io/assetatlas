# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared fixtures for MCP server tests."""

from unittest.mock import MagicMock, patch

import pytest

from mcp.server.fastmcp import FastMCP


@pytest.fixture
def mock_db():
    """Mock PostgreSQLAdapter that returns configurable query results."""
    db = MagicMock()
    db.execute_query = MagicMock(return_value=[])
    return db


@pytest.fixture
def mock_ctx(mock_db):
    """Mock MCP Context with lifespan_context.db pointing to mock_db."""
    ctx = MagicMock()
    ctx.request_context.lifespan_context.db = mock_db
    # field_id_for_display_name returns None by default (no per-source fields
    # in unit tests); individual tests can override via resolver mock.
    resolver = ctx.request_context.lifespan_context.field_resolver
    resolver.catalog.return_value.field_id_for_display_name.return_value = None
    return ctx


@pytest.fixture
def mcp_server():
    """A clean FastMCP server instance for testing registration."""
    return FastMCP("test-server")


@pytest.fixture
def app_context_with_mock_db(mock_db):
    """Patch get_app_context to return a mock AppContext with mock_db."""
    from src.mcp.context import AppContext

    app_ctx = AppContext(
        db=mock_db,
        conn_manager=MagicMock(),
    )
    with patch("src.mcp.resources.schema.get_app_context", return_value=app_ctx), patch(
        "src.mcp.resources.config.get_app_context", return_value=app_ctx
    ), patch("src.mcp.resources.stats.get_app_context", return_value=app_ctx):
        yield app_ctx
