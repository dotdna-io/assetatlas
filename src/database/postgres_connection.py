# SPDX-License-Identifier: AGPL-3.0-or-later
"""
PostgreSQL Connection Management for AssetAtlas

Provides connection pooling, health checks, and error handling for PostgreSQL database operations.
"""

import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Optional, Dict, Any

import psycopg2
from psycopg2 import pool, sql, extras, extensions
from psycopg2.extensions import connection as pg_connection

# Import query logging cursors
from src.database.query_logger import LoggingCursor, LoggingRealDictCursor

logger = logging.getLogger(__name__)


# Lazily-bound Prometheus hook. Bound on first use (then a direct call) so the
# DB layer doesn't import flask/prometheus at module-import time — keeping the
# connection manager importable in minimal contexts and free of import cycles.
_update_db_pool_metrics = None


def _publish_pool_metrics(pool_obj, max_connections) -> None:
    """Best-effort: publish current pool capacity/availability to Prometheus.

    psycopg2's pool exposes no public stats, so the in-use count is read from
    its documented ``_used`` map defensively; any attribute-shape change just
    skips the update rather than disturbing connection handling. Never raises.
    """
    global _update_db_pool_metrics
    try:
        if pool_obj is None or pool_obj.closed:
            return
        maxconn = getattr(pool_obj, "maxconn", max_connections)
        used = len(getattr(pool_obj, "_used", ()) or ())
        if _update_db_pool_metrics is None:
            from src.api.utils.metrics import update_db_pool_metrics

            _update_db_pool_metrics = update_db_pool_metrics
        _update_db_pool_metrics(maxconn, max(0, maxconn - used))
    except Exception:
        pass


