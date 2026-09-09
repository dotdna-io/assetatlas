# SPDX-License-Identifier: AGPL-3.0-or-later
"""API key authentication decorator for external API endpoints."""

import hashlib
import hmac
import logging
import os
import threading
import time
from functools import wraps
from typing import Optional
from flask import abort, request, g, jsonify, current_app

logger = logging.getLogger(__name__)

API_KEY_HEADER = 'X-API-Key'

# In-memory rate limit counters (per-process; for multi-process, use Redis).
# The lock guards the read-modify-write in _check_rate_limit: without it,
# threaded workers lose updates and the limit drifts permissive.
_rate_counters: dict = {}
_rate_counters_lock = threading.Lock()


def _check_rate_limit(key_id: str, limit: Optional[int] = None) -> tuple:
    """
    Check if the API key has exceeded its rate limit.

    Returns (allowed: bool, retry_after: int).
    Uses a sliding window counter per minute. ``limit`` is the per-minute
    budget; when omitted it falls back to ``EXTERNAL_API_RATE_LIMIT`` so
    existing callers are unaffected.
    """
    if limit is None:
        limit = current_app.config.get('EXTERNAL_API_RATE_LIMIT', 60)
    now = time.time()
    window_start = now - 60

    with _rate_counters_lock:
        if key_id not in _rate_counters:
            _rate_counters[key_id] = []

        # Prune old timestamps
        _rate_counters[key_id] = [t for t in _rate_counters[key_id] if t > window_start]

        if len(_rate_counters[key_id]) >= limit:
            oldest = _rate_counters[key_id][0]
            retry_after = int(oldest + 60 - now) + 1
            return False, max(retry_after, 1)

        _rate_counters[key_id].append(now)
        return True, 0


def require_api_key(fn=None, *, rate_limit_setting='EXTERNAL_API_RATE_LIMIT'):
    """
    Decorator that authenticates requests via the X-API-Key header.

    Usable bare or parameterized::

        @require_api_key                                          # 60/min default
        @require_api_key(rate_limit_setting='EXTERNAL_API_INVENTORY_RATE_LIMIT')

    ``rate_limit_setting`` names the Flask config key holding this route's
    per-minute budget. It is resolved per request and falls back to
    ``EXTERNAL_API_RATE_LIMIT`` when the named key is absent, so the existing
    bare usage is unchanged.

    On success, sets:
        g.api_key_id   — UUID of the API key
        g.api_key_user — dict with user_id, username, role

    On failure, returns 401, 429, or other JSON error response.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = request.headers.get(API_KEY_HEADER)
            if not key:
                return jsonify({
                    'success': False,
                    'error': 'missing_api_key',
                    'message': f'Missing {API_KEY_HEADER} header',
                }), 401

            # Rate limit by raw key hash before hitting DB. Pass this route's
            # budget through; _check_rate_limit applies the EXTERNAL_API_RATE_LIMIT
            # fallback when the named setting is absent (single fallback site).
            key_hash = hashlib.sha256(key.encode()).hexdigest()
            allowed, retry_after = _check_rate_limit(
                key_hash, current_app.config.get(rate_limit_setting)
            )
            if not allowed:
                return jsonify({
                    'success': False,
                    'error': 'rate_limit_exceeded',
                    'message': 'Rate limit exceeded. Try again later.',
                }), 429, {'Retry-After': str(retry_after)}

            service = current_app.api_key_service
            key_data = service.validate_key(key)

            if key_data is None:
                logger.warning("Invalid API key attempt from %s", request.remote_addr)
                return jsonify({
                    'success': False,
                    'error': 'invalid_api_key',
                    'message': 'Invalid, expired, or revoked API key',
                }), 401

            # Set request context for downstream handlers
            g.api_key_id = key_data['id']
            g.api_key_user = {
                'user_id': key_data['user_id'],
                'username': key_data['username'],
                'role': key_data['role'],
            }

            # Record usage (fire and forget; throttled to one stamp per
            # LAST_USED_STAMP_INTERVAL_S inside record_usage)
            try:
                service.record_usage(
                    key_data['id'],
                    request.remote_addr or '0.0.0.0',
                    key_data.get('last_used_at'),
                )
            except Exception:
                logger.debug("Failed to record API key usage", exc_info=True)

            return fn(*args, **kwargs)

        return wrapper

    # Bare usage (@require_api_key) passes the function directly; parameterized
    # usage (@require_api_key(...)) passes no function and returns the decorator.
    return decorator(fn) if fn is not None else decorator


def require_service_api_key(env_var: str):
    """Flask decorator: gate a route on a constant-time match against the
    value of an environment variable. Use for service-to-service callers
    (the Teams bridge) that don't have a JWT.

    Behavior:
      - X-Service-Key header missing → 401
      - X-Service-Key header doesn't match → 403
      - Configured env var unset OR shorter than 32 chars → 503 (fail closed,
        so a misconfigured install never accepts traffic instead of failing
        loudly).

    The env var is read at each request rather than at decorator-call time
    so test monkeypatching works as expected.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            expected = os.environ.get(env_var, "")
            if not expected or len(expected) < 32:
                return jsonify(
                    success=False,
                    error="service_unavailable",
                    message=f"{env_var} not set or too short",
                ), 503
            presented = request.headers.get("X-Service-Key", "")
            if not presented:
                abort(401)
            if not hmac.compare_digest(presented, expected):
                abort(403)
            return fn(*args, **kwargs)
        return wrapper

    return decorator
