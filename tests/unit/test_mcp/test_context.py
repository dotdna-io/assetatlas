# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for MCP AppContext and lifespan."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.mcp.context import AppContext, get_app_context


class TestAppContext:
    """Test AppContext dataclass."""

    def test_creation(self):
        db = MagicMock()
        conn = MagicMock()
        ctx = AppContext(db=db, conn_manager=conn)
        assert ctx.db is db
        assert ctx.conn_manager is conn
        assert ctx.redis is None

    def test_creation_with_redis(self):
        db = MagicMock()
        conn = MagicMock()
        redis = MagicMock()
        ctx = AppContext(db=db, conn_manager=conn, redis=redis)
        assert ctx.redis is redis


class TestGetAppContext:
    """Test the module-level get_app_context function."""

    def test_returns_none_before_lifespan(self):
        import src.mcp.context as ctx_mod

        original = ctx_mod._app_context
        ctx_mod._app_context = None
        try:
            assert get_app_context() is None
        finally:
            ctx_mod._app_context = original

    def test_returns_context_when_set(self):
        import src.mcp.context as ctx_mod

        original = ctx_mod._app_context
        mock_ctx = AppContext(db=MagicMock(), conn_manager=MagicMock())
        ctx_mod._app_context = mock_ctx
        try:
            assert get_app_context() is mock_ctx
        finally:
            ctx_mod._app_context = original


class TestLifespan:
    """Test the app_lifespan async context manager."""

    def test_lifespan_requires_database_url(self):
        from src.mcp.context import app_lifespan

        async def _run():
            with patch.dict("os.environ", {}, clear=True):
                async with app_lifespan(MagicMock()):
                    pass

        with pytest.raises(ValueError, match="DATABASE_URL"):
            asyncio.run(_run())

    def test_lifespan_sets_and_clears_app_context(self):
        import src.mcp.context as ctx_mod
        from src.mcp.context import app_lifespan

        mock_conn_manager = MagicMock()
        mock_conn_manager.health_check.return_value = {
            "healthy": True,
            "database_version": "test",
        }

        results = {}

        async def _run():
            with patch.dict("os.environ", {"DATABASE_URL": "postgresql://test"}), patch(
                "src.mcp.context.PostgreSQLConnectionManager",
                return_value=mock_conn_manager,
            ), patch("src.mcp.context.PostgreSQLAdapter"):

                async with app_lifespan(MagicMock()) as app_ctx:
                    results["during"] = ctx_mod._app_context is app_ctx
                    results["is_appctx"] = isinstance(app_ctx, AppContext)

                results["after"] = ctx_mod._app_context is None

        asyncio.run(_run())
        assert results["during"] is True
        assert results["is_appctx"] is True
        assert results["after"] is True

    def test_lifespan_fails_on_unhealthy_db(self):
        from src.mcp.context import app_lifespan

        mock_conn_manager = MagicMock()
        mock_conn_manager.health_check.return_value = {
            "healthy": False,
            "error": "Connection refused",
        }

        async def _run():
            with patch.dict("os.environ", {"DATABASE_URL": "postgresql://test"}), patch(
                "src.mcp.context.PostgreSQLConnectionManager",
                return_value=mock_conn_manager,
            ), patch("src.mcp.context.PostgreSQLAdapter"):

                async with app_lifespan(MagicMock()):
                    pass

        with pytest.raises(ConnectionError, match="health check failed"):
            asyncio.run(_run())


class _FakeGroupDB:
    """db_adapter stub returning the integration-group mapping + extended-
    attribute discovery rows a no-app-context resolver build needs (everything
    else empty)."""

    def execute_query(self, query, params=None, fetch=True):
        if "integration_groups" in query:      # IntegrationGroupRepository.get_group_mapping
            return [{"integration_name": "Corp SentinelOne", "group_name": "SentinelOne"}]
        if "jsonb_object_keys" in query:        # catalog extended_attribute discovery
            return [{"source_name": "Corp SentinelOne", "field_name": "siteId"}]
        return []


class TestBuildFieldResolver:
    """The MCP server runs as its own process with NO Flask app context, so its
    FieldResolver must read the integration-group mapping straight from the DB.
    With the old empty-{} provider, resolving a group:<group>:<attr> field raised
    InvalidFieldError and the chatbot could neither filter nor chart by a grouped
    integration attribute (#792)."""

    def test_resolves_integration_group_field(self):
        from src.mcp.context import _build_field_resolver
        from src.modules.fields.models import IntegrationGroupField

        resolver = _build_field_resolver(_FakeGroupDB())

        resolved = resolver.resolve(
            IntegrationGroupField(
                kind="integration_group", group="SentinelOne", attribute="siteId"
            )
        )
        where = resolved.where_expr()
        assert "extended_attributes" in where
        assert "Corp SentinelOne" in where
        assert "siteId" in where


class TestServerRegistration:
    """Test the full server module-level registration."""

    # The MCP tool surface must stay read-only. #1158 made an `aa_` API key an
    # admin-level READ credential over the Streamable HTTP transport, and that
    # grant is only defensible while no tool creates, updates or deletes — so
    # this is a security invariant, not a naming convention. A verb prefix is
    # how it is enforced cheaply; a tool that cannot be named that way has to
    # be justified by hand below.
    READ_ONLY_PREFIXES = ("get_", "list_", "search_", "aggregate_")
    READ_ONLY_EXCEPTIONS = {
        # Read-only: delegates to SuggestionService.get_suggestions()
        # (src/mcp/tools/suggestions.py).
        "semantic_suggest",
    }

    def test_all_tools_are_read_only(self):
        from src.mcp.server import mcp

        offenders = sorted(
            name
            for name in mcp._tool_manager._tools
            if not name.startswith(self.READ_ONLY_PREFIXES)
            and name not in self.READ_ONLY_EXCEPTIONS
        )
        assert offenders == [], (
            f"MCP tools must be read-only, but {offenders} match none of "
            f"{list(self.READ_ONLY_PREFIXES)} and are not listed in "
            "READ_ONLY_EXCEPTIONS. An `aa_` API key is an admin-level READ "
            "credential over the HTTP transport (#1158); a mutating tool "
            "silently widens it into a write credential."
        )

    def test_server_has_all_tools(self):
        from src.mcp.server import mcp

        # The prefix check above cannot see a tool that is GONE, so the count
        # stays on as the de-registration guard — bump it deliberately when the
        # surface changes. It sat at 34 against 37 registered because the only
        # job running this file was the advisory `pytest (unit)` one, where a
        # red test is indistinguishable from the ~750 known isolation failures
        # and nobody is paged (#1235).
        registered = sorted(mcp._tool_manager._tools)
        assert len(registered) == 37, (
            f"MCP tool surface is {len(registered)}, expected 37: {registered}. "
            "This is a REQUIRED check (pipeline-domain) — if you added or "
            "removed a tool on purpose, bump the number here in the same "
            "commit; if you did not, a tool has been de-registered."
        )

    def test_server_has_all_resources(self):
        from src.mcp.server import mcp

        assert len(mcp._resource_manager._resources) == 8

    def test_server_has_all_prompts(self):
        from src.mcp.server import mcp

        assert len(mcp._prompt_manager._prompts) == 5
