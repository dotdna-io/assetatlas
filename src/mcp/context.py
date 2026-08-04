# SPDX-License-Identifier: AGPL-3.0-or-later
"""Application context for the AssetAtlas MCP server.

Manages DB pool and Redis connections, initialized via lifespan manager
and made available to tools/resources/prompts through FastMCP's Context.
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional

from mcp.server.fastmcp import FastMCP

from src.database.postgres_connection import PostgreSQLConnectionManager
from src.database.postgres_adapter import PostgreSQLAdapter

if TYPE_CHECKING:
    from src.services.embedding import SuggestionService

try:
    from src.services.embedding import SuggestionService as _RuntimeSuggestionService
except ImportError:   # pragma: no cover - optional dep (chromadb, sentence-transformers)
    _RuntimeSuggestionService = None   # type: ignore[assignment, misc]

logger = logging.getLogger(__name__)

# Module-level reference set during lifespan — used by resources that
# cannot receive Context injection (FastMCP treats any parameter as a
# template variable for resources).
_app_context: Optional["AppContext"] = None


def get_app_context() -> Optional["AppContext"]:
    """Get the current AppContext (available after lifespan init)."""
    return _app_context


def get_db(ctx):
    """Return the PostgreSQLAdapter from a FastMCP request context."""
    return ctx.request_context.lifespan_context.db


def get_resolver(ctx):
    """Return the FieldResolver from a FastMCP request context."""
    return ctx.request_context.lifespan_context.field_resolver


def _init_optional_service(name: str,
                           builder: Callable[[], Any]) -> Optional[Any]:
    """Run ``builder()``, logging + returning None on failure.

    MCP tools gracefully handle a None service (each tool surfaces "X not
    available" to the LLM), so any import/construction failure here should be
    non-fatal — the rest of the server should keep running.
    """
    try:
        service = builder()
        logger.info(f"{name.capitalize()} constructed")
        return service
    except Exception as e:
        logger.warning(f"{name.capitalize()} unavailable: {e}")
        return None


def _build_field_resolver(db):
    """Field resolver for the MCP server, which runs as its own process with NO
    Flask app context. Delegates to the shared no-app-context factory so that
    integration_group (``group:<group>:<attr>``) fields resolve — the cached
    provider reads ``current_app`` and silently returns ``{}`` with no app
    context, erasing every group field from the catalog (#792, refs #791).
    Required by AssetRepository: without a resolver every asset tool fails with
    TypeError."""
    from src.modules.shared.field_resolver_factory import build_field_resolver
    return build_field_resolver(db)


@dataclass
class AppContext:
    """Typed application context available to all MCP handlers."""

    db: PostgreSQLAdapter
    conn_manager: PostgreSQLConnectionManager
    field_resolver: Any = None
    redis: Optional[object] = None
    suggestion_service: Optional["SuggestionService"] = None
    dashboard_service: Optional[Any] = None
    pipeline_service: Optional[Any] = None
    scheduler_service: Optional[Any] = None
    integration_service: Optional[Any] = None
    alerts_service: Optional[Any] = None
    settings_manager: Optional[Any] = None


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Initialize DB pool on startup, clean up on shutdown."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is required")

    conn_manager = PostgreSQLConnectionManager(
        database_url=database_url,
        min_connections=1,
        max_connections=3,
    )
    db = PostgreSQLAdapter(connection_manager=conn_manager)

    # Optional Redis connection
    redis_client = None
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            import redis
            redis_client = redis.from_url(redis_url, decode_responses=True)
            redis_client.ping()
            logger.info("Redis connected")
        except Exception as e:
            logger.warning(f"Redis unavailable, continuing without cache: {e}")
            redis_client = None

    # Verify DB connectivity
    health = conn_manager.health_check()
    if not health["healthy"]:
        raise ConnectionError(f"Database health check failed: {health.get('error')}")
    logger.info(f"Database connected: {health.get('database_version', 'unknown')}")

    # Optional suggestion service (requires chromadb + sentence-transformers)
    suggestion_service = None
    if _RuntimeSuggestionService is not None:
        try:
            suggestion_service = _RuntimeSuggestionService()
            logger.info("Suggestion service constructed (lazy-initialized on first call)")
        except Exception as e:
            logger.warning(f"Suggestion service unavailable: {e}")
            suggestion_service = None

    # All optional services follow the same "build or warn + None" pattern.
    # Each one is used by exactly one MCP tool, which gracefully handles None.
    def _build_dashboard_service():
        from src.modules.dashboard.services.dashboard_service import DashboardService
        from src.modules.dashboard.repositories.dashboard_repository import (
            DashboardRepository,
        )
        return DashboardService(DashboardRepository(db), settings_manager=settings_manager)

    def _build_pipeline_service():
        # MCP only exercises read paths, but the constructor still requires a
        # trigger_service — build the minimal chain to satisfy it.
        from src.modules.pipeline.pipeline_service import PipelineService
        from src.modules.integrations.services.asset_generation_trigger_service import (
            AssetGenerationTriggerService,
        )
        trigger_service = AssetGenerationTriggerService(db_adapter=db)
        return PipelineService(
            connection_manager=conn_manager,
            trigger_service=trigger_service,
        )

    def _build_scheduler_service():
        # MCP only reads health metrics; do NOT call initialize()/start().
        from src.modules.integrations.services import SchedulerService
        return SchedulerService()

    def _build_integration_service():
        from src.modules.integrations.services.integration_service import (
            IntegrationService,
        )
        return IntegrationService()

    def _build_alerts_service():
        from src.modules.alerts.service import AlertsService
        from src.modules.alerts.repository import AlertsRepository
        return AlertsService(repository=AlertsRepository(db))

    def _build_settings_manager():
        from src.api.modules.settings.settings_manager import SettingsManager
        return SettingsManager(conn_manager)

    settings_manager = _init_optional_service(
        "settings manager", _build_settings_manager)
    dashboard_service = _init_optional_service(
        "dashboard service", _build_dashboard_service)
    pipeline_service = _init_optional_service(
        "pipeline service", _build_pipeline_service)
    scheduler_service = _init_optional_service(
        "scheduler service", _build_scheduler_service)
    integration_service = _init_optional_service(
        "integration service", _build_integration_service)
    alerts_service = _init_optional_service(
        "alerts service", _build_alerts_service)
    field_resolver = _init_optional_service(
        "field resolver", lambda: _build_field_resolver(db))

    global _app_context
    app_ctx = AppContext(db=db, conn_manager=conn_manager,
                         field_resolver=field_resolver,
                         redis=redis_client,
                         suggestion_service=suggestion_service,
                         dashboard_service=dashboard_service,
                         pipeline_service=pipeline_service,
                         scheduler_service=scheduler_service,
                         integration_service=integration_service,
                         alerts_service=alerts_service,
                         settings_manager=settings_manager)
    _app_context = app_ctx
    try:
        yield app_ctx
    finally:
        _app_context = None
        conn_manager.close_all_connections()
        logger.info("Database connections closed")
