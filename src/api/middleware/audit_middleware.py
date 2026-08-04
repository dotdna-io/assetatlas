# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Audit Trail Middleware

Captures all mutating API calls to the audit_trail database table.
Uses Flask's after_request hook for fire-and-forget audit logging.

Usage:
    from src.api.middleware.audit_middleware import init_audit_middleware

    app = Flask(__name__)
    init_audit_middleware(app)
"""

import json
import time
import logging
from flask import Flask, request, g, current_app

from src.api.utils.caching import cache
from src.api.utils.audit_context import get_audit_annotation

logger = logging.getLogger(__name__)

# Paths to skip (anything starting with these prefixes)
_SKIP_PATH_PREFIXES = (
    '/api/health',
    '/api/healthz',
    '/api/ready',
    '/health',
    '/healthz',
    '/ready',
    '/api/docs',
    '/api/apispec.json',
    '/flasgger_static',
)

# HTTP methods to always skip
_SKIP_METHODS = {'HEAD', 'OPTIONS'}

# Request body fields to sanitize (matched case-insensitively)
_SENSITIVE_FIELDS = {
    'password', 'token', 'secret', 'api_key', 'apikey',
    'authorization', 'bearer_token'
}

# Max request body size to read (64 KB)
_MAX_BODY_SIZE = 64 * 1024

# Module-level cache for AUDIT_TRAIL_LOG_READS setting
_audit_settings_cache = {
    'log_reads': False,
    'last_refresh': 0,
}
_AUDIT_SETTINGS_TTL = 60  # Refresh every 60 seconds


def _refresh_audit_settings(app):
    """
    Refresh audit settings from settings manager.
    Called periodically to avoid per-request overhead.
    """
    global _audit_settings_cache
    current_time = time.time()

    if (current_time - _audit_settings_cache['last_refresh']) < _AUDIT_SETTINGS_TTL:
        return

    try:
        if hasattr(app, 'settings_manager'):
            _audit_settings_cache['log_reads'] = app.settings_manager.get(
                'retention', 'AUDIT_TRAIL_LOG_READS', False
            )
            _audit_settings_cache['last_refresh'] = current_time
    except Exception:
        pass  # Keep using cached values on error


def _resolve_username(user_id):
    """Return the real username for ``user_id`` from the JWT user cache,
    or ``None`` if the request is anonymous, missed the cache, or the
    cache backend errors out. Audit insert is fire-and-forget — never
    raise from here.
    """
    if not user_id or user_id == 'anonymous':
        return None
    try:
        cached_user = cache.get(f"jwt_user:{user_id}")
    except Exception:
        return None
    if cached_user:
        return cached_user.get('username')
    return None


def _sanitize_body(body):
    """
    Strip sensitive fields from request body dict.
    Returns sanitized copy of the dict.
    """
    if not isinstance(body, dict):
        return body

    sanitized = {}
    for key, value in body.items():
        if key.lower() in _SENSITIVE_FIELDS:
            sanitized[key] = '***REDACTED***'
        elif isinstance(value, dict):
            sanitized[key] = _sanitize_body(value)
        else:
            sanitized[key] = value
    return sanitized


def _serialize_details(details):
    """
    Serialize an audit annotation's details for the jsonb column.

    Applies the same recursive redaction as request bodies, then serializes
    with allow_nan=False: details are built in Python (unlike request_body,
    which comes from parsed JSON and cannot contain NaN), and psycopg2/Postgres
    reject NaN in jsonb. Any sanitize/serialize failure (a non-string key,
    say, which breaks _sanitize_body's key.lower()) drops the details but
    keeps the audit row; past the 64 KB cap a {"truncated": true} marker is
    stored instead. Never raises -- the insert is fire-and-forget.
    """
    try:
        serialized = json.dumps(
            _sanitize_body(details), default=str, allow_nan=False
        )
    except Exception as e:
        logger.warning(f"Audit annotation details dropped: {e}")
        return None
    if len(serialized) > _MAX_BODY_SIZE:
        return json.dumps({'truncated': True})
    return serialized


def init_audit_middleware(app: Flask) -> None:
    """
    Initialize audit trail middleware for the Flask application.

    This middleware captures mutating API calls (POST, PUT, PATCH, DELETE)
    and optionally GET requests to the audit_trail database table.

    Args:
        app: Flask application instance
    """

    @app.after_request
    def audit_after_request(response):
        """
        Capture request details to audit_trail table after response is built.
        Fire-and-forget: failures are logged as warnings, never block the response.
        """
        try:
            # Skip non-API paths
            if not request.path.startswith('/api/'):
                return response

            # Skip health/docs paths
            for prefix in _SKIP_PATH_PREFIXES:
                if request.path.startswith(prefix):
                    return response

            # Skip HEAD and OPTIONS
            if request.method in _SKIP_METHODS:
                return response

            # Refresh cached settings
            _refresh_audit_settings(current_app)

            # Skip GETs unless AUDIT_TRAIL_LOG_READS is enabled
            if request.method == 'GET' and not _audit_settings_cache['log_reads']:
                return response

            # request_id middleware skips the DB lookup on g.username; we
            # resolve the real username from the JWT cache instead.
            user_id = getattr(g, 'user_id', 'anonymous')
            if user_id == 'anonymous':
                user_id = None
            username = _resolve_username(user_id)

            # Read and sanitize request body (only JSON, under 64KB)
            request_body = None
            if (request.content_type and 'application/json' in request.content_type
                    and request.content_length is not None
                    and request.content_length < _MAX_BODY_SIZE):
                try:
                    raw_body = request.get_json(silent=True)
                    if raw_body is not None:
                        request_body = _sanitize_body(raw_body)
                except Exception:
                    pass

            # Response summary (first 500 chars of JSON response)
            response_summary = None
            if response.content_type and 'application/json' in response.content_type:
                try:
                    response_data = response.get_data(as_text=True)
                    if response_data:
                        response_summary = response_data[:500]
                except Exception:
                    pass

            # Duration from request_id middleware
            duration_ms = None
            if hasattr(g, 'request_start_time'):
                duration_ms = int((time.time() - g.request_start_time) * 1000)

            # Request ID from request_id middleware
            request_id = getattr(g, 'request_id', None)

            # Semantic annotation, if a handler attached one via
            # annotate_audit(). Merged into this same INSERT -- never a
            # second row. All five columns stay NULL when nothing annotated.
            annotation = get_audit_annotation() or {}
            resource_id = annotation.get('resource_id')
            details = annotation.get('details')
            details_json = _serialize_details(details) if details is not None else None

            # Insert audit record
            current_app.db_adapter.execute_query(
                """
                INSERT INTO audit_trail
                    (user_id, username, method, path, status_code,
                     ip_address, user_agent, request_body, response_summary,
                     duration_ms, request_id,
                     category, resource_type, resource_id, action, details)
                VALUES
                    (%(user_id)s::uuid, %(username)s, %(method)s, %(path)s, %(status_code)s,
                     %(ip_address)s, %(user_agent)s, %(request_body)s::jsonb, %(response_summary)s,
                     %(duration_ms)s, %(request_id)s,
                     %(category)s, %(resource_type)s, %(resource_id)s, %(action)s, %(details)s::jsonb)
                RETURNING id
                """,
                {
                    'user_id': str(user_id) if user_id is not None else None,
                    'username': username,
                    'method': request.method,
                    'path': request.path,
                    'status_code': response.status_code,
                    'ip_address': request.remote_addr,
                    'user_agent': request.headers.get('User-Agent'),
                    'request_body': json.dumps(request_body, default=str) if request_body is not None else None,
                    'response_summary': response_summary,
                    'duration_ms': duration_ms,
                    'request_id': request_id,
                    'category': annotation.get('category'),
                    'resource_type': annotation.get('resource_type'),
                    'resource_id': str(resource_id) if resource_id is not None else None,
                    'action': annotation.get('action'),
                    'details': details_json,
                }
            )

        except Exception as e:
            logger.warning(f"Audit trail capture failed: {e}")

        return response

    logger.info("Audit trail middleware initialized")