class PostgreSQLConnectionManager:
    """Manages PostgreSQL connections with pooling and error handling."""

    def __init__(
        self,
        database_url: Optional[str] = None,
        min_connections: int = 5,
        max_connections: int = 20,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ):
        """
        Initialize PostgreSQL connection manager.

        Defaults are tuned for a small production deployment (one gunicorn
        worker process + scheduler). Increase max_connections proportionally
        when scaling out gunicorn workers — a 4-worker process pool with
        max_connections=20 each will need ~80 backend connections, which
        Postgres' default max_connections of 100 still accommodates.

        Earlier defaults of 2-5 caused pool exhaustion under modest
        concurrency (BLOCKER-19 / H19 of #596).

        Args:
            database_url: PostgreSQL connection string (default: from DATABASE_URL env var)
            min_connections: Minimum number of connections in pool (default: 5)
            max_connections: Maximum number of connections in pool (default: 20)
            max_retries: Maximum number of connection retry attempts
            retry_delay: Delay between retry attempts in seconds
        """
        self.database_url = database_url or os.getenv("DATABASE_URL")
        if not self.database_url:
            raise ValueError(
                "DATABASE_URL must be provided or set in environment variables"
            )

        self.min_connections = min_connections
        self.max_connections = max_connections
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._pool: Optional[pool.ThreadedConnectionPool] = None
        self._pool_lock = threading.Lock()
        self._scope_state = threading.local()

        logger.info(
            f"PostgreSQL connection manager initialized with pool size {min_connections}-{max_connections}"
        )

    # Cold-start retry budget for the very first pool create. Compose's
    # `depends_on: condition: service_healthy` releases the api/scheduler
    # once `pg_isready` passes inside the db container, but on a fresh
    # install postgres briefly closes its TCP listener between initdb
    # finishing and the main server accepting external clients — TCP gets
    # "Connection refused" for a few seconds even though pg_isready
    # already reported ready. Without retry here, all 4 gunicorn workers
    # exhaust the per-request retry budget at boot and the operator sees
    # a wall of ERRORs before things eventually settle.
    _POOL_CREATE_MAX_ATTEMPTS = 30
    _POOL_CREATE_RETRY_DELAY = 1.0

    # Per-connection session guards, applied via the libpq `options` startup
    # packet (so they override any server/database-level setting). Kept as
    # constants because end_scope restores idle_in_transaction back to this
    # value after the pipeline scope temporarily disables it.
    _STATEMENT_TIMEOUT_MS = 60000  # 60s max single-query execution time
    _IDLE_IN_TRANSACTION_TIMEOUT_MS = 30000  # 30s max idle time in a transaction

    def _create_pool(self) -> pool.ThreadedConnectionPool:
        """Create a new connection pool with timeouts and cleanup settings.

        Retries transient connection-refused errors during postgres cold
        start (see `_POOL_CREATE_MAX_ATTEMPTS` doc above). Hard errors
        (auth failure, missing db) raise immediately — they are not
        recoverable by waiting.
        """
        last_error: Optional[Exception] = None
        for attempt in range(1, self._POOL_CREATE_MAX_ATTEMPTS + 1):
            try:
                if attempt == 1:
                    logger.info("Creating PostgreSQL connection pool...")
                else:
                    logger.info(
                        f"Retrying PostgreSQL pool create "
                        f"(attempt {attempt}/{self._POOL_CREATE_MAX_ATTEMPTS})..."
                    )
                connection_pool = pool.ThreadedConnectionPool(
                    self.min_connections,
                    self.max_connections,
                    self.database_url,
                    # Connection timeouts to prevent hanging connections
                    connect_timeout=10,  # 10 seconds to establish connection
                    # Query timeout to prevent long-running queries from holding connections
                    options=(
                        f"-c statement_timeout={self._STATEMENT_TIMEOUT_MS} "
                        f"-c idle_in_transaction_session_timeout="
                        f"{self._IDLE_IN_TRANSACTION_TIMEOUT_MS}"
                    ),
                )
                logger.info("Connection pool created successfully with timeouts enabled")
                return connection_pool
            except psycopg2.OperationalError as e:
                last_error = e
                if not self._is_transient_connect_error(e):
                    logger.error(f"Failed to create connection pool: {e}")
                    raise
                if attempt < self._POOL_CREATE_MAX_ATTEMPTS:
                    # Log the first few attempts at WARNING so the operator
                    # sees what's happening; downgrade to DEBUG after that
                    # to avoid spamming logs during a long cold start.
                    log = logger.warning if attempt <= 3 else logger.debug
                    log(
                        f"Database not yet accepting connections "
                        f"(attempt {attempt}/{self._POOL_CREATE_MAX_ATTEMPTS}): {e}"
                    )
                    time.sleep(self._POOL_CREATE_RETRY_DELAY)
                    continue
                logger.error(
                    f"Failed to create connection pool after "
                    f"{self._POOL_CREATE_MAX_ATTEMPTS} attempts: {e}"
                )
                raise
            except Exception as e:
                logger.error(f"Failed to create connection pool: {e}")
                raise
        # Defensive: loop always exits via return or raise above.
        raise RuntimeError("unreachable") from last_error

    @staticmethod
    def _is_transient_connect_error(exc: BaseException) -> bool:
        """Whether a psycopg2 OperationalError is a cold-start race we should retry.

        Matches both stock libpq messages ("Connection refused", "could not
        translate host name") and the formatted form psycopg2 emits which
        includes the host:port prefix.
        """
        msg = str(exc).lower()
        markers = (
            "connection refused",
            "could not connect to server",
            "could not translate host name",
            "server closed the connection unexpectedly",
        )
        return any(m in msg for m in markers)

    def get_pool(self) -> pool.ThreadedConnectionPool:
        """Get or create the connection pool."""
        if self._pool is None or self._pool.closed:
            with self._pool_lock:
                if self._pool is None or self._pool.closed:
                    self._pool = self._create_pool()
        return self._pool

    def begin_scope(self) -> None:
        """Begin — or nest into — a connection scope. Re-entrant: nested
        begin/end calls increment/decrement a depth counter, and the single
        cached connection + its transaction are only finalized (committed or
        rolled back) and released by the OUTERMOST end_scope. While any level
        is open, get_connection() reuses one pooled connection for the thread.
        """
        depth = getattr(self._scope_state, 'depth', 0)
        if depth == 0:
            self._scope_state.connection = None
            self._scope_state.errored = False
        self._scope_state.depth = depth + 1

    def end_scope(self, error=None, raise_on_aborted: bool = False) -> None:
        """Close one nesting level. The outermost close commits the scope's work
        on success, or rolls back if an error was passed at ANY level, then
        releases the connection. Inner closes only decrement depth, so a stage
        opening its own scope no longer tears down the surrounding transaction.

        A failure of the outermost commit propagates — the scope's writes are
        the run's, so the caller must see the failure rather than a false
        success; a failed rollback is logged and swallowed (an error is already
        in flight). Safe to call with no active scope (no-op).

        ``raise_on_aborted``: when the success-path transaction is found ABORTED
        (a stage swallowed a DB error, leaving it INERROR without poisoning the
        scope), it is ALWAYS rolled back rather than committed — Postgres would
        turn the COMMIT into a silent ROLLBACK anyway. With this flag True the
        rollback is ALSO escalated to a RuntimeError so the pipeline run is
        marked failed instead of a false success (#813). It is left False for
        request-scope callers (HTTP teardown, chat), where raising inside
        teardown would turn a successful response into a 500; there the aborted
        transaction is rolled back and a warning logged.
        """
        depth = getattr(self._scope_state, 'depth', 0)
        if depth == 0:
            return
        if error:
            self._scope_state.errored = True
        depth -= 1
        self._scope_state.depth = depth
        if depth > 0:
            return  # an inner level closed; keep the outer scope + connection alive

        conn = getattr(self._scope_state, 'connection', None)
        errored = bool(getattr(self._scope_state, 'errored', False))
        # Clear scope state before finalizing so a propagating commit failure
        # can't leave a stale scope for the next caller on this thread.
        self._scope_state.errored = False
        self._scope_state.connection = None
        if conn is None:
            return
        try:
            if errored:
                try:
                    conn.rollback()
                except Exception as e:
                    logger.error("Error rolling back scope: %s", e)
            elif self._is_transaction_aborted(conn):
                # #813: a caught, non-critical stage error can abort the shared
                # transaction WITHOUT poisoning the scope (the exception never
                # propagated through get_connection().__exit__, so `errored` is
                # False). Committing now would be a silent no-op ROLLBACK —
                # Postgres refuses COMMIT on an aborted transaction — discarding
                # the work while reporting success. Always roll back explicitly;
                # only the pipeline write scope (raise_on_aborted) escalates to
                # an exception so the run is marked failed rather than a false
                # success. Request-scope callers just log and move on (a raise in
                # HTTP teardown would 500 an otherwise-successful response).
                try:
                    conn.rollback()
                except Exception as e:
                    logger.error("Error rolling back aborted scope: %s", e)
                if raise_on_aborted:
                    raise RuntimeError(
                        "Refusing to commit an aborted transaction: a pipeline "
                        "stage swallowed a database error and left the shared "
                        "scope in a failed state. Rolled back to avoid silent "
                        "data loss."
                    )
                logger.warning(
                    "Scope transaction was aborted (a swallowed DB error) and "
                    "rolled back instead of committed."
                )
            else:
                conn.commit()
        finally:
            self._restore_scope_session_params(conn)
            self._release_connection(conn)

    @staticmethod
    def _is_transaction_aborted(conn) -> bool:
        """True if the connection's transaction is in the aborted (INERROR)
        state, where Postgres silently downgrades COMMIT to ROLLBACK.

        Guards the success-path commit in ``end_scope``: a stage that caught a
        DB error without poisoning the scope (see the ``errored`` flag) can leave
        the transaction aborted; committing it would discard the whole run
        silently (#813). On any doubt about the status, returns False so a
        legitimate run is never blocked.
        """
        try:
            return (conn.info.transaction_status
                    == extensions.TRANSACTION_STATUS_INERROR)
        except Exception:
            return False

    def is_scope_active(self) -> bool:
        """Return whether the current thread has an active connection scope."""
        return getattr(self._scope_state, 'depth', 0) > 0

    def commit_unless_scoped(self, conn) -> None:
        """Commit the connection only if no scope is active.

        When a scope is active, commit/rollback is deferred to ``end_scope()``.
        """
        if not self.is_scope_active():
            conn.commit()

    def execute_autonomous(self, query: str, params=None) -> None:
        """Run a write on a fresh, autocommitting connection, bypassing any
        active scope.

        For progress/telemetry writes — e.g. the live pipeline ``current_stage``
        — that must be visible to other connections IMMEDIATELY. A normal write
        issued while a scope is active is batched into the scope's transaction
        and only becomes visible at ``end_scope`` (so the polling UI would see a
        frozen stage for the whole run). This pulls a SEPARATE pooled connection
        (never the scope's held connection) in autocommit mode, so it does NOT
        participate in the scope — never use it for data that must be atomic
        with the scope.
        """
        conn = self._get_connection_with_retry()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(query, params or ())
        finally:
            # Reset autocommit before returning to the pool. get_connection
            # resets it on every borrow, so this is defence-in-depth, but it
            # keeps the pooled connection in the expected default state for any
            # future caller that borrows it directly.
            try:
                conn.autocommit = False
            except Exception:
                pass
            self._release_connection(conn)

    @contextmanager
    def get_connection(self, autocommit: bool = False):
        """
        Get a connection from the pool with automatic cleanup.

        When a scope is active (via ``begin_scope``), the connection is lazily
        acquired on first call and reused for the lifetime of the scope.
        Individual callers still create/close their own cursors; only the
        underlying connection is shared.

        Args:
            autocommit: Whether to enable autocommit mode

        Yields:
            psycopg2 connection object

        Example:
            with manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM assets")
        """
        # --- Scope-aware path: reuse a single connection per scope ---
        if getattr(self._scope_state, 'depth', 0) > 0:
            conn = getattr(self._scope_state, 'connection', None)
            if conn is None:
                conn = self._get_connection_with_retry()
                conn.autocommit = autocommit
                self._scope_state.connection = conn
                self._apply_scope_session_params(conn)
            elif conn.autocommit != autocommit:
                # Switching session autocommit mode would force a commit or
                # rollback of the scope's in-flight transaction — both break
                # atomicity. Refuse it; do autocommit-mode work outside a scope.
                raise RuntimeError(
                    "Cannot change autocommit mode on a connection inside an "
                    "active scope (it would break transaction atomicity)"
                )
            try:
                yield conn
            except Exception:
                # A failure inside the scope poisons the shared transaction:
                # mark it errored so the outermost end_scope rolls back instead
                # of committing torn-down work, and roll back now so the
                # connection stays usable.
                self._scope_state.errored = True
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            # NOTE: connection is NOT released here — end_scope() handles that
            return

        # --- Original per-call path (no scope) ---
        conn = None
        try:
            conn = self._get_connection_with_retry()
            conn.autocommit = autocommit
            yield conn
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Error during database operation: {e}")
            raise
        finally:
            if conn:
                self._release_connection(conn)

    def _get_connection_with_retry(self) -> pg_connection:
        """Get a connection from pool with retry logic and wait time tracking."""
        last_error = None
        wait_start_time = time.time()

        for attempt in range(self.max_retries):
            try:
                connection_pool = self.get_pool()
                conn = connection_pool.getconn()
                if conn:
                    wait_duration_ms = (time.time() - wait_start_time) * 1000

                    # Log connection acquisition
                    logger.debug(
                        f"Connection acquired (attempt {attempt + 1})",
                        extra={'connection_wait_ms': round(wait_duration_ms, 2)}
                    )

                    # Warn if connection acquisition took too long (threshold: 1000ms)
                    if wait_duration_ms > 1000:
                        logger.warning(
                            "Slow connection acquisition from pool",
                            extra={
                                'connection_wait_ms': round(wait_duration_ms, 2),
                                'attempts': attempt + 1,
                                'pool_info': self.get_pool_stats()
                            }
                        )

                    _publish_pool_metrics(connection_pool, self.max_connections)
                    return conn
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Connection attempt {attempt + 1}/{self.max_retries} failed: {e}"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)

        error_msg = f"Failed to get connection after {self.max_retries} attempts"
        logger.error(f"{error_msg}: {last_error}")
        raise ConnectionError(error_msg) from last_error

    def _release_connection(self, conn: pg_connection) -> None:
        """Release a connection back to the pool."""
        try:
            connection_pool = self.get_pool()
            if conn.closed:
                # Connection is dead but pool still considers it checked out.
                # Return it with close=True so the pool frees the slot.
                connection_pool.putconn(conn, close=True)
                logger.warning("Released dead connection back to pool")
            else:
                connection_pool.putconn(conn)
                logger.debug("Connection released back to pool")
            _publish_pool_metrics(connection_pool, self.max_connections)
        except Exception as e:
            logger.error(f"Error releasing connection: {e}")

    def _apply_scope_session_params(self, conn: pg_connection) -> None:
        """Disable the idle-in-transaction guard for a scope's connection.

        The asset-generation pipeline runs its entire multi-minute run inside a
        single scope — i.e. ONE open transaction. CPU-bound stages (UnionFind
        id_assignment, scoring, enrichment) can sit well beyond the pool's 30s
        ``idle_in_transaction_session_timeout`` without issuing any SQL; Postgres
        then terminates the connection and the next statement raises
        ``InterfaceError: connection already closed``, failing the run.

        Scope transactions are explicitly managed (begin_scope/end_scope) and
        orphans are recovered by the pipeline watchdog, so the idle guard is
        disabled here and restored on release. ``statement_timeout`` still bounds
        each individual query, so a single runaway statement is still caught.

        Best-effort: if the SET fails the (pre-existing) guard simply stays in
        place rather than crashing the run — logged loudly so it's visible.
        """
        try:
            with conn.cursor() as cur:
                cur.execute("SET idle_in_transaction_session_timeout = 0")
        except Exception as e:
            logger.warning(
                "Could not disable idle-in-transaction timeout on scope "
                "connection (long pipeline stages may be interrupted): %s", e
            )

    def _restore_scope_session_params(self, conn: pg_connection) -> None:
        """Restore the default idle-in-transaction guard before the scope's
        connection returns to the pool, where it again serves ordinary requests
        that DO want the 30s protection.

        Runs after the scope's own commit/rollback, so it uses autocommit to
        avoid leaving an empty open transaction on the pooled connection.
        """
        if getattr(conn, "closed", False):
            return
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    f"SET idle_in_transaction_session_timeout = "
                    f"{self._IDLE_IN_TRANSACTION_TIMEOUT_MS}"
                )
        except Exception as e:
            logger.warning(
                "Could not restore idle-in-transaction timeout on scope "
                "connection: %s", e
            )

    def get_pool_stats(self) -> Dict[str, Any]:
        """
        Get current connection pool statistics.

        Returns configuration-level pool metrics only.  Does NOT query the
        database (avoids recursive ``get_connection()`` calls that can deadlock
        during pool-exhaustion warnings).

        Returns:
            Dictionary with pool metrics:
            - min_connections: Minimum pool size
            - max_connections: Maximum pool size
            - pool_closed: Whether pool is closed
        """
        return {
            'min_connections': self.min_connections,
            'max_connections': self.max_connections,
            'pool_closed': self._pool is None or self._pool.closed if self._pool else True,
        }

    def log_pool_stats(self) -> None:
        """
        Log current connection pool statistics.

        Useful for periodic monitoring or troubleshooting connection issues.
        """
        stats = self.get_pool_stats()

        extra_data = {
            'pool_min_size': stats['min_connections'],
            'pool_max_size': stats['max_connections'],
            'pool_closed': stats['pool_closed'],
        }

        logger.info(
            "Connection pool statistics",
            extra=extra_data
        )

    @contextmanager
    def get_cursor(self, cursor_factory=None, autocommit: bool = False):
        """
        Get a cursor with automatic connection and transaction management.

        Automatically uses logging cursors for query timing and slow query detection.

        Args:
            cursor_factory: Optional cursor factory (e.g., RealDictCursor, extras.RealDictCursor)
                           If RealDictCursor is requested, LoggingRealDictCursor is used instead.
                           If None, LoggingCursor is used (standard cursor with logging).
            autocommit: Whether to enable autocommit mode

        Yields:
            Tuple of (cursor, connection)

        Example:
            with manager.get_cursor() as (cursor, conn):
                cursor.execute("SELECT * FROM assets")
                results = cursor.fetchall()
                conn.commit()
        """
        # Determine which cursor factory to use
        # Replace standard cursors with logging equivalents for query tracking
        if cursor_factory is None:
            # Use logging cursor for query timing and tracking
            actual_factory = LoggingCursor
        elif cursor_factory == extras.RealDictCursor:
            # Use logging dict cursor for dict results with query tracking
            actual_factory = LoggingRealDictCursor
        else:
            # Use provided factory as-is (allows custom cursor factories)
            actual_factory = cursor_factory

        with self.get_connection(autocommit=autocommit) as conn:
            cursor = conn.cursor(cursor_factory=actual_factory)
            try:
                yield cursor, conn
            finally:
                cursor.close()

    def health_check(self) -> Dict[str, Any]:
        """
        Perform a health check on the database connection.

        Returns:
            Dictionary with health check results
        """
        health_status = {
            "healthy": False,
            "error": None,
            "pool_available": False,
            "connection_test": False,
            "database_version": None,
        }

        try:
            # Check if pool is available
            connection_pool = self.get_pool()
            health_status["pool_available"] = not connection_pool.closed

            # Test connection
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT version()")
                    version = cursor.fetchone()[0]
                    health_status["database_version"] = version
                    health_status["connection_test"] = True

            health_status["healthy"] = True
            logger.info("Database health check passed")

        except Exception as e:
            health_status["error"] = str(e)
            logger.error(f"Database health check failed: {e}")

        return health_status

    def close_all_connections(self) -> None:
        """Close all connections in the pool."""
        if self._pool and not self._pool.closed:
            try:
                self._pool.closeall()
                logger.info("All database connections closed")
            except Exception as e:
                logger.error(f"Error closing connections: {e}")
                raise

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close all connections."""
        self.close_all_connections()


# Global connection manager instance (lazy initialization)
_global_connection_manager: Optional[PostgreSQLConnectionManager] = None
_global_manager_lock = threading.Lock()


def get_connection_manager(
    database_url: Optional[str] = None,
    min_connections: int = 2,
    max_connections: int = 5,
    **kwargs,
) -> PostgreSQLConnectionManager:
    """
    Get or create the global connection manager instance.

    Conservative defaults (2-5 connections) to prevent pool exhaustion.
    Production deployments should configure explicit pool sizes via factory.py.

    Args:
        database_url: PostgreSQL connection string
        min_connections: Minimum pool size (default: 2)
        max_connections: Maximum pool size (default: 5)
        **kwargs: Additional arguments for PostgreSQLConnectionManager

    Returns:
        Global PostgreSQLConnectionManager instance
    """
    global _global_connection_manager

    if _global_connection_manager is None:
        with _global_manager_lock:
            if _global_connection_manager is None:
                _global_connection_manager = PostgreSQLConnectionManager(
                    database_url=database_url,
                    min_connections=min_connections,
                    max_connections=max_connections,
                    **kwargs,
                )

    return _global_connection_manager


def close_global_connection_manager() -> None:
    """Close the global connection manager and all its connections."""
    global _global_connection_manager

    if _global_connection_manager is not None:
        _global_connection_manager.close_all_connections()
        _global_connection_manager = None
        logger.info("Global connection manager closed")
