# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Caching Utilities for AssetAtlas API

Provides caching infrastructure using Flask-Caching with Redis backend.
Includes cache initialization and custom cache decorators for specific use cases.

Usage:
    from src.api.utils.caching import cache, cached_connector_discovery, cached_dashboard

    # Initialize cache in factory
    cache.init_app(app)

    # Use decorators on endpoints
    @cached_connector_discovery
    def discover_connectors():
        # Expensive operation
        return connectors
"""

import hashlib
import logging
import time
import os
from functools import wraps
from typing import Optional, Dict, Any
from collections import defaultdict
from threading import Lock

from flask import request, current_app
from flask_caching import Cache

from src.modules.fields import catalog_version

logger = logging.getLogger(__name__)

# Cache statistics tracking
_cache_stats = defaultdict(lambda: {'hits': 0, 'misses': 0, 'sets': 0, 'errors': 0})
_cache_stats_lock = Lock()

# Enable detailed cache logging
ENABLE_CACHE_LOGGING = os.getenv('CACHE_ENABLE_LOGGING', 'true').lower() in ('true', '1', 'yes')
LOG_CACHE_HITS = os.getenv('CACHE_LOG_HITS', 'false').lower() in ('true', '1', 'yes')  # Usually too verbose

# Import metrics tracking (lazy import to avoid circular dependency)
def _get_track_cache_operation():
    """Lazy import to avoid circular dependency during initialization"""
    try:
        from src.api.utils.metrics import track_cache_operation
        return track_cache_operation
    except ImportError:
        return None

# Global cache instance (initialized in factory)
cache = Cache()


def _update_cache_stats(operation: str, result: str, cache_namespace: str = 'global'):
    """
    Update cache statistics for monitoring.

    Args:
        operation: Operation type ('get', 'set', 'delete')
        result: Result ('hit', 'miss', 'success', 'error')
        cache_namespace: Cache namespace for grouping stats
    """
    if not ENABLE_CACHE_LOGGING:
        return

    with _cache_stats_lock:
        if operation == 'get':
            if result == 'hit':
                _cache_stats[cache_namespace]['hits'] += 1
            elif result == 'miss':
                _cache_stats[cache_namespace]['misses'] += 1
        elif operation == 'set':
            if result == 'success':
                _cache_stats[cache_namespace]['sets'] += 1

        if result == 'error':
            _cache_stats[cache_namespace]['errors'] += 1


def _log_cache_operation(operation: str, result: str, cache_key: str, duration_ms: float, cache_namespace: str = 'global'):
    """
    Log cache operations with timing and context.

    Args:
        operation: Operation type ('get', 'set', 'delete')
        result: Result ('hit', 'miss', 'success', 'error')
        cache_key: Cache key accessed
        duration_ms: Operation duration in milliseconds
        cache_namespace: Cache namespace for grouping
    """
    if not ENABLE_CACHE_LOGGING:
        return

    # Only log hits if explicitly enabled (can be very verbose)
    if result == 'hit' and not LOG_CACHE_HITS:
        return

    # Log misses and sets at INFO level for visibility
    # Hits at DEBUG level (too verbose for production)
    if result == 'hit':
        log_level = logger.debug
        status = 'HIT'
    elif result == 'miss':
        log_level = logger.info
        status = 'MISS'
    elif result == 'success':
        log_level = logger.debug
        status = 'SET'
    else:
        log_level = logger.warning
        status = 'ERROR'

    # Calculate hit rate for this namespace
    hit_rate = get_cache_hit_rate(cache_namespace)

    log_level(
        f"Cache {status}: {cache_namespace}",
        extra={
            'cache_operation': operation,
            'cache_result': result,
            'cache_key': cache_key[:100],  # Truncate long keys
            'cache_duration_ms': round(duration_ms, 2),
            'cache_namespace': cache_namespace,
            'cache_hit_rate': round(hit_rate, 2) if hit_rate is not None else None
        }
    )


def get_cache_stats(cache_namespace: str = None) -> Dict[str, Any]:
    """
    Get cache statistics for monitoring.

    Args:
        cache_namespace: Optional namespace to filter stats (None = all namespaces)

    Returns:
        Dictionary with cache statistics
    """
    with _cache_stats_lock:
        if cache_namespace:
            stats = dict(_cache_stats.get(cache_namespace, {}))
            total_requests = stats.get('hits', 0) + stats.get('misses', 0)
            hit_rate = (stats.get('hits', 0) / total_requests * 100) if total_requests > 0 else 0

            return {
                'namespace': cache_namespace,
                'hits': stats.get('hits', 0),
                'misses': stats.get('misses', 0),
                'sets': stats.get('sets', 0),
                'errors': stats.get('errors', 0),
                'total_requests': total_requests,
                'hit_rate_percent': round(hit_rate, 2)
            }
        else:
            # Aggregate all namespaces
            all_stats = {
                'namespaces': {},
                'total_hits': 0,
                'total_misses': 0,
                'total_sets': 0,
                'total_errors': 0
            }

            for ns, stats in _cache_stats.items():
                all_stats['namespaces'][ns] = {
                    'hits': stats['hits'],
                    'misses': stats['misses'],
                    'sets': stats['sets'],
                    'errors': stats['errors']
                }
                all_stats['total_hits'] += stats['hits']
                all_stats['total_misses'] += stats['misses']
                all_stats['total_sets'] += stats['sets']
                all_stats['total_errors'] += stats['errors']

            total_requests = all_stats['total_hits'] + all_stats['total_misses']
            all_stats['total_requests'] = total_requests
            all_stats['overall_hit_rate_percent'] = (
                round(all_stats['total_hits'] / total_requests * 100, 2)
                if total_requests > 0 else 0
            )

            return all_stats


def get_cache_hit_rate(cache_namespace: str) -> Optional[float]:
    """
    Calculate cache hit rate percentage for a namespace.

    Args:
        cache_namespace: Cache namespace

    Returns:
        Hit rate percentage (0-100) or None if no requests yet
    """
    with _cache_stats_lock:
        stats = _cache_stats.get(cache_namespace, {})
        total = stats.get('hits', 0) + stats.get('misses', 0)
        if total == 0:
            return None
        return (stats.get('hits', 0) / total) * 100


def log_cache_stats(cache_namespace: str = None):
    """
    Log current cache statistics.

    Args:
        cache_namespace: Optional namespace to log (None = all)
    """
    stats = get_cache_stats(cache_namespace)

    if cache_namespace:
        logger.info(
            f"Cache statistics for {cache_namespace}",
            extra={
                'cache_namespace': cache_namespace,
                'cache_hits': stats['hits'],
                'cache_misses': stats['misses'],
                'cache_sets': stats['sets'],
                'cache_hit_rate': stats['hit_rate_percent']
            }
        )
    else:
        logger.info(
            "Overall cache statistics",
            extra={
                'cache_total_hits': stats['total_hits'],
                'cache_total_misses': stats['total_misses'],
                'cache_total_requests': stats['total_requests'],
                'cache_hit_rate': stats['overall_hit_rate_percent'],
                'cache_namespaces': list(stats['namespaces'].keys())
            }
        )


def init_cache(app):
    """
    Initialize Flask-Caching with the application.

    This should be called in the application factory after configuration is loaded.

    Args:
        app: Flask application instance

    Configuration:
        CACHE_TYPE: Type of cache backend (redis, simple, null)
        CACHE_REDIS_URL: Redis connection URL
        CACHE_DEFAULT_TIMEOUT: Default cache timeout in seconds
        CACHE_KEY_PREFIX: Prefix for all cache keys

    Example:
        from src.api.utils.caching import init_cache

        app = Flask(__name__)
        app.config.from_object(config)
        init_cache(app)
    """
    cache.init_app(app)
    logger.info(
        f"Cache initialized: type={app.config.get('CACHE_TYPE')}, "
        f"timeout={app.config.get('CACHE_DEFAULT_TIMEOUT')}s"
    )


def cached_connector_discovery(timeout: int = 300):
    """
    Cache decorator for connector discovery endpoints.

    Connector discovery is expensive (filesystem scanning, dynamic imports) but
    results rarely change. Cache for 5 minutes by default.

    Args:
        timeout: Cache timeout in seconds (default: 300 = 5 minutes)

    Usage:
        @cached_connector_discovery(timeout=600)
        def discover_connectors():
            return expensive_discovery()
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Create cache key based on function name and arguments
            cache_key = f"connector_discovery:{f.__name__}"
            cache_namespace = "connector_discovery"
            track_cache = _get_track_cache_operation()

            # Try to get from cache
            start_time = time.time()
            try:
                cached_value = cache.get(cache_key)
                duration_ms = (time.time() - start_time) * 1000

                if cached_value is not None:
                    # Cache HIT
                    _update_cache_stats('get', 'hit', cache_namespace)
                    _log_cache_operation('get', 'hit', cache_key, duration_ms, cache_namespace)
                    if track_cache:
                        track_cache('get', 'hit', duration_ms / 1000)
                    return cached_value

                # Cache MISS
                _update_cache_stats('get', 'miss', cache_namespace)
                _log_cache_operation('get', 'miss', cache_key, duration_ms, cache_namespace)
                if track_cache:
                    track_cache('get', 'miss', duration_ms / 1000)

            except Exception as e:
                logger.error(f"Cache GET error for {cache_key}: {e}")
                _update_cache_stats('get', 'error', cache_namespace)

            # Call function (cache miss or error)
            result = f(*args, **kwargs)

            # Set cache
            try:
                set_start = time.time()
                cache.set(cache_key, result, timeout=timeout)
                set_duration_ms = (time.time() - set_start) * 1000

                _update_cache_stats('set', 'success', cache_namespace)
                _log_cache_operation('set', 'success', cache_key, set_duration_ms, cache_namespace)
                if track_cache:
                    track_cache('set', 'success', set_duration_ms / 1000)

            except Exception as e:
                logger.error(f"Cache SET error for {cache_key}: {e}")
                _update_cache_stats('set', 'error', cache_namespace)

            return result

        return decorated_function
    return decorator


