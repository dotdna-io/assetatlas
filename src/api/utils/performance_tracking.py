# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Performance Tracking and Monitoring

Provides performance budget warnings and latency percentile tracking for API endpoints.
Helps identify performance issues and track performance trends over time.

Usage:
    from src.api.utils.performance_tracking import init_performance_tracking, log_performance_stats

    # Initialize in factory
    init_performance_tracking(app)

    # Log stats periodically
    log_performance_stats()
"""

import logging
import os
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Dict, List, Optional, Tuple
import statistics

logger = logging.getLogger(__name__)

# Performance tracking configuration
ENABLE_PERFORMANCE_TRACKING = os.getenv('PERF_ENABLE_TRACKING', 'true').lower() in ('true', '1', 'yes')
ENABLE_BUDGET_WARNINGS = os.getenv('PERF_ENABLE_BUDGET_WARNINGS', 'true').lower() in ('true', '1', 'yes')
DEFAULT_PERFORMANCE_BUDGET_MS = int(os.getenv('PERF_DEFAULT_BUDGET_MS', '1000'))  # 1 second default

# Performance budgets per endpoint (path pattern -> budget in ms)
PERFORMANCE_BUDGETS = {
    '/api/health': 100,           # Health checks should be very fast
    '/api/assets': 500,            # Asset listings
    '/api/assets/search': 1000,    # Search can be slower
    '/api/dashboard': 2000,        # Dashboard aggregations allowed to be slower
    '/api/integrations': 500,      # Integration listings
    '/api/tag-rules': 300,         # Tag rule operations
}

# Latency tracking storage (path -> deque of latencies)
# Use deque with maxlen to automatically discard old data
_latency_data = defaultdict(lambda: deque(maxlen=1000))  # Keep last 1000 requests per endpoint
_latency_lock = Lock()

# Performance budget violations tracker
_budget_violations = defaultdict(int)
_budget_violations_lock = Lock()


def _get_performance_budget(path: str) -> int:
    """
    Get performance budget for an endpoint.

    Args:
        path: Request path

    Returns:
        Performance budget in milliseconds
    """
    # Try exact match first
    if path in PERFORMANCE_BUDGETS:
        return PERFORMANCE_BUDGETS[path]

    # Try prefix match (for parameterized routes)
    for pattern, budget in PERFORMANCE_BUDGETS.items():
        if path.startswith(pattern):
            return budget

    # Return default budget
    return DEFAULT_PERFORMANCE_BUDGET_MS


def track_request_latency(path: str, duration_ms: float, status_code: int):
    """
    Track request latency for percentile analysis and budget monitoring.

    Args:
        path: Request path
        duration_ms: Request duration in milliseconds
        status_code: HTTP status code
    """
    if not ENABLE_PERFORMANCE_TRACKING:
        return

    # Normalize path (remove query params, IDs)
    normalized_path = _normalize_path(path)

    # Store latency data for percentile calculation
    with _latency_lock:
        _latency_data[normalized_path].append({
            'duration_ms': duration_ms,
            'status_code': status_code,
            'timestamp': time.time()
        })

    # Check performance budget
    if ENABLE_BUDGET_WARNINGS and status_code < 500:  # Only check successful requests
        budget_ms = _get_performance_budget(normalized_path)

        if duration_ms > budget_ms:
            # Budget exceeded
            with _budget_violations_lock:
                _budget_violations[normalized_path] += 1

            # Calculate how much over budget
            over_budget_ms = duration_ms - budget_ms
            over_budget_pct = (over_budget_ms / budget_ms) * 100

            logger.warning(
                f"Performance budget exceeded: {normalized_path}",
                extra={
                    'endpoint': normalized_path,
                    'duration_ms': round(duration_ms, 2),
                    'budget_ms': budget_ms,
                    'over_budget_ms': round(over_budget_ms, 2),
                    'over_budget_percent': round(over_budget_pct, 2),
                    'status_code': status_code,
                    'total_violations': _budget_violations[normalized_path]
                }
            )


def _normalize_path(path: str) -> str:
    """
    Normalize request path for grouping.

    Replaces UUIDs and numeric IDs with placeholders.

    Args:
        path: Original path

    Returns:
        Normalized path
    """
    import re

    # Remove query parameters
    path = path.split('?')[0]

    # Replace UUIDs with {id}
    path = re.sub(
        r'/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
        '/{id}',
        path,
        flags=re.IGNORECASE
    )

    # Replace numeric IDs with {id}
    path = re.sub(r'/\d+(?=/|$)', '/{id}', path)

    return path


def calculate_latency_percentiles(path: str = None) -> Dict[str, any]:
    """
    Calculate latency percentiles (P50, P95, P99) for endpoints.

    Args:
        path: Optional specific path to calculate (None = all endpoints)

    Returns:
        Dictionary with percentile statistics
    """
    if not ENABLE_PERFORMANCE_TRACKING:
        return {}

    with _latency_lock:
        if path:
            # Calculate for specific endpoint
            if path not in _latency_data or len(_latency_data[path]) == 0:
                return {}

            latencies = [entry['duration_ms'] for entry in _latency_data[path]]
            return _calculate_percentiles_for_data(path, latencies)
        else:
            # Calculate for all endpoints
            all_stats = {}
            for endpoint_path, entries in _latency_data.items():
                if len(entries) > 0:
                    latencies = [entry['duration_ms'] for entry in entries]
                    all_stats[endpoint_path] = _calculate_percentiles_for_data(endpoint_path, latencies)

            return all_stats


def _calculate_percentiles_for_data(path: str, latencies: List[float]) -> Dict[str, float]:
    """
    Calculate percentile statistics for a list of latencies.

    Args:
        path: Endpoint path
        latencies: List of latency values in ms

    Returns:
        Dictionary with statistics
    """
    if not latencies:
        return {}

    try:
        return {
            'path': path,
            'sample_size': len(latencies),
            'min_ms': round(min(latencies), 2),
            'max_ms': round(max(latencies), 2),
            'mean_ms': round(statistics.mean(latencies), 2),
            'median_ms': round(statistics.median(latencies), 2),
            'p50_ms': round(statistics.quantiles(latencies, n=2)[0], 2),  # 50th percentile
            'p95_ms': round(statistics.quantiles(latencies, n=20)[18], 2),  # 95th percentile
            'p99_ms': round(statistics.quantiles(latencies, n=100)[98], 2),  # 99th percentile
            'stddev_ms': round(statistics.stdev(latencies), 2) if len(latencies) > 1 else 0.0
        }
    except Exception as e:
        logger.error(f"Error calculating percentiles for {path}: {e}")
        return {}


def get_performance_summary() -> Dict[str, any]:
    """
    Get overall performance summary across all endpoints.

    Returns:
        Dictionary with performance summary
    """
    if not ENABLE_PERFORMANCE_TRACKING:
        return {'enabled': False}

    with _latency_lock, _budget_violations_lock:
        total_requests = sum(len(entries) for entries in _latency_data.values())
        total_violations = sum(_budget_violations.values())

        # Find slowest endpoints
        slowest_endpoints = []
        for path, entries in _latency_data.items():
            if entries:
                avg_latency = statistics.mean([e['duration_ms'] for e in entries])
                slowest_endpoints.append((path, avg_latency))

        slowest_endpoints.sort(key=lambda x: x[1], reverse=True)

        return {
            'enabled': True,
            'total_requests_tracked': total_requests,
            'total_budget_violations': total_violations,
            'endpoints_tracked': len(_latency_data),
            'slowest_endpoints': [
                {'path': path, 'avg_ms': round(avg, 2)}
                for path, avg in slowest_endpoints[:5]
            ]
        }


def log_performance_stats(path: str = None):
    """
    Log performance statistics for monitoring.

    Args:
        path: Optional specific path to log (None = all endpoints)
    """
    if not ENABLE_PERFORMANCE_TRACKING:
        return

    if path:
        # Log specific endpoint
        stats = calculate_latency_percentiles(path)
        if stats:
            logger.info(
                f"Performance statistics for {path}",
                extra={
                    'endpoint': path,
                    'sample_size': stats['sample_size'],
                    'p50_ms': stats['p50_ms'],
                    'p95_ms': stats['p95_ms'],
                    'p99_ms': stats['p99_ms'],
                    'mean_ms': stats['mean_ms'],
                    'max_ms': stats['max_ms']
                }
            )
    else:
        # Log summary
        summary = get_performance_summary()
        logger.info(
            "Overall performance summary",
            extra={
                'total_requests': summary['total_requests_tracked'],
                'budget_violations': summary['total_budget_violations'],
                'endpoints_tracked': summary['endpoints_tracked']
            }
        )

        # Log top 5 slowest endpoints
        all_stats = calculate_latency_percentiles()
        if all_stats:
            logger.info(
                f"Tracked {len(all_stats)} endpoints with latency data",
                extra={'endpoint_count': len(all_stats)}
            )


def reset_performance_data():
    """
    Reset all performance tracking data.

    Use with caution - clears all latency data and violation counts.
    """
    with _latency_lock, _budget_violations_lock:
        _latency_data.clear()
        _budget_violations.clear()
    logger.info("Performance tracking data reset")


def init_performance_tracking(app):
    """
    Initialize performance tracking for the Flask application.

    This should be called in the application factory.

    Args:
        app: Flask application instance
    """
    if ENABLE_PERFORMANCE_TRACKING:
        logger.info(
            "Performance tracking initialized",
            extra={
                'budget_warnings_enabled': ENABLE_BUDGET_WARNINGS,
                'default_budget_ms': DEFAULT_PERFORMANCE_BUDGET_MS,
                'endpoint_budgets': len(PERFORMANCE_BUDGETS)
            }
        )
    else:
        logger.info("Performance tracking disabled (PERF_ENABLE_TRACKING=false)")
