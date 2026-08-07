# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Health Blueprint

Provides comprehensive health check and status endpoints for monitoring application health.

Kubernetes-compatible endpoints:
- GET /api/health - Overall health status (existing endpoint, maintained for compatibility)
- GET /api/health/live - Liveness probe (is app running?)
- GET /api/health/ready - Readiness probe (can app accept traffic?)

Each endpoint checks:
- Database connectivity
- Cache connectivity (Redis)
- Scheduler status
- Application metadata (version, uptime, etc.)
"""

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from flask import Blueprint, current_app, jsonify
from flask_jwt_extended import jwt_required
from typing import Dict, Any, Tuple

from src.api.middleware.error_handler import handle_errors
from src.auth.middleware.rbac import require_admin
from src.api.utils.caching import cache
from src.api.modules.settings.settings_schema import monitoring_dashboards_enabled
from src.modules.alerts.update_check import (
    fetch_latest_version, is_newer, manifest_url_for, _CACHE_TTL_SECONDS,
)

logger = logging.getLogger(__name__)

# Create blueprint
health_bp = Blueprint('health', __name__, url_prefix='/api')

# Track application start time
app_start_time = time.time()


# =============================================================================
# Health Check Helper Functions
# =============================================================================

def check_database() -> Dict[str, Any]:
    """
    Check database connectivity and pool status.

    Returns:
        Dict with status, message, and optional details
    """
    try:
        db_adapter = current_app.db_adapter

        # Test simple query
        result = db_adapter.execute_query("SELECT 1 as test", {})

        # Get connection pool stats if available
        pool_stats = {}
        if hasattr(current_app, 'connection_manager'):
            conn_mgr = current_app.connection_manager
            if hasattr(conn_mgr, 'get_pool_stats'):
                pool_stats = conn_mgr.get_pool_stats()

                # Add usage percentage if we have connection estimate
                if 'total_connections_estimate' in pool_stats and \
                   isinstance(pool_stats['total_connections_estimate'], int):
                    total = pool_stats['total_connections_estimate']
                    max_conns = pool_stats['max_connections']
                    pool_stats['usage_percent'] = round((total / max_conns) * 100, 1)

        return {
            'status': 'healthy',
            'message': 'Database connection successful',
            'pool': pool_stats
        }
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return {
            'status': 'unhealthy',
            'message': f'Database connection failed: {str(e)}'
        }


def check_cache() -> Dict[str, Any]:
    """
    Check Redis cache connectivity.

    Returns:
        Dict with status, message, and optional details
    """
    try:
        from src.api.utils.caching import cache

        # Skip check if cache is not Redis
        if current_app.config.get('CACHE_TYPE') != 'redis':
            return {
                'status': 'n/a',
                'message': f"Cache type: {current_app.config.get('CACHE_TYPE', 'unknown')}"
            }

        # Test cache connectivity with a ping
        test_key = '_health_check_test'
        test_value = str(time.time())

        # Try to set and get a value
        cache.set(test_key, test_value, timeout=5)
        retrieved = cache.get(test_key)

        if retrieved == test_value:
            # Clean up test key
            cache.delete(test_key)
            return {
                'status': 'healthy',
                'message': 'Cache connection successful',
                'type': 'redis'
            }
        else:
            return {
                'status': 'degraded',
                'message': 'Cache read/write test failed',
                'type': 'redis'
            }

    except Exception as e:
        logger.error(f"Cache health check failed: {e}")
        return {
            'status': 'unhealthy',
            'message': f'Cache connection failed: {str(e)}'
        }


def check_scheduler() -> Dict[str, Any]:
    """
    Check scheduler service status.

    The scheduler may run in-process (embedded mode) or as a separate worker
    process that publishes heartbeats to Redis key ``scheduler:status``.

    Returns:
        Dict with status, message, and optional details
    """
    try:
        scheduler_mode = current_app.config.get('SCHEDULER_MODE', 'disabled')

        # When mode is 'disabled', the scheduler runs as a separate worker.
        # Check Redis for its heartbeat (key has 120s TTL).
        if scheduler_mode == 'disabled':
            return _check_scheduler_via_redis()

        # In-process scheduler (embedded mode)
        if not hasattr(current_app, 'scheduler_service'):
            return {
                'status': 'unavailable',
                'message': 'Scheduler service not initialized',
                'mode': scheduler_mode
            }

        scheduler_service = current_app.scheduler_service
        scheduler = scheduler_service.scheduler

        if scheduler and scheduler.running:
            jobs = scheduler.get_jobs()
            job_count = len(jobs)

            next_run = None
            if jobs:
                next_runs = [getattr(job, 'next_run_time', None) for job in jobs if getattr(job, 'next_run_time', None)]
                if next_runs:
                    next_run = min(next_runs).isoformat()

            return {
                'status': 'healthy',
                'message': 'Scheduler is running',
                'mode': scheduler_mode,
                'jobs': job_count,
                'next_run': next_run
            }
        else:
            return {
                'status': 'unhealthy',
                'message': 'Scheduler is not running',
                'mode': scheduler_mode
            }

    except Exception as e:
        logger.error(f"Scheduler health check failed: {e}")
        return {
            'status': 'unhealthy',
            'message': f'Scheduler check failed: {str(e)}'
        }


def _check_scheduler_via_redis() -> Dict[str, Any]:
    """Read the scheduler heartbeat from Redis (``scheduler:status`` key)."""
    try:
        import redis as redis_lib
        redis_url = os.getenv('REDIS_URL')
        if not redis_url:
            return {
                'status': 'unknown',
                'message': 'Redis not configured; cannot reach scheduler worker',
                'mode': 'worker'
            }

        r = redis_lib.from_url(redis_url)
        raw = r.get('scheduler:status')
        if not raw:
            return {
                'status': 'unavailable',
                'message': 'Scheduler worker not reporting (heartbeat expired or not started)',
                'mode': 'worker'
            }

        import json
        status = json.loads(raw)
        job_count = len(status.get('jobs', []))
        health = status.get('health_status', 'unknown')

        result = {
            'status': 'healthy' if health in ('healthy', 'running') else health,
            'message': f'Scheduler worker running — {job_count} jobs',
            'mode': 'worker',
            'jobs': job_count,
            'updated_at': status.get('updated_at'),
        }

        # Include next run time from jobs list
        jobs = status.get('jobs', [])
        next_runs = [j['next_run_time'] for j in jobs if j.get('next_run_time')]
        if next_runs:
            result['next_run'] = min(next_runs)

        return result

    except Exception as e:
        logger.error(f"Scheduler Redis heartbeat check failed: {e}")
        return {
            'status': 'unknown',
            'message': f'Could not read scheduler heartbeat: {str(e)}',
            'mode': 'worker'
        }


def _git_commit_date() -> str:
    """Return the date of the latest git commit, or 'unknown'."""
    try:
        import subprocess
        result = subprocess.run(
            ['git', 'log', '-1', '--format=%cs'],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return 'unknown'


def _chat_feature_enabled() -> bool:
    """Whether the UI should mount the chat widget.

    Deliberately NOT `app.chat_service is not None`. The api runs four gunicorn
    workers, each builds its own chat_service, and this poll is answered by
    whichever one gunicorn picks — so right after a first-time provider save
    three workers in four reported chat unavailable and the widget stayed
    hidden. The user then could not send the chat request that heals a cold
    worker, so the only ways out were a container restart or getting lucky on a
    poll. Same shape as the `monitoring` flag below, and for the same reason:
    the question is "is this configured", which is a DB fact.
    """
    if not current_app.config.get('CHAT_ENABLED', False):
        return False
    providers_service = getattr(current_app, 'chat_providers_service', None)
    if providers_service is None:
        return getattr(current_app, 'chat_service', None) is not None
    try:
        # bool(): this lands in a jsonify'd payload, so anything non-boolean
        # leaking out of the service would 500 the whole health response.
        return bool(providers_service.is_configured())
    except Exception:
        # A health endpoint must survive the outage it exists to report. Fall
        # back to this worker's own view rather than failing the whole payload.
        logger.warning("Chat availability probe failed; reporting local state",
                       exc_info=True)
        return getattr(current_app, 'chat_service', None) is not None


def get_application_info() -> Dict[str, Any]:
    """
    Get application metadata and runtime information.

    Returns:
        Dict with application info
    """
    uptime_seconds = time.time() - app_start_time

    return {
        'name': current_app.config.get('APP_NAME', 'AssetAtlas Backend API'),
        'version': current_app.config.get('APP_VERSION', '0.0.0-dev'),
        'build_date': os.getenv('BUILD_DATE') or _git_commit_date(),
        'git_commit': os.getenv('GIT_COMMIT', 'unknown'),
        'environment': current_app.config.get('FLASK_ENV', 'unknown'),
        'debug': current_app.debug,
        'uptime_seconds': round(uptime_seconds, 2),
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'features': {
            'chat': _chat_feature_enabled(),
            # Effective on/off of the dashboards (runtime `monitoring.dashboards`
            # setting, defaulting to the MONITORING_ENABLED env). Drives the UI's
            # useMonitoringAvailable() — the service is now always attached, so
            # "is not None" would always be true.
            'monitoring': monitoring_dashboards_enabled(
                getattr(current_app, 'settings_manager', None),
                current_app.config.get('MONITORING_ENABLED', False),
            ),
        },
    }


def check_log_files() -> Dict[str, Any]:
    """
    Check log file health status.

    Verifies:
    - Log directories exist and are writable
    - Log files exist and are writable
    - Log file sizes and rotation status
    - Disk space availability

    Returns:
        Dict with log file health status
    """
    try:
        log_dirs = current_app.config.get('LOG_DIRECTORIES', {})
        log_handlers = current_app.config.get('LOG_FILE_HANDLERS', {})

        if not log_dirs:
            return {
                'status': 'disabled',
                'message': 'File logging is disabled',
                'enabled': False
            }

        files_status = {}
        total_size_bytes = 0
        issues = []

        # Check each log file
        for handler_name, handler in log_handlers.items():
            if hasattr(handler, 'baseFilename'):
                filepath = Path(handler.baseFilename)

                file_info = {
                    'path': str(filepath),
                    'exists': filepath.exists(),
                    'writable': False,
                    'size_bytes': 0,
                    'size_mb': 0,
                    'rotation': {
                        'max_bytes': handler.maxBytes if hasattr(handler, 'maxBytes') else 0,
                        'backup_count': handler.backupCount if hasattr(handler, 'backupCount') else 0
                    }
                }

                if filepath.exists():
                    try:
                        file_stat = filepath.stat()
                        file_info['size_bytes'] = file_stat.st_size
                        file_info['size_mb'] = round(file_stat.st_size / (1024 * 1024), 2)
                        file_info['writable'] = os.access(filepath, os.W_OK)
                        # tz=timezone.utc, not a bare + 'Z': fromtimestamp() with no tz
                        # returns naive LOCAL wall-clock, so labelling it UTC put the
                        # instant out by the host's offset (#1162).
                        file_info['last_modified'] = datetime.fromtimestamp(
                            file_stat.st_mtime, tz=timezone.utc
                        ).isoformat().replace('+00:00', 'Z')
                        total_size_bytes += file_stat.st_size

                        # Check for rotation backups
                        backup_count = len(list(filepath.parent.glob(f'{filepath.name}.*')))
                        file_info['backup_files'] = backup_count

                        if not file_info['writable']:
                            issues.append(f'{handler_name} log file is not writable')

                    except Exception as e:
                        file_info['error'] = str(e)
                        issues.append(f'{handler_name} log file error: {e}')
                else:
                    issues.append(f'{handler_name} log file does not exist')

                files_status[handler_name] = file_info

        # Check directories
        directories_status = {}
        for dir_name, dir_path in log_dirs.items():
            dir_info = {
                'path': str(dir_path),
                'exists': dir_path.exists(),
                'writable': os.access(dir_path, os.W_OK) if dir_path.exists() else False
            }

            if not dir_info['exists']:
                issues.append(f'Log directory {dir_name} does not exist')
            elif not dir_info['writable']:
                issues.append(f'Log directory {dir_name} is not writable')

            directories_status[dir_name] = dir_info

        # Determine overall status
        if issues:
            status = 'degraded' if len(issues) < len(log_handlers) else 'unhealthy'
        else:
            status = 'healthy'

        return {
            'status': status,
            'message': 'Log file system is operational' if status == 'healthy' else f'{len(issues)} issues found',
            'enabled': True,
            'files': files_status,
            'directories': directories_status,
            'total_size_mb': round(total_size_bytes / (1024 * 1024), 2),
            'issues': issues
        }

    except Exception as e:
        logger.error(f"Log file health check failed: {e}")
        return {
            'status': 'unhealthy',
            'message': f'Log file check failed: {str(e)}',
            'enabled': False
        }


def check_pipeline_in_flight() -> Dict[str, Any]:
    """
    Check whether any pipeline run is currently 'pending' or 'running'.

    Returns:
        Dict with status ('ok' or 'error'), in_flight (bool), and optional message.
    """
    try:
        rows = current_app.db_adapter.execute_query(
            "SELECT EXISTS(SELECT 1 FROM pipeline_runs WHERE status IN ('pending', 'running')) AS in_flight"
        )
        in_flight = bool(rows[0]['in_flight']) if rows else False
        return {'status': 'ok', 'in_flight': in_flight}
    except Exception as e:
        logger.warning(f"In-flight check failed: {e}")
        return {'status': 'error', 'message': 'DB unavailable'}


def check_chromadb() -> Dict[str, Any]:
    """Check ChromaDB availability (non-critical).

    OBSERVE ONLY — a health probe must never construct the embedding stack.
    Reading the ``chromadb_store`` property lazily builds a SynonymGenerator,
    a ChromaDBStore and two EmbeddingService instances (ONNX model load under
    an exclusive cross-process file lock) inside whichever gunicorn worker
    answers the request, so the first probe of a cold worker blocks for the
    entire model load (#1390). A stack no code path has needed yet is
    reported as 'initializing', not as a fault.
    """
    try:
        service = getattr(current_app, 'embedding_integration_service', None)
        if service is None:
            return {'status': 'unavailable', 'message': 'ChromaDB not configured'}
        store = service.chromadb_store_if_initialized
        if store:
            stats = store.get_collection_stats()
            return {
                'status': 'available',
                'fields_count': stats.get('fields_count', 0),
                'values_count': stats.get('values_count', 0),
            }
        if service.is_heavy_loaded:
            # The build ran and failed (store is None) — that IS a fault.
            return {'status': 'unavailable', 'message': 'ChromaDB not configured'}
        return {
            'status': 'initializing',
            'message': 'Embedding stack not loaded yet in this worker',
        }
    except Exception as e:
        return {
            'status': 'unavailable',
            'message': f'ChromaDB not available: {str(e)}'
        }


# =============================================================================
# Health Endpoints
# =============================================================================

@health_bp.route('/health', methods=['GET'])
@handle_errors
def health_check():
    """
    Overall health check endpoint (legacy compatibility).
    ---
    tags:
      - Health
    summary: Comprehensive health check
    description: |
      Performs all health checks and returns comprehensive status.
      Returns 200 if all critical dependencies are healthy, 503 otherwise.

      Checks performed:
      - Database connectivity and connection pool status
      - Redis cache connectivity (if enabled)
      - Scheduler status and job count
      - Application metadata and uptime
    responses:
      200:
        description: System is healthy
        schema:
          type: object
          properties:
            status:
              type: string
              enum: [healthy, unhealthy]
            checks:
              type: object
              properties:
                database:
                  type: object
                  properties:
                    status:
                      type: string
                    message:
                      type: string
                    pool:
                      type: object
                cache:
                  type: object
                  properties:
                    status:
                      type: string
                    message:
                      type: string
                scheduler:
                  type: object
                  properties:
                    status:
                      type: string
                    message:
                      type: string
                    jobs:
                      type: integer
            application:
              type: object
              properties:
                name:
                  type: string
                version:
                  type: string
                environment:
                  type: string
                uptime_seconds:
                  type: number
      503:
        description: System is unhealthy (critical dependency failed)
    """
    logger.debug("GET /api/health")

    # Perform all health checks
    db_status = check_database()
    cache_status = check_cache()
    scheduler_status = check_scheduler()
    app_info = get_application_info()

    # Determine overall health
    # Critical: database must be healthy
    # Non-critical: cache and scheduler can be degraded
    is_healthy = db_status['status'] == 'healthy'

    # Prepare response
    response = {
        'status': 'healthy' if is_healthy else 'unhealthy',
        'checks': {
            'database': db_status,
            'cache': cache_status,
            'scheduler': scheduler_status
        },
        'application': app_info
    }

    # Non-critical components
    chromadb_status = check_chromadb()
    response['checks']['chromadb'] = chromadb_status

    # Return appropriate status code
    status_code = 200 if is_healthy else 503

    return jsonify(response), status_code


@health_bp.route('/health/live', methods=['GET'])
def liveness_probe():
    """
    Kubernetes liveness probe endpoint.
    ---
    tags:
      - Health
    summary: Liveness probe (is app alive?)
    description: |
      Checks if the application is running and able to process requests.
      This is a lightweight check - only verifies the app is alive.

      Use this for Kubernetes liveness probes. If this fails, K8s will restart the pod.

      **Always returns 200 unless the application is completely unresponsive.**
    responses:
      200:
        description: Application is alive and responding
        schema:
          type: object
          properties:
            status:
              type: string
              enum: [alive]
            application:
              type: object
              properties:
                name:
                  type: string
                version:
                  type: string
                uptime_seconds:
                  type: number
            timestamp:
              type: string
              format: date-time
    """
    logger.debug("GET /api/health/live")

    app_info = get_application_info()

    return jsonify({
        'status': 'alive',
        'application': {
            'name': app_info['name'],
            'version': app_info['version'],
            'uptime_seconds': app_info['uptime_seconds']
        },
        'timestamp': app_info['timestamp']
    }), 200


@health_bp.route('/health/ready', methods=['GET'])
def readiness_probe():
    """
    Kubernetes readiness probe endpoint.
    ---
    tags:
      - Health
    summary: Readiness probe (can app accept traffic?)
    description: |
      Checks if the application is ready to accept traffic.
      Verifies critical dependencies (database) are available.

      Use this for Kubernetes readiness probes. If this fails, K8s will stop
      sending traffic to this pod until it becomes ready again.

      **Returns 200 if ready (database healthy), 503 if not ready.**
    responses:
      200:
        description: Application is ready to accept traffic
        schema:
          type: object
          properties:
            status:
              type: string
              enum: [ready]
            checks:
              type: object
              properties:
                database:
                  type: object
                  properties:
                    status:
                      type: string
                    message:
                      type: string
                cache:
                  type: object
                  properties:
                    status:
                      type: string
                    message:
                      type: string
            application:
              type: object
              properties:
                name:
                  type: string
                version:
                  type: string
            timestamp:
              type: string
              format: date-time
      503:
        description: Application is not ready (critical dependency unavailable)
        schema:
          type: object
          properties:
            status:
              type: string
              enum: [not_ready]
    """
    logger.debug("GET /api/health/ready")

    # Check critical dependencies
    db_status = check_database()
    cache_status = check_cache()
    app_info = get_application_info()

    # App is ready if database is healthy
    # Cache is optional - app can function without it
    is_ready = db_status['status'] == 'healthy'

    response = {
        'status': 'ready' if is_ready else 'not_ready',
        'checks': {
            'database': db_status,
            'cache': cache_status
        },
        'application': {
            'name': app_info['name'],
            'version': app_info['version']
        },
        'timestamp': app_info['timestamp']
    }

    status_code = 200 if is_ready else 503

    return jsonify(response), status_code


@health_bp.route('/health/in-flight', methods=['GET'])
def in_flight_check():
    """
    Pipeline-in-flight check endpoint.
    ---
    tags:
      - Health
    summary: Is any pipeline run currently in flight?
    description: |
      Returns whether any pipeline_runs row is currently in 'pending' or 'running' state.
      Used by the installer's pre-update sanity check to avoid killing in-progress runs.

      Returns 200 with `{"in_flight": true|false}` on success.
      Returns 503 with `{"status": "error", ...}` if the DB is unreachable —
      callers should treat 503 as "unable to determine; do NOT proceed without --force".
    responses:
      200:
        description: Status determined successfully
        schema:
          type: object
          properties:
            in_flight:
              type: boolean
              description: True if any pipeline_runs row is in 'pending' or 'running' state.
      503:
        description: Could not determine state (DB unreachable, query failed, etc.)
    """
    logger.debug("GET /api/health/in-flight")
    result = check_pipeline_in_flight()
    if result['status'] == 'ok':
        return jsonify({"in_flight": result['in_flight']}), 200
    return jsonify({"status": "error", "message": result['message']}), 503


@health_bp.route('/health/logs', methods=['GET'])
def log_file_health():
    """
    Log file health check endpoint.
    ---
    tags:
      - Health
    summary: Log file system health check
    description: |
      Checks the health of the log file system including:
      - Log directories exist and are writable
      - Log files exist and are writable
      - Log file sizes and rotation status
      - Total log storage usage

      Returns 200 if all log files are healthy, 503 if critical issues found.

      **Use this endpoint to monitor log file health and diagnose logging issues.**
    responses:
      200:
        description: Log file system is healthy
        schema:
          type: object
          properties:
            status:
              type: string
              enum: [healthy, degraded]
            message:
              type: string
            enabled:
              type: boolean
            files:
              type: object
              description: Status of each log file
            directories:
              type: object
              description: Status of log directories
            total_size_mb:
              type: number
            issues:
              type: array
              items:
                type: string
      503:
        description: Log file system is unhealthy
        schema:
          type: object
          properties:
            status:
              type: string
              enum: [unhealthy, disabled]
            message:
              type: string
    """
    logger.debug("GET /api/health/logs")

    log_status = check_log_files()
    app_info = get_application_info()

    response = {
        'logs': log_status,
        'timestamp': app_info['timestamp']
    }

    # Return appropriate status code
    if log_status['status'] == 'healthy':
        status_code = 200
    elif log_status['status'] == 'disabled':
        status_code = 200  # Disabled is OK, not an error
    elif log_status['status'] == 'degraded':
        status_code = 200  # Degraded means partial issues, still operational
    else:
        status_code = 503  # Unhealthy

    return jsonify(response), status_code


@health_bp.route('/health/about', methods=['GET'])
@jwt_required()
@handle_errors
def system_about():
    """
    Extended system information for the Settings > About tab.
    ---
    tags:
      - Health
    summary: Detailed system information
    responses:
      200:
        description: System stats
    """
    import platform

    result = {}

    # --- Asset stats ---
    try:
        db = current_app.db_adapter
        rows = db.execute_query("SELECT count(*) AS cnt FROM assets")
        asset_count = rows[0]['cnt'] if rows else 0

        last_run_rows = db.execute_query(
            "SELECT started_at FROM pipeline_runs "
            "ORDER BY started_at DESC LIMIT 1"
        )
        last_run = last_run_rows[0]['started_at'].isoformat() if last_run_rows else None

        source_rows = db.execute_query(
            "SELECT count(*) AS cnt FROM integrations"
        )
        source_count = source_rows[0]['cnt'] if source_rows else 0

        result['assets'] = {
            'total': asset_count,
            'last_pipeline_run': last_run,
            'data_sources': source_count,
        }
    except Exception as e:
        logger.debug(f"About: asset stats failed: {e}")
        result['assets'] = None

    # --- Database size ---
    try:
        db = current_app.db_adapter
        size_rows = db.execute_query(
            "SELECT pg_size_pretty(pg_database_size(current_database())) AS size"
        )
        db_size = size_rows[0]['size'] if size_rows else 'unknown'

        table_rows = db.execute_query(
            "SELECT relname AS name, "
            "       n_live_tup AS rows "
            "FROM pg_stat_user_tables "
            "ORDER BY n_live_tup DESC LIMIT 8"
        )
        top_tables = [{'name': r['name'], 'rows': r['rows']} for r in (table_rows or [])]

        result['database'] = {
            'size': db_size,
            'top_tables': top_tables,
        }
    except Exception as e:
        logger.debug(f"About: database stats failed: {e}")
        result['database'] = None

    # --- Python runtime ---
    result['runtime'] = {
        'python_version': platform.python_version(),
        'platform': platform.platform(),
    }

    # --- Redis memory ---
    try:
        import redis as redis_lib
        redis_url = os.getenv('REDIS_URL')
        if redis_url:
            r = redis_lib.from_url(redis_url)
            info = r.info('memory')
            result['redis'] = {
                'used_memory_human': info.get('used_memory_human', 'unknown'),
                'peak_memory_human': info.get('used_memory_peak_human', 'unknown'),
                'connected_clients': r.info('clients').get('connected_clients', 0),
            }
        else:
            result['redis'] = None
    except Exception as e:
        logger.debug(f"About: redis stats failed: {e}")
        result['redis'] = None

    # --- Integrations ---
    try:
        db = current_app.db_adapter
        int_rows = db.execute_query(
            "SELECT count(*) AS total, "
            "       count(*) FILTER (WHERE schedule_enabled = true) AS active "
            "FROM integrations"
        )
        next_rows = db.execute_query(
            "SELECT name, next_run_at FROM integrations "
            "WHERE schedule_enabled = true AND next_run_at IS NOT NULL "
            "ORDER BY next_run_at ASC LIMIT 1"
        )
        int_data = int_rows[0] if int_rows else {}
        next_data = next_rows[0] if next_rows else None
        result['integrations'] = {
            'total': int_data.get('total', 0),
            'active': int_data.get('active', 0),
            'next_sync': {
                'name': next_data['name'],
                'at': next_data['next_run_at'].isoformat()
            } if next_data and next_data.get('next_run_at') else None,
        }
    except Exception as e:
        logger.debug(f"About: integration stats failed: {e}")
        result['integrations'] = None

    # --- Log storage ---
    try:
        log_dir = Path(current_app.config.get('LOG_DIRECTORY', 'logs'))
        if log_dir.exists():
            total_bytes = sum(f.stat().st_size for f in log_dir.rglob('*') if f.is_file())
            file_count = sum(1 for f in log_dir.rglob('*') if f.is_file())
            if total_bytes >= 1_048_576:
                size_str = f"{total_bytes / 1_048_576:.1f} MB"
            else:
                size_str = f"{total_bytes / 1024:.0f} KB"
            result['logs'] = {
                'total_size': size_str,
                'file_count': file_count,
            }
        else:
            result['logs'] = None
    except Exception as e:
        logger.debug(f"About: log stats failed: {e}")
        result['logs'] = None

    return jsonify(result), 200


_UPDATE_CHECK_CACHE_KEY = 'update_check:latest'


def _update_check_state(force=False):
    """Compute the About-page update-check payload.

    Never raises for network reasons (fetch_latest_version swallows errors to
    None). Gated by system.enable_update_check: while off, makes NO outbound
    call — and `force` does not bypass that master switch (air-gap stays
    silent), only the cache TTL. While on, the fetch result is cached for
    _CACHE_TTL_SECONDS so callers share it: with Redis that is one fetch per
    TTL process-wide; if the cache has fallen back to in-memory (Redis
    unavailable at startup) it is one fetch per TTL per worker.
    """
    current = current_app.config.get('APP_VERSION', '0.0.0-dev')
    enabled = bool(current_app.settings_manager.get(
        'system', 'enable_update_check', False))
    if not enabled:
        return {
            'enabled': False, 'current_version': current,
            'latest_version': None, 'update_available': False,
            'checked_at': None,
        }

    cached = None if force else cache.get(_UPDATE_CHECK_CACHE_KEY)
    if cached is not None:
        latest = cached.get('latest_version')
        checked_at = cached.get('checked_at')
    else:
        # The installed version rides in the URL path (distribution census);
        # a non-release version ('0.0.0-dev') falls back to plain /latest.json.
        latest = fetch_latest_version(manifest_url_for(current))
        checked_at = datetime.utcnow().isoformat() + 'Z'
        # Cache the attempt (success OR failure) so a poll storm — or an
        # enabled-but-unreachable box — makes at most one fetch per TTL.
        cache.set(
            _UPDATE_CHECK_CACHE_KEY,
            {'latest_version': latest, 'checked_at': checked_at},
            timeout=_CACHE_TTL_SECONDS,
        )

    return {
        'enabled': True, 'current_version': current,
        'latest_version': latest,
        'update_available': is_newer(latest, current),
        'checked_at': checked_at,
    }


@health_bp.route('/health/update-check', methods=['GET'])
@jwt_required()
@handle_errors
def get_update_check():
    """Cached "is a newer release available?" for the About tab.
    ---
    tags:
      - Health
    summary: Latest-version check (cached; gated by system.enable_update_check)
    responses:
      200:
        description: Update-check state
    """
    return jsonify(_update_check_state(force=False)), 200


@health_bp.route('/health/update-check', methods=['POST'])
@jwt_required()
@require_admin()
@handle_errors
def force_update_check():
    """Admin-only "Check now": bypass the cache and re-fetch once.
    ---
    tags:
      - Health
    summary: Force a fresh latest-version check (admin only)
    responses:
      200:
        description: Fresh update-check state
    """
    return jsonify(_update_check_state(force=True)), 200
