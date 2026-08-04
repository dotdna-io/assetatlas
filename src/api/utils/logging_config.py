# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Structured Logging Configuration

Provides JSON logging formatter for structured logs with request ID correlation.
Enables easy parsing by log aggregation systems (Elasticsearch, Splunk, etc.).

Usage:
    from src.api.utils.logging_config import configure_structured_logging

    app = Flask(__name__)
    configure_structured_logging(app)
"""

import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from flask import Flask, has_request_context, request, g
from pythonjsonlogger import jsonlogger

from src.modules.shared.log_levels import resolve_log_level


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """
    Custom JSON formatter that adds AssetAtlas-specific fields to log records.

    Adds:
    - timestamp (ISO 8601 format)
    - request_id (if available)
    - request_method, request_path (if in request context)
    - application name and version
    - environment
    """

    def __init__(self, *args, **kwargs):
        """Initialize formatter with timestamp format"""
        super().__init__(*args, **kwargs)
        self.app_name = "AssetAtlas Backend API"
        self.app_version = "1.0.0"

    def add_fields(self, log_record: Dict[str, Any], record: logging.LogRecord, message_dict: Dict[str, Any]) -> None:
        """
        Add custom fields to JSON log record.

        Args:
            log_record: Dictionary that will be serialized to JSON
            record: Original LogRecord object
            message_dict: Dictionary from log message
        """
        super().add_fields(log_record, record, message_dict)

        # Add timestamp in ISO 8601 format
        log_record['timestamp'] = datetime.utcnow().isoformat() + 'Z'

        # Add log level
        log_record['level'] = record.levelname

        # Add logger name
        log_record['logger'] = record.name

        # Add application metadata
        log_record['application'] = self.app_name
        log_record['version'] = self.app_version

        # Add request context if available
        if has_request_context():
            log_record['request_method'] = request.method
            log_record['request_path'] = request.path

            # Add request ID if available (added by RequestIDFilter)
            if hasattr(record, 'request_id'):
                log_record['request_id'] = record.request_id

            # Add user context if available (added by RequestIDFilter)
            if hasattr(record, 'user_id'):
                log_record['user_id'] = record.user_id
            if hasattr(record, 'username'):
                log_record['username'] = record.username

        # Add source location
        log_record['source'] = {
            'file': record.filename,
            'line': record.lineno,
            'function': record.funcName
        }

        # Add exception info if present
        if record.exc_info:
            log_record['exception'] = {
                'type': record.exc_info[0].__name__ if record.exc_info[0] else None,
                'message': str(record.exc_info[1]) if record.exc_info[1] else None
            }


class SensitiveDataFilter(logging.Filter):
    """
    Logging filter that sanitizes sensitive data from log messages.

    Prevents passwords, tokens, and other secrets from appearing in logs.

    Usage:
        handler.addFilter(SensitiveDataFilter())
    """

    SENSITIVE_KEYS = {
        'password', 'pwd', 'passwd', 'secret', 'token', 'api_key',
        'apikey', 'auth', 'authorization', 'credential', 'private_key',
        'client_secret', 'access_token', 'refresh_token'
    }

    def filter(self, record):
        """
        Sanitize sensitive data from log record.

        Args:
            record: LogRecord instance

        Returns:
            bool: Always True (don't filter out records)
        """
        # Sanitize message
        record.msg = self._sanitize_string(str(record.msg))

        # Sanitize args if present (with depth limit to prevent infinite recursion)
        if record.args:
            try:
                record.args = tuple(
                    self._sanitize_value(arg, depth=0) for arg in record.args
                )
            except RecursionError:
                # If we hit recursion limit, just convert to string
                record.args = ('***RECURSION_ERROR***',)

        return True

    def _sanitize_string(self, value: str) -> str:
        """
        Sanitize a string value by masking sensitive keywords.

        Args:
            value: String to sanitize

        Returns:
            Sanitized string
        """
        lower_value = value.lower()
        for sensitive_key in self.SENSITIVE_KEYS:
            if sensitive_key in lower_value:
                # Mask the value after the sensitive key
                # Example: "password=abc123" becomes "password=***REDACTED***"
                value = value.replace(
                    f"{sensitive_key}=",
                    f"{sensitive_key}=***REDACTED***"
                )
                value = value.replace(
                    f'"{sensitive_key}":',
                    f'"{sensitive_key}":"***REDACTED***"'
                )
        return value

    def _sanitize_value(self, value: Any, depth: int = 0, max_depth: int = 10) -> Any:
        """
        Sanitize any value (recursively handles dicts and lists with depth limit).

        Args:
            value: Value to sanitize
            depth: Current recursion depth
            max_depth: Maximum recursion depth to prevent infinite loops

        Returns:
            Sanitized value
        """
        # Prevent infinite recursion on circular references or deep nesting
        if depth >= max_depth:
            return '***MAX_DEPTH_EXCEEDED***'

        if isinstance(value, str):
            return self._sanitize_string(value)
        elif isinstance(value, dict):
            try:
                return {
                    k: '***REDACTED***' if k.lower() in self.SENSITIVE_KEYS else self._sanitize_value(v, depth + 1, max_depth)
                    for k, v in value.items()
                }
            except:
                return '***SANITIZATION_ERROR***'
        elif isinstance(value, (list, tuple)):
            try:
                return type(value)(self._sanitize_value(item, depth + 1, max_depth) for item in value)
            except:
                return '***SANITIZATION_ERROR***'
        return value


# ============================================================================
# Custom Log Filters for Separate File Handlers (Phase 4.5)
# ============================================================================

class AccessLogFilter(logging.Filter):
    """
    Filter that passes only HTTP request completion logs.

    These are logs that contain request completion metadata like:
    - request_path, request_method, status_code, duration_ms
    """

    def filter(self, record):
        """Pass only request completion logs."""
        # Check if this is a request completion log (has specific markers)
        return (
            hasattr(record, 'event_type') and record.event_type == 'request_completed'
        ) or (
            'Request completed' in str(record.msg) or
            'response_status' in str(record.msg)
        )


class ErrorOnlyFilter(logging.Filter):
    """
    Filter that passes only ERROR and CRITICAL level logs.

    Used for error.log file that contains only errors for easy monitoring.
    """

    def filter(self, record):
        """Pass only ERROR and CRITICAL logs."""
        return record.levelno >= logging.ERROR


class SecurityEventFilter(logging.Filter):
    """
    Filter that passes only security-related events.

    Security events include:
    - Authentication failures/successes
    - Authorization denials
    - Suspicious activity
    - Brute force attempts
    """

    SECURITY_KEYWORDS = {
        'authentication', 'authorization', 'login', 'logout', 'access_denied',
        'permission_denied', 'forbidden', 'unauthorized', 'brute_force',
        'suspicious', 'security', 'audit', 'rbac'
    }

    def filter(self, record):
        """Pass only security-related logs."""
        # Check for security event type
        if hasattr(record, 'event_type'):
            event_type = record.event_type.lower()
            if any(kw in event_type for kw in self.SECURITY_KEYWORDS):
                return True

        # Check logger name
        logger_name = record.name.lower()
        if 'auth' in logger_name or 'rbac' in logger_name or 'security' in logger_name:
            return True

        # Check message content
        msg_lower = str(record.msg).lower()
        if any(kw in msg_lower for kw in self.SECURITY_KEYWORDS):
            return True

        return False


class PerformanceWarningFilter(logging.Filter):
    """
    Filter that passes only performance-related warnings.

    Performance warnings include:
    - Slow queries
    - Budget violations
    - Performance degradation
    - High latency
    """

    PERFORMANCE_KEYWORDS = {
        'slow_query', 'budget_exceeded', 'performance', 'latency',
        'timeout', 'pool_exhaustion', 'connection_timeout'
    }

    def filter(self, record):
        """Pass only performance warning logs."""
        # Check for performance event type
        if hasattr(record, 'event_type'):
            event_type = record.event_type.lower()
            if any(kw in event_type for kw in self.PERFORMANCE_KEYWORDS):
                return True

        # Check logger name
        logger_name = record.name.lower()
        if 'performance' in logger_name or 'query' in logger_name:
            if record.levelno >= logging.WARNING:
                return True

        # Check message content for performance issues
        msg_lower = str(record.msg).lower()
        if any(kw in msg_lower for kw in self.PERFORMANCE_KEYWORDS):
            return True

        # Check for slow query warnings
        if 'slow' in msg_lower and ('query' in msg_lower or 'request' in msg_lower):
            return True

        return False


# ============================================================================
# Log Directory and File Handler Setup (Phase 4.5)
# ============================================================================

def setup_log_directories(base_dir: str = 'logs') -> Dict[str, Path]:
    """
    Create log directory structure if it doesn't exist.

    Directory structure:
    logs/
    ├── api/          # API logs (access, error, app)
    ├── security/     # Security alerts
    └── performance/  # Performance warnings

    Args:
        base_dir: Base directory for logs (default: 'logs')

    Returns:
        Dictionary of directory paths
    """
    base_path = Path(base_dir)

    directories = {
        'base': base_path,
        'api': base_path / 'api',
        'security': base_path / 'security',
        'performance': base_path / 'performance'
    }

    for name, path in directories.items():
        try:
            path.mkdir(parents=True, exist_ok=True)
            # Set permissions (owner read/write, group read)
            if os.name != 'nt':  # Not Windows
                os.chmod(path, 0o750)
        except PermissionError as e:
            # Log to stderr since logging may not be set up yet
            print(f"WARNING: Could not create log directory {path}: {e}", file=sys.stderr)
        except Exception as e:
            print(f"WARNING: Error creating log directory {path}: {e}", file=sys.stderr)

    return directories


def create_rotating_file_handler(
    filepath: Path,
    max_bytes: int = 50 * 1024 * 1024,  # 50MB default
    backup_count: int = 30,
    formatter: logging.Formatter = None,
    level: int = logging.INFO
) -> logging.handlers.RotatingFileHandler:
    """
    Create a rotating file handler with specified parameters.

    Args:
        filepath: Path to log file
        max_bytes: Maximum file size before rotation (default: 50MB)
        backup_count: Number of backup files to keep (default: 30)
        formatter: Formatter to use (default: JSON formatter)
        level: Log level for this handler

    Returns:
        Configured RotatingFileHandler
    """
    handler = logging.handlers.RotatingFileHandler(
        filepath,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    handler.setLevel(level)

    if formatter:
        handler.setFormatter(formatter)

    return handler


def setup_file_handlers(
    app: Flask,
    log_dirs: Dict[str, Path],
    json_formatter: logging.Formatter,
    text_formatter: logging.Formatter,
    use_json: bool = True
) -> Dict[str, logging.Handler]:
    """
    Set up all file handlers for different log types.

    Creates handlers for:
    - access.log: HTTP request/response logs
    - error.log: ERROR and CRITICAL only
    - app.log: General application logs (INFO+)
    - alerts.log: Security events
    - slow.log: Performance warnings

    Args:
        app: Flask application instance
        log_dirs: Dictionary of log directory paths
        json_formatter: JSON formatter for structured logs
        text_formatter: Text formatter for human-readable logs
        use_json: Whether to use JSON formatting (default: True)

    Returns:
        Dictionary of configured handlers
    """
    # Import filter here to avoid circular dependency
    from src.api.middleware.request_id import RequestIDFilter

    handlers = {}
    formatter = json_formatter if use_json else text_formatter

    # Get environment configuration
    log_to_files = os.getenv('LOG_TO_FILES', 'true').lower() == 'true'

    if not log_to_files:
        return handlers

    # 1. Access Log Handler - HTTP requests
    if os.getenv('LOG_ACCESS_ENABLED', 'true').lower() == 'true':
        try:
            access_handler = create_rotating_file_handler(
                log_dirs['api'] / 'access.log',
                max_bytes=50 * 1024 * 1024,  # 50MB
                backup_count=30,  # ~90 days
                formatter=formatter,
                level=logging.INFO
            )
            access_handler.addFilter(RequestIDFilter())
            access_handler.addFilter(SensitiveDataFilter())
            access_handler.addFilter(AccessLogFilter())
            handlers['access'] = access_handler
        except Exception as e:
            print(f"WARNING: Could not create access log handler: {e}", file=sys.stderr)

    # 2. Error Log Handler - ERROR and CRITICAL only
    if os.getenv('LOG_ERROR_ENABLED', 'true').lower() == 'true':
        try:
            error_handler = create_rotating_file_handler(
                log_dirs['api'] / 'error.log',
                max_bytes=10 * 1024 * 1024,  # 10MB
                backup_count=90,  # 90 days
                formatter=formatter,
                level=logging.ERROR
            )
            error_handler.addFilter(RequestIDFilter())
            error_handler.addFilter(SensitiveDataFilter())
            error_handler.addFilter(ErrorOnlyFilter())
            handlers['error'] = error_handler
        except Exception as e:
            print(f"WARNING: Could not create error log handler: {e}", file=sys.stderr)

    # 3. Application Log Handler - All INFO+ logs
    if os.getenv('LOG_APP_ENABLED', 'true').lower() == 'true':
        try:
            app_handler = create_rotating_file_handler(
                log_dirs['api'] / 'app.log',
                max_bytes=50 * 1024 * 1024,  # 50MB
                backup_count=30,  # 30 backups
                formatter=formatter,
                level=logging.INFO
            )
            app_handler.addFilter(RequestIDFilter())
            app_handler.addFilter(SensitiveDataFilter())
            handlers['app'] = app_handler
        except Exception as e:
            print(f"WARNING: Could not create app log handler: {e}", file=sys.stderr)

    # 4. Security Alerts Log Handler
    if os.getenv('LOG_SECURITY_ENABLED', 'true').lower() == 'true':
        try:
            security_handler = create_rotating_file_handler(
                log_dirs['security'] / 'alerts.log',
                max_bytes=10 * 1024 * 1024,  # 10MB
                backup_count=365,  # 1 year for compliance
                formatter=formatter,
                level=logging.INFO
            )
            security_handler.addFilter(RequestIDFilter())
            security_handler.addFilter(SensitiveDataFilter())
            security_handler.addFilter(SecurityEventFilter())
            handlers['security'] = security_handler
        except Exception as e:
            print(f"WARNING: Could not create security log handler: {e}", file=sys.stderr)

    # 5. Performance/Slow Query Log Handler
    if os.getenv('LOG_PERFORMANCE_ENABLED', 'true').lower() == 'true':
        try:
            perf_handler = create_rotating_file_handler(
                log_dirs['performance'] / 'slow.log',
                max_bytes=10 * 1024 * 1024,  # 10MB
                backup_count=30,  # 30 backups
                formatter=formatter,
                level=logging.WARNING
            )
            perf_handler.addFilter(RequestIDFilter())
            perf_handler.addFilter(SensitiveDataFilter())
            perf_handler.addFilter(PerformanceWarningFilter())
            handlers['performance'] = perf_handler
        except Exception as e:
            print(f"WARNING: Could not create performance log handler: {e}", file=sys.stderr)

    return handlers


def configure_structured_logging(app: Flask) -> None:
    """
    Configure structured JSON logging for the Flask application.

    Sets up:
    - JSON formatter for machine-readable logs
    - Request ID filter for log correlation
    - Sensitive data filter for security
    - Console and file handlers (access.log, error.log, app.log, alerts.log, slow.log)
    - Log rotation (if in production)

    Args:
        app: Flask application instance

    Example:
        app = Flask(__name__)
        configure_structured_logging(app)
    """
    # Import here to avoid circular dependency
    from src.api.middleware.request_id import RequestIDFilter

    # Determine if we should use JSON logging
    # Use JSON in production, human-readable in development
    use_json_logging = app.config.get('FLASK_ENV') == 'production'

    # Get log level from config. Operator-facing: compose passes
    # LOG_LEVEL: ${LOG_LEVEL:-WARNING}, so a typo in .env reaches this line and
    # the warning is the only sign of it — resolve once at startup, warn=True.
    log_level_str = app.config.get('LOG_LEVEL', 'INFO')
    log_level = resolve_log_level(log_level_str, logging.INFO, warn=True)

    # Create root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers
    root_logger.handlers = []

    # Create formatters
    json_formatter = CustomJsonFormatter(
        '%(timestamp)s %(level)s %(logger)s %(message)s'
    )
    text_formatter = logging.Formatter(
        fmt='%(asctime)s [%(request_id)s] [%(username)s] %(levelname)-8s %(name)-30s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Create console handler (always enabled for development)
    console_enabled = os.getenv('LOG_CONSOLE_ENABLED', 'true').lower() == 'true'
    if console_enabled:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)

        if use_json_logging:
            console_handler.setFormatter(json_formatter)
        else:
            console_handler.setFormatter(text_formatter)

        # Add filters
        console_handler.addFilter(RequestIDFilter())
        console_handler.addFilter(SensitiveDataFilter())

        # Add console handler to root logger
        root_logger.addHandler(console_handler)

    # Set up file handlers (Phase 4.5)
    # Only set up if LOG_TO_FILES is enabled (default: true)
    log_to_files = os.getenv('LOG_TO_FILES', 'true').lower() == 'true'

    if log_to_files:
        # Get base log directory from environment
        log_base_dir = os.getenv('LOG_BASE_DIR', 'logs')

        # Create directory structure
        log_dirs = setup_log_directories(log_base_dir)

        # Set up file handlers
        file_handlers = setup_file_handlers(
            app=app,
            log_dirs=log_dirs,
            json_formatter=json_formatter,
            text_formatter=text_formatter,
            use_json=use_json_logging
        )

        # Add all file handlers to root logger
        for handler_name, handler in file_handlers.items():
            root_logger.addHandler(handler)

        # Store handler info in app config for health checks
        app.config['LOG_FILE_HANDLERS'] = file_handlers
        app.config['LOG_DIRECTORIES'] = log_dirs
    else:
        app.config['LOG_FILE_HANDLERS'] = {}
        app.config['LOG_DIRECTORIES'] = {}

    # Configure Flask app logger
    # Don't add handler to app.logger - it will use root logger's handler via propagation
    # This prevents duplicate log messages
    app.logger.setLevel(log_level)
    app.logger.handlers = []
    app.logger.propagate = True  # Ensure propagation is enabled (default)

    # Configure werkzeug logger (Flask's HTTP server)
    # Don't add handler - it will use root logger's handler via propagation
    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.setLevel(logging.WARNING)  # Less verbose
    werkzeug_logger.handlers = []
    werkzeug_logger.propagate = True  # Ensure propagation is enabled

    # Silence chatty third-party clients that emit malformed debug frames.
    # The anthropic SDK's "Request options: %s" debug log has 7 positional args
    # vs 1 placeholder and raises TypeError on every format attempt (harmless
    # to the request itself, noisy in the log). httpcore/httpx at DEBUG is
    # also too verbose for a chat with streaming responses.
    for noisy in ('anthropic._base_client', 'anthropic', 'httpcore', 'httpx'):
        logging.getLogger(noisy).setLevel(logging.INFO)

    # Log configuration (only in child process to avoid duplicates in debug mode)
    if os.getenv('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        handler_names = list(app.config.get('LOG_FILE_HANDLERS', {}).keys())
        app.logger.info(
            f"Structured logging configured: json={use_json_logging}, level={log_level_str}, "
            f"file_handlers={handler_names}, console={console_enabled}"
        )


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with proper configuration.

    This is a convenience function for getting loggers in other modules.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance

    Example:
        from src.api.utils.logging_config import get_logger

        logger = get_logger(__name__)
        logger.info("Something happened")
    """
    return logging.getLogger(name)
