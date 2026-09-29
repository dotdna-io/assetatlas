# SPDX-License-Identifier: AGPL-3.0-or-later
"""Application context for the AssetAtlas MCP server.

Manages DB pool and Redis connections, initialized via lifespan manager
and made available to tools/resources/prompts through FastMCP's Context.

Two shapes (#1448). stdio: ``app_lifespan`` builds the context, publishes it,
and closes it when the one session ends. Streamable HTTP: the SDK enters
``app_lifespan`` once per session (once per request when stateless), so the
context is instead owned by ``app_context_process_scope``, entered once around
the Starlette app's lifespan; every session, request and API-key check shares
it through ``ensure_app_context()``, which builds it lazily if startup could
not.
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional

import anyio
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

# Serialises the lazy build in `ensure_app_context`. Non-None exactly while an
# HTTP process scope is active — which is also how `app_lifespan` tells the
# HTTP shape from stdio. Created inside the scope, never at import: an
# asyncio-backed lock binds to the first event loop that waits on it, and the
# test suite runs many loops in one process.
_build_lock: Optional[anyio.Lock] = None


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


def _build_app_context() -> AppContext:
    """Open the DB pool, check it, connect Redis and build the services.

    Blocking (psycopg2, Redis ping, service construction): the HTTP shape runs
    it in a worker thread. Raises ``ValueError`` without ``DATABASE_URL`` and
    ``ConnectionError`` on an unhealthy DB, closing the pool it opened first so
    a retried build never strands one.
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is required")

    conn_manager = PostgreSQLConnectionManager(
        database_url=database_url,
        min_connections=1,
        max_connections=3,
    )
    try:
        return _build_on_pool(conn_manager)
    except BaseException:
        try:
            conn_manager.close_all_connections()
        except Exception:
            logger.debug("Closing the pool of a failed build failed",
                         exc_info=True)
        raise


def _build_on_pool(conn_manager: PostgreSQLConnectionManager) -> AppContext:
    """Everything `_build_app_context` builds once the pool exists."""
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

    return AppContext(db=db, conn_manager=conn_manager,
                      field_resolver=field_resolver,
                      redis=redis_client,
                      suggestion_service=suggestion_service,
                      dashboard_service=dashboard_service,
                      pipeline_service=pipeline_service,
                      scheduler_service=scheduler_service,
                      integration_service=integration_service,
                      alerts_service=alerts_service,
                      settings_manager=settings_manager)


def _close_app_context(app_ctx: AppContext) -> None:
    app_ctx.conn_manager.close_all_connections()
    logger.info("Database connections closed")


async def ensure_app_context() -> AppContext:
    """Return the published AppContext, building it first if there is none.

    Only an active process scope may build: the build runs in a worker thread
    under the scope's lock, so concurrent callers wait for one build rather
    than each opening a pool, and a failed build publishes nothing — it
    raises, and the next call retries. Outside a scope (stdio, whose context
    ``app_lifespan`` owns) there is nothing to build with, so no context
    raises ``RuntimeError``.
    """
    global _app_context
    if _app_context is not None:
        return _app_context
    lock = _build_lock
    if lock is None:
        raise RuntimeError("MCP app context is not available")
    async with lock:
        if _app_context is None:
            built = await anyio.to_thread.run_sync(_build_app_context)
            if _build_lock is not lock:
                # The scope shut down while the build ran — nothing would
                # ever close this pool.
                _close_app_context(built)
                raise RuntimeError("MCP server is shutting down")
            _app_context = built
        return _app_context


@asynccontextmanager
async def app_context_process_scope() -> AsyncIterator[None]:
    """Own the AppContext for the life of one HTTP server process.

    Entered once, around the SDK's session-manager lifespan (see
    ``_AssetAtlasFastMCP.streamable_http_app``). Startup attempts the build; a
    failure is logged and the server still starts, because
    ``ensure_app_context`` retries on the next request — an MCP container that
    exits while Postgres is still coming up would otherwise crash-loop.
    Shutdown closes the pool and clears the global.
    """
    global _app_context, _build_lock
    if _build_lock is not None:
        raise RuntimeError("An MCP app-context process scope is already active")
    _build_lock = anyio.Lock()
    try:
        try:
            await ensure_app_context()
        except Exception:
            logger.error(
                "MCP app context could not be built at startup — serving "
                "anyway and retrying on the next request", exc_info=True)
        yield
    finally:
        _build_lock = None
        app_ctx, _app_context = _app_context, None
        if app_ctx is not None:
            _close_app_context(app_ctx)


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """FastMCP lifespan: the AppContext every handler of a session sees.

    Inside an HTTP process scope it only borrows the shared context — it is
    entered per session / per request, and closing the pool here would break
    every other client (#1448). Raises if the context still cannot be built,
    failing that request. Outside a scope (stdio) it owns the context:
    build, publish, and close when the session ends.
    """
    global _app_context
    if _build_lock is not None:
        yield await ensure_app_context()
        return

    app_ctx = _build_app_context()
    _app_context = app_ctx
    try:
        yield app_ctx
    finally:
        _app_context = None
        _close_app_context(app_ctx)
