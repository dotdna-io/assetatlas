# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Database Query Logging Utilities

Provides query timing, slow query detection, parameter sanitization, and query fingerprinting
for database operations monitoring and performance analysis.

Usage:
    from src.database.query_logger import LoggingCursor

    with conn.cursor(cursor_factory=LoggingCursor) as cursor:
        cursor.execute("SELECT * FROM assets WHERE id = %s", (asset_id,))
"""

import logging
import time
import re
import os
from typing import Any, Optional, Tuple

import psycopg2
import psycopg2.extensions
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

# Import sensitive data filter at module level to avoid circular imports
try:
    from src.api.utils.logging_config import SensitiveDataFilter
    _sensitive_data_filter = SensitiveDataFilter()
except ImportError:
    # If import fails (e.g., during testing), create a simple passthrough
    class SimpleSanitizer:
        def _sanitize_value(self, value):
            return value
    _sensitive_data_filter = SimpleSanitizer()


class QueryLogger:
    """
    Centralized query logging with timing, slow query detection, and fingerprinting.

    Uses environment variables for configuration (not SettingsManager) to avoid
    circular dependencies with the database connection pool.
    """

    # Configurable slow query threshold (milliseconds)
    SLOW_QUERY_THRESHOLD_MS = int(os.getenv('DB_SLOW_QUERY_THRESHOLD_MS', '100'))

    # Enable/disable query logging
    ENABLE_QUERY_LOGGING = os.getenv('DB_ENABLE_QUERY_LOGGING', 'true').lower() in ('true', '1', 'yes')

    # Log all queries (not just slow ones) - useful for debugging
    LOG_ALL_QUERIES = os.getenv('DB_LOG_ALL_QUERIES', 'false').lower() in ('true', '1', 'yes')

    @staticmethod
    def sanitize_params(params: Any) -> Any:
        """
        Sanitize query parameters to prevent sensitive data in logs.

        Args:
            params: Query parameters (tuple, list, dict, or single value)

        Returns:
            Sanitized parameters with sensitive data masked
        """
        if params is None:
            return None

        # Use module-level sanitizer to avoid circular imports
        return _sensitive_data_filter._sanitize_value(params)

    @staticmethod
    def fingerprint_query(query: str) -> str:
        """
        Generate a query fingerprint by normalizing the SQL query.

        Replaces literals with placeholders to group similar queries together.
        For example:
        - "SELECT * FROM assets WHERE id = 123"
        - "SELECT * FROM assets WHERE id = 456"
        Both become: "SELECT * FROM assets WHERE id = ?"

        Args:
            query: SQL query string

        Returns:
            Normalized query fingerprint
        """
        # Handle bytes queries (e.g. from execute_values)
        if isinstance(query, bytes):
            query = query.decode('utf-8', errors='replace')
        # Remove extra whitespace
        normalized = ' '.join(query.split())

        # Replace numeric literals with ?
        normalized = re.sub(r'\b\d+\b', '?', normalized)

        # Replace string literals with ?
        normalized = re.sub(r"'[^']*'", '?', normalized)

        # Replace UUID literals with ?
        normalized = re.sub(
            r"'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'::uuid",
            '?::uuid',
            normalized,
            flags=re.IGNORECASE
        )

        # Replace %s placeholders (psycopg2 style) with ? for consistency
        normalized = re.sub(r'%s', '?', normalized)

        # Replace UUID cast syntax
        normalized = re.sub(r'\?::uuid', '?', normalized)

        return normalized

    @staticmethod
    def log_query(
        query: str,
        params: Optional[Any],
        duration_ms: float,
        rows_affected: Optional[int] = None,
        error: Optional[Exception] = None
    ) -> None:
        """
        Log a database query with timing and metadata.

        Args:
            query: SQL query string
            params: Query parameters
            duration_ms: Query execution time in milliseconds
            rows_affected: Number of rows affected/returned (if available)
            error: Exception if query failed
        """
        if not QueryLogger.ENABLE_QUERY_LOGGING:
            return

        # Determine if this is a slow query
        is_slow = duration_ms >= QueryLogger.SLOW_QUERY_THRESHOLD_MS

        # Only log if it's slow OR if we're logging all queries
        if not is_slow and not QueryLogger.LOG_ALL_QUERIES:
            return

        # Generate query fingerprint for grouping
        fingerprint = QueryLogger.fingerprint_query(query)

        # Sanitize parameters
        sanitized_params = QueryLogger.sanitize_params(params)

        # Determine log level
        if error:
            log_level = logger.error
            status = 'FAILED'
        elif is_slow:
            log_level = logger.warning
            status = 'SLOW'
        else:
            log_level = logger.debug
            status = 'OK'

        # Build log message
        msg = f"Database query {status}"

        # Build extra context
        extra = {
            'query_status': status,
            'query_duration_ms': round(duration_ms, 2),
            'query_fingerprint': fingerprint,
            'query_raw': query[:500],  # Truncate long queries
            'is_slow_query': is_slow,
        }

        # Add parameters if available (and not too large)
        if sanitized_params is not None:
            params_str = str(sanitized_params)
            if len(params_str) <= 200:
                extra['query_params'] = sanitized_params
            else:
                extra['query_params'] = f"{params_str[:200]}... [TRUNCATED]"

        # Add rows affected if available
        if rows_affected is not None:
            extra['rows_affected'] = rows_affected

        # Add error details if failed
        if error:
            extra['error_type'] = type(error).__name__
            extra['error_message'] = str(error)

        # Log the query
        log_level(msg, extra=extra)


class LoggingCursorMixin:
    """
    Mixin to add query logging to psycopg2 cursors.

    Wraps execute() and executemany() to log queries with timing.
    """

    def execute(self, query, vars=None):
        """Execute query with timing and logging."""
        start_time = time.time()
        error = None

        try:
            # Call parent execute
            result = super().execute(query, vars)
            return result

        except Exception as e:
            error = e
            raise

        finally:
            # Calculate duration
            duration_ms = (time.time() - start_time) * 1000

            # Get rows affected (if available)
            rows_affected = None
            try:
                rows_affected = self.rowcount if self.rowcount >= 0 else None
            except:
                pass

            # Log the query
            QueryLogger.log_query(
                query=query,
                params=vars,
                duration_ms=duration_ms,
                rows_affected=rows_affected,
                error=error
            )

    def executemany(self, query, vars_list):
        """Execute many queries with timing and logging."""
        start_time = time.time()
        error = None
        batch_size = len(vars_list) if vars_list else 0

        try:
            # Call parent executemany
            result = super().executemany(query, vars_list)
            return result

        except Exception as e:
            error = e
            raise

        finally:
            # Calculate duration
            duration_ms = (time.time() - start_time) * 1000

            # Get rows affected (if available)
            rows_affected = None
            try:
                rows_affected = self.rowcount if self.rowcount >= 0 else None
            except:
                pass

            # Log the batch query (show first params as sample)
            sample_params = vars_list[0] if vars_list else None

            QueryLogger.log_query(
                query=query,
                params={'batch_size': batch_size, 'sample': sample_params},
                duration_ms=duration_ms,
                rows_affected=rows_affected,
                error=error
            )


class LoggingCursor(LoggingCursorMixin, psycopg2.extensions.cursor):
    """Standard cursor with query logging."""
    pass


class LoggingRealDictCursor(LoggingCursorMixin, RealDictCursor):
    """RealDictCursor with query logging."""
    pass
