# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Request ID Middleware

Generates unique request IDs for distributed tracing and log correlation.
Adds X-Request-ID header to all responses for request tracking.

Usage:
    from src.api.middleware.request_id import init_request_id_middleware

    app = Flask(__name__)
    init_request_id_middleware(app)
"""

import uuid
import time
import logging
from flask import Flask, current_app, request, g
from typing import Optional

from src.modules.shared.log_levels import resolve_log_level

logger = logging.getLogger(__name__)

# Health check endpoints whose request logging level is configurable via the
# logging.health_check_log_level setting (INFO by default, so probes stay visible
# in the request log). Every other path logs at INFO, except that slow or failing
# requests are promoted to WARNING.
HEALTH_CHECK_PATHS = {'/api/health', '/health', '/api/healthz', '/healthz', '/api/ready', '/ready'}

# Default for logging.health_check_log_level; mirrors the schema default in
# src/api/modules/settings/settings_schema.py.
DEFAULT_HEALTH_CHECK_LOG_LEVEL = 'INFO'

# Module-level settings cache to avoid repeated settings_manager lookups per request
# These are refreshed periodically by _refresh_logging_settings()
_logging_settings_cache = {
    'enable_request_logging': True,
    'enable_performance_tracking': True,
    'health_check_log_level': DEFAULT_HEALTH_CHECK_LOG_LEVEL,
    'slow_request_threshold_ms': 1000,
    'last_refresh': 0
}
_LOGGING_SETTINGS_TTL = 60  # Refresh every 60 seconds (longer than per-request check)


def _refresh_logging_settings(app):
    """
    Refresh logging settings from settings manager.
    Called periodically to avoid per-request overhead.
    """
    global _logging_settings_cache
    current_time = time.time()

    # Only refresh if TTL expired
    if (current_time - _logging_settings_cache['last_refresh']) < _LOGGING_SETTINGS_TTL:
        return

    try:
        if hasattr(app, 'settings_manager'):
            _logging_settings_cache['enable_request_logging'] = app.settings_manager.get(
                'logging', 'enable_request_logging', True
            )
            _logging_settings_cache['enable_performance_tracking'] = app.settings_manager.get(
                'logging', 'enable_performance_tracking', True
            )
            _logging_settings_cache['health_check_log_level'] = app.settings_manager.get(
                'logging', 'health_check_log_level', DEFAULT_HEALTH_CHECK_LOG_LEVEL
            )
            _logging_settings_cache['slow_request_threshold_ms'] = app.settings_manager.get(
                'logging', 'slow_request_threshold_ms', 1000
            )
            _logging_settings_cache['last_refresh'] = current_time
    except Exception:
        pass  # Keep using cached values on error


def generate_request_id() -> str:
    """
    Generate a unique request ID using UUID4.

    Returns:
        str: Unique request ID (e.g., "req_550e8400-e29b-41d4-a716-446655440000")
    """
    return f"req_{uuid.uuid4()}"


def get_request_id() -> Optional[str]:
    """
    Get the current request ID from Flask's g object.

    Returns:
        str: Current request ID, or None if not in request context

    Usage:
        from src.api.middleware.request_id import get_request_id

        request_id = get_request_id()
        logger.info(f"Processing request {request_id}")
    """
    return getattr(g, 'request_id', None)


def init_request_id_middleware(app: Flask) -> None:
    """
    Initialize request ID middleware for the Flask application.

    This middleware:
    1. Generates unique request IDs for each request (or accepts existing X-Request-ID)
    2. Stores request ID in Flask's g object for access throughout request lifecycle
    3. Adds X-Request-ID header to all responses
    4. Logs request start/finish with timing information

    Args:
        app: Flask application instance

    Example:
        app = Flask(__name__)
        init_request_id_middleware(app)
    """

    @app.before_request
    def before_request_handler():
        """
        Generate or extract request ID before processing request.

        Accepts existing X-Request-ID from client, or generates new one.
        Stores in g.request_id for access throughout request lifecycle.
        Records request start time for duration calculation.
        Extracts user context from JWT token if available.
        """
        # Check if client provided X-Request-ID header
        request_id = request.headers.get('X-Request-ID')

        if not request_id:
            # Generate new request ID
            request_id = generate_request_id()

        # Store in Flask's g object for access in handlers
        g.request_id = request_id

        # Store request start time for duration calculation
        g.request_start_time = time.time()

        # Extract user context from JWT token (if available)
        # NOTE: We avoid verify_jwt_in_request() here as it triggers database lookup
        # which causes performance overhead. Let the actual @jwt_required() decorator
        # handle full verification. Here we just extract the token identity for logging.
        g.user_id = 'anonymous'
        g.username = 'anonymous'

        try:
            from flask_jwt_extended import decode_token

            # Get Authorization header
            auth_header = request.headers.get('Authorization', '')
            if auth_header.startswith('Bearer '):
                token = auth_header[7:]  # Remove 'Bearer ' prefix
                # Decode token without triggering user_loader_callback (no DB lookup)
                decoded = decode_token(token)
                identity = decoded.get('sub')  # 'sub' is where identity is stored
                if identity:
                    g.user_id = str(identity)
                    g.username = f'user_{identity}'
        except Exception:
            # No JWT token or invalid token - use anonymous
            # Full verification will happen in @jwt_required() decorator
            pass

        # Refresh logging settings cache (only every 60s, not per-request)
        try:
            _refresh_logging_settings(current_app)
        except Exception:
            pass

        # Use cached settings (fast dictionary lookup instead of multiple settings_manager calls)
        enable_request_logging = _logging_settings_cache['enable_request_logging']
        health_check_log_level_str = _logging_settings_cache['health_check_log_level']

        # Only log if request logging is enabled
        if enable_request_logging:
            # Check if this is a health check endpoint (log at configured level to reduce verbosity)
            is_health_check = request.path in HEALTH_CHECK_PATHS

            # Determine log level for health checks. A stored value that is not a
            # usable level name falls back to INFO so probes stay visible; no
            # warning, because this resolves on every request (see resolve_log_level).
            health_check_level = resolve_log_level(health_check_log_level_str, logging.INFO)
            log_level = health_check_level if is_health_check else logging.INFO

            # Log request start (structured logging will add request_id automatically)
            logger.log(
                log_level,
                "Request started",
                extra={
                    'request_id': request_id,
                    'method': request.method,
                    'path': request.path,
                    'remote_addr': request.remote_addr,
                    'user_agent': request.headers.get('User-Agent', 'Unknown'),
                    'user_id': g.user_id,
                    'username': g.username
                }
            )

    @app.after_request
    def after_request_handler(response):
        """
        Add request ID to response headers and log request completion.

        Args:
            response: Flask response object

        Returns:
            Modified response with X-Request-ID header
        """
        request_id = get_request_id()

        if request_id:
            # Add request ID to response headers
            response.headers['X-Request-ID'] = request_id

        # Calculate request duration
        duration_ms = None
        if hasattr(g, 'request_start_time'):
            duration_ms = (time.time() - g.request_start_time) * 1000

        # Get user context
        user_id = getattr(g, 'user_id', 'anonymous')
        username = getattr(g, 'username', 'anonymous')

        # Use cached settings (fast dictionary lookup, no settings_manager calls)
        enable_request_logging = _logging_settings_cache['enable_request_logging']
        enable_performance_tracking = _logging_settings_cache['enable_performance_tracking']
        health_check_log_level_str = _logging_settings_cache['health_check_log_level']
        slow_request_threshold_ms = _logging_settings_cache['slow_request_threshold_ms']

        # Log request completion if enabled
        if enable_request_logging:
            # Check if this is a health check endpoint
            is_health_check = request.path in HEALTH_CHECK_PATHS

            # Check if this is a slow request
            is_slow_request = duration_ms and duration_ms > slow_request_threshold_ms

            # Determine log level based on request type and response
            # Health checks: configured level for fast successful responses, WARNING for slow/failed
            if is_health_check:
                if response.status_code >= 500 or is_slow_request:
                    log_level = logging.WARNING
                else:
                    log_level = resolve_log_level(health_check_log_level_str, logging.INFO)
            elif is_slow_request or response.status_code >= 500:
                # Slow requests or errors logged at WARNING
                log_level = logging.WARNING
            else:
                log_level = logging.INFO

            # Log request completion
            logger.log(
                log_level,
                "Request completed",
                extra={
                    'request_id': request_id,
                    'method': request.method,
                    'path': request.path,
                    'status_code': response.status_code,
                    'duration_ms': round(duration_ms, 2) if duration_ms else None,
                    'response_size': len(response.get_data()) if response.get_data() else 0,
                    'user_id': user_id,
                    'username': username
                }
            )

        # Track performance metrics for latency percentiles and budget warnings
        if enable_performance_tracking and duration_ms is not None:
            try:
                from src.api.utils.performance_tracking import track_request_latency
                track_request_latency(request.path, duration_ms, response.status_code)
            except Exception as e:
                # Don't let performance tracking errors affect the request
                logger.debug(f"Performance tracking error: {e}")

        return response

    @app.teardown_request
    def teardown_request_handler(exception=None):
        """
        Clean up request context after request processing.

        Logs any exceptions that occurred during request processing.

        Args:
            exception: Exception that occurred during request, if any
        """
        if exception:
            request_id = get_request_id()
            logger.error(
                f"Request failed with exception: {exception}",
                extra={
                    'request_id': request_id,
                    'method': request.method,
                    'path': request.path,
                    'exception_type': type(exception).__name__,
                    'exception_message': str(exception)
                },
                exc_info=True
            )

    logger.info("Request ID middleware initialized")


class RequestIDFilter(logging.Filter):
    """
    Logging filter that adds request ID to all log records.

    This ensures request_id appears in all logs during request processing,
    enabling correlation of logs for a single request across the application.

    Usage:
        import logging
        from src.api.middleware.request_id import RequestIDFilter

        handler = logging.StreamHandler()
        handler.addFilter(RequestIDFilter())
        logger.addHandler(handler)
    """

    def filter(self, record):
        """
        Add request_id, user_id, and username attributes to log record.

        Args:
            record: LogRecord instance

        Returns:
            bool: Always True (don't filter out any records)
        """
        # Add request ID to log record if available
        # Use try/except to handle cases where we're outside request context
        try:
            record.request_id = get_request_id() or 'no-request'
        except (RuntimeError, AttributeError):
            # Outside of request context (e.g., during app startup)
            record.request_id = 'no-request'

        # Add user context to log record if available
        # Use try/except to handle cases where we're outside request context
        try:
            from flask import g
            record.user_id = getattr(g, 'user_id', 'system')
            record.username = getattr(g, 'username', 'system')
        except (RuntimeError, AttributeError):
            # Outside of request context (e.g., during app startup)
            record.user_id = 'system'
            record.username = 'system'

        return True