def _query_param_key(query_params: dict) -> str:
    """Stable cache-key component for a request's query parameters.

    Deliberately NOT `hash(frozenset(params.items()))`. Python randomises string
    hashing per process (PYTHONHASHSEED is unset), so identical requests
    produced a DIFFERENT key in each of the four gunicorn workers: one cache
    entry per worker, each filled and expiring at its own moment. Refreshing
    round-robins across workers, so the dashboard showed up to four different
    snapshots of the same data until every entry aged out. Only endpoints that
    take query params were affected — an empty frozenset hashes to a constant,
    which is why the parameterless cards looked fine.

    It also made keys unstable across restarts, orphaning entries in Redis
    until their TTL.

    md5 is a key-shortening device here, not a security boundary.
    """
    if not query_params:
        return "-"
    canonical = "&".join(f"{k}={query_params[k]}" for k in sorted(query_params))
    return hashlib.md5(canonical.encode()).hexdigest()[:16]


def cached_dashboard(timeout: int = 60):
    """
    Cache decorator for dashboard aggregation endpoints.

    Dashboard aggregations are expensive database queries but can tolerate
    slightly stale data. Cache for 1 minute by default.

    Args:
        timeout: Cache timeout in seconds (default: 60 = 1 minute)

    Usage:
        @cached_dashboard(timeout=120)
        def get_dashboard_stats():
            return expensive_aggregation()
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Create cache key including query parameters
            query_params = request.args.to_dict() if request else {}
            cache_key = f"dashboard:{f.__name__}:{_query_param_key(query_params)}"
            cache_namespace = "dashboard"
            track_cache = _get_track_cache_operation()

            # Try to get from cache
            start_time = time.time()
            try:
                cached_value = cache.get(cache_key)
                duration_ms = (time.time() - start_time) * 1000

                if cached_value is not None:
                    # Cache HIT
                    _update_cache_stats('get', 'hit', cache_namespace)
                    _log_cache_operation('get', 'hit', cache_key, duration_ms, cache_namespace)
                    if track_cache:
                        track_cache('get', 'hit', duration_ms / 1000)
                    return cached_value

                # Cache MISS
                _update_cache_stats('get', 'miss', cache_namespace)
                _log_cache_operation('get', 'miss', cache_key, duration_ms, cache_namespace)
                if track_cache:
                    track_cache('get', 'miss', duration_ms / 1000)

            except Exception as e:
                logger.error(f"Cache GET error for {cache_key}: {e}")
                _update_cache_stats('get', 'error', cache_namespace)

            # Call function (cache miss or error)
            result = f(*args, **kwargs)

            # Set cache
            try:
                set_start = time.time()
                cache.set(cache_key, result, timeout=timeout)
                set_duration_ms = (time.time() - set_start) * 1000

                _update_cache_stats('set', 'success', cache_namespace)
                _log_cache_operation('set', 'success', cache_key, set_duration_ms, cache_namespace)
                if track_cache:
                    track_cache('set', 'success', set_duration_ms / 1000)

            except Exception as e:
                logger.error(f"Cache SET error for {cache_key}: {e}")
                _update_cache_stats('set', 'error', cache_namespace)

            return result

        return decorated_function
    return decorator


def invalidate_connector_cache():
    """
    Invalidate all connector discovery cache entries.

    Use this when new connectors are added or existing ones are modified.

    Example:
        # After installing a new connector
        invalidate_connector_cache()
    """
    pattern = "assetatlas:connector_discovery:*"
    logger.info(f"Invalidating cache pattern: {pattern}")

    # Note: Cache invalidation by pattern requires Redis backend
    if current_app.config.get('CACHE_TYPE') == 'redis':
        redis_client = cache.cache._write_client
        count = 0
        for key in redis_client.scan_iter(match=pattern):
            redis_client.delete(key)
            count += 1
        if count:
            logger.info(f"Invalidated {count} connector cache entries")
    else:
        # For simple cache, clear entire cache
        cache.clear()
        logger.info("Cleared entire cache (simple cache backend)")


def invalidate_dashboard_cache() -> int:
    """
    Invalidate all dashboard aggregation cache entries. Returns entries removed.

    Called when the underlying data changes wholesale — chiefly at the end of a
    pipeline run. Without it the dashboard serves the previous run's numbers for
    up to the 60s TTL of each entry, which is what made a finished pipeline look
    like it had not landed.

    Works with NO application context on purpose. The pipeline's single
    completion point runs in the scheduler, which never pushes one, so a
    `current_app` read here would raise exactly where it matters most. The
    Redis cache is shared by every process, so a client built straight from
    REDIS_URL clears the same keys the api workers read.

    Never raises: a stale dashboard is a far better outcome than a pipeline run
    reported as failed because a cache flush could not connect.
    """
    pattern = "assetatlas:dashboard:*"

    redis_client, backend = _resolve_cache_client()
    if redis_client is None:
        if backend == 'simple':
            # Per-process memory; only reachable with an app context anyway.
            cache.clear()
            logger.info("Cleared entire cache (simple cache backend)")
        return 0

    try:
        count = 0
        for key in redis_client.scan_iter(match=pattern):
            redis_client.delete(key)
            count += 1
        if count:
            logger.info(f"Invalidated {count} dashboard cache entries")
        return count
    except Exception:
        logger.warning("Dashboard cache invalidation failed; entries will "
                       "expire on their own TTL", exc_info=True)
        return 0


def _resolve_cache_client():
    """Return ``(redis_client, backend)``; client is None when unavailable.

    Prefers the live Flask-Caching client when an app context exists, so an
    in-request caller reuses the configured pool. Falls back to a direct
    connection from REDIS_URL for context-free callers — the scheduler.
    """
    try:
        if current_app and current_app.config.get('CACHE_TYPE') == 'redis':
            return cache.cache._write_client, 'redis'
        if current_app:
            return None, 'simple'
    except RuntimeError:
        pass  # no application context — the scheduler path

    url = os.getenv('REDIS_URL')
    if not url:
        return None, 'none'
    try:
        import redis
        return redis.Redis.from_url(url), 'redis'
    except Exception:
        logger.warning("Could not build a Redis client for cache "
                       "invalidation", exc_info=True)
        return None, 'none'


def get_cached_group_mapping() -> dict:
    """
    Get integration group mapping from cache, falling back to DB.

    Returns:
        Dict of {integration_name: group_name} for grouped integrations.
        Empty dict if no groups exist.
    """
    cache_key = "integration_group_mapping"
    cache_namespace = "group_mapping"

    start_time = time.time()
    try:
        cached = cache.get(cache_key)
        duration_ms = (time.time() - start_time) * 1000

        if cached is not None:
            _update_cache_stats('get', 'hit', cache_namespace)
            _log_cache_operation('get', 'hit', cache_key, duration_ms, cache_namespace)
            return cached

        _update_cache_stats('get', 'miss', cache_namespace)
        _log_cache_operation('get', 'miss', cache_key, duration_ms, cache_namespace)
    except Exception as e:
        logger.error(f"Cache GET error for {cache_key}: {e}")
        _update_cache_stats('get', 'error', cache_namespace)

    # Cache miss — fetch from service
    try:
        mapping = current_app.integration_group_service.get_group_mapping()
    except Exception as e:
        logger.warning(f"Failed to fetch group mapping from DB: {e}")
        return {}

    try:
        set_start = time.time()
        cache.set(cache_key, mapping, timeout=300)
        set_duration_ms = (time.time() - set_start) * 1000
        _update_cache_stats('set', 'success', cache_namespace)
        _log_cache_operation('set', 'success', cache_key, set_duration_ms, cache_namespace)
    except Exception as e:
        logger.error(f"Cache SET error for {cache_key}: {e}")
        _update_cache_stats('set', 'error', cache_namespace)

    return mapping


def invalidate_group_mapping_cache():
    """Invalidate group mapping cache. Call after any group mutation."""
    cache_key = "integration_group_mapping"
    try:
        cache.delete(cache_key)
        logger.info("Invalidated integration group mapping cache")
    except Exception as e:
        logger.error(f"Cache DELETE error for {cache_key}: {e}")
    # Group membership changes the catalog's integration_group entries; bump
    # the shared stamp so every worker's FieldCatalog rebuilds (#1413).
    # Never raises; no-op without Redis.
    catalog_version.bump()


def clear_all_cache():
    """
    Clear all cache entries.

    Use with caution - this clears the entire cache.

    Example:
        # During maintenance or after configuration changes
        clear_all_cache()
    """
    cache.clear()
    logger.warning("Cleared all cache entries")
