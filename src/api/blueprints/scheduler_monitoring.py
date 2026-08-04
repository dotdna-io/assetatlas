# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Scheduler Monitoring Blueprint

Provides health check and monitoring endpoints for the robust scheduler service.
Includes metrics, status, job history, and alerting capabilities.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, verify_jwt_in_request

from src.auth.middleware.rbac import require_admin

# Create a helper function to get database connection
def get_db_connection():
    """
    Get a database connection from the application's connection manager.

    Reuses the existing connection pool instead of creating a new one.
    """
    from flask import current_app
    if not hasattr(current_app, 'connection_manager'):
        raise RuntimeError("Connection manager not initialized in application context")
    return current_app.connection_manager.get_connection()

logger = logging.getLogger(__name__)

# Create blueprint
scheduler_monitoring_bp = Blueprint('scheduler_monitoring', __name__)


class SchedulerMonitor:
    """Service for monitoring scheduler health and performance."""

    @staticmethod
    def get_scheduler_health(scheduler_service) -> Dict[str, Any]:
        """Get comprehensive scheduler health status."""
        try:
            return scheduler_service.get_health_status()
        except Exception as e:
            logger.error(f"Failed to get scheduler health: {e}")
            return {
                'status': 'error',
                'error': str(e),
                'running': False
            }

    @staticmethod
    def get_job_execution_history(job_id: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Get job execution history from database."""
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                if job_id:
                    query = """
                        SELECT
                            id, job_id, job_name, execution_id,
                            started_at, completed_at, duration_seconds,
                            status, records_processed, error_message,
                            message, attempt_number
                        FROM job_executions
                        WHERE job_id = %s
                        ORDER BY started_at DESC
                        LIMIT %s
                    """
                    cursor.execute(query, (job_id, limit))
                else:
                    query = """
                        SELECT
                            id, job_id, job_name, execution_id,
                            started_at, completed_at, duration_seconds,
                            status, records_processed, error_message,
                            message, attempt_number
                        FROM job_executions
                        ORDER BY started_at DESC
                        LIMIT %s
                    """
                    cursor.execute(query, (limit,))

                columns = [desc[0] for desc in cursor.description]
                results = []
                for row in cursor.fetchall():
                    record = dict(zip(columns, row))
                    # Convert datetime objects to ISO format
                    for key in ['started_at', 'completed_at']:
                        if record.get(key):
                            record[key] = record[key].isoformat()
                    # Convert UUID to string
                    for key in ['id', 'execution_id']:
                        if record.get(key):
                            record[key] = str(record[key])
                    results.append(record)

                return results

    @staticmethod
    def get_job_statistics(job_id: str = None) -> Dict[str, Any]:
        """Get aggregated job statistics."""
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                if job_id:
                    query = """
                        SELECT * FROM job_execution_stats
                        WHERE job_id = %s
                    """
                    cursor.execute(query, (job_id,))
                else:
                    query = """
                        SELECT * FROM job_execution_stats
                        ORDER BY last_execution_at DESC
                    """
                    cursor.execute(query)

                columns = [desc[0] for desc in cursor.description]
                results = []
                for row in cursor.fetchall():
                    record = dict(zip(columns, row))
                    # Convert datetime objects
                    for key in ['last_execution_at', 'last_success_at', 'last_failure_at']:
                        if record.get(key):
                            record[key] = record[key].isoformat()
                    # Convert Decimal to float
                    for key in ['avg_duration_seconds', 'min_duration_seconds',
                               'max_duration_seconds', 'success_rate']:
                        if record.get(key):
                            record[key] = float(record[key])
                    results.append(record)

                return {'statistics': results} if not job_id else results[0] if results else {}

    @staticmethod
    def get_stuck_jobs() -> List[Dict[str, Any]]:
        """Get jobs that appear to be stuck."""
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                query = """
                    SELECT
                        job_id, job_name, execution_id,
                        started_at, status,
                        EXTRACT(EPOCH FROM (NOW() - started_at)) as seconds_running
                    FROM job_executions
                    WHERE status = 'running'
                    AND started_at < NOW() - INTERVAL '1 hour'
                    ORDER BY started_at
                """
                cursor.execute(query)

                columns = [desc[0] for desc in cursor.description]
                results = []
                for row in cursor.fetchall():
                    record = dict(zip(columns, row))
                    record['started_at'] = record['started_at'].isoformat()
                    record['execution_id'] = str(record['execution_id'])
                    record['seconds_running'] = float(record['seconds_running'])
                    record['hours_running'] = round(record['seconds_running'] / 3600, 2)
                    results.append(record)

                return results

    @staticmethod
    def get_failed_jobs(hours: int = 24) -> List[Dict[str, Any]]:
        """Get recently failed jobs."""
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                query = """
                    SELECT
                        job_id, job_name, execution_id,
                        started_at, completed_at, duration_seconds,
                        error_message, attempt_number
                    FROM job_executions
                    WHERE status = 'failed'
                    AND started_at > NOW() - INTERVAL '%s hours'
                    ORDER BY started_at DESC
                """
                cursor.execute(query, (hours,))

                columns = [desc[0] for desc in cursor.description]
                results = []
                for row in cursor.fetchall():
                    record = dict(zip(columns, row))
                    for key in ['started_at', 'completed_at']:
                        if record.get(key):
                            record[key] = record[key].isoformat()
                    record['execution_id'] = str(record['execution_id'])
                    if record.get('duration_seconds'):
                        record['duration_seconds'] = float(record['duration_seconds'])
                    results.append(record)

                return results


# Initialize monitor
monitor = SchedulerMonitor()


@scheduler_monitoring_bp.route('/health', methods=['GET'])
def scheduler_health():
    """
    Get scheduler health status.
    Returns minimal status without authentication.
    Returns full health metrics with valid admin JWT.
    """
    try:
        from flask import current_app
        scheduler_service = current_app.config.get('SCHEDULER_SERVICE')

        if not scheduler_service:
            return jsonify({
                'status': 'error',
                'message': 'Scheduler service not initialized'
            }), 503

        health = monitor.get_scheduler_health(scheduler_service)

        # Check if authenticated — return minimal info without auth
        try:
            verify_jwt_in_request()
            is_authenticated = True
        except Exception:
            is_authenticated = False

        if not is_authenticated:
            return jsonify({
                'status': health.get('status', 'unknown'),
                'running': health.get('running', False)
            }), 200

        # Full details for authenticated users
        if health['status'] == 'healthy':
            status_code = 200
        elif health['status'] == 'degraded':
            status_code = 200
        else:
            status_code = 503

        return jsonify(health), status_code

    except Exception as e:
        logger.error(f"Error getting scheduler health: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


@scheduler_monitoring_bp.route('/metrics', methods=['GET'])
@jwt_required()
@require_admin()
def scheduler_metrics():
    """
    Get Prometheus-compatible metrics.

    Returns metrics in Prometheus text format for scraping.
    """
    try:
        from flask import current_app
        scheduler_service = current_app.config.get('SCHEDULER_SERVICE')

        if not scheduler_service:
            return "# Scheduler not initialized\n", 503

        health = monitor.get_scheduler_health(scheduler_service)
        metrics = health.get('metrics', {})

        # Format as Prometheus metrics
        output = []
        output.append('# HELP scheduler_up Whether the scheduler is running')
        output.append('# TYPE scheduler_up gauge')
        output.append(f"scheduler_up {1 if health.get('running') else 0}")

        output.append('# HELP scheduler_jobs_scheduled Number of scheduled jobs')
        output.append('# TYPE scheduler_jobs_scheduled gauge')
        output.append(f"scheduler_jobs_scheduled {metrics.get('jobs_scheduled', 0)}")

        output.append('# HELP scheduler_jobs_executed_total Total executed jobs')
        output.append('# TYPE scheduler_jobs_executed_total counter')
        output.append(f"scheduler_jobs_executed_total {metrics.get('jobs_executed', 0)}")

        output.append('# HELP scheduler_jobs_failed_total Total failed jobs')
        output.append('# TYPE scheduler_jobs_failed_total counter')
        output.append(f"scheduler_jobs_failed_total {metrics.get('jobs_failed', 0)}")

        output.append('# HELP scheduler_jobs_timeout_total Total timeout jobs')
        output.append('# TYPE scheduler_jobs_timeout_total counter')
        output.append(f"scheduler_jobs_timeout_total {metrics.get('jobs_timeout', 0)}")

        output.append('# HELP scheduler_jobs_missed_total Total missed jobs')
        output.append('# TYPE scheduler_jobs_missed_total counter')
        output.append(f"scheduler_jobs_missed_total {metrics.get('jobs_missed', 0)}")

        output.append('# HELP scheduler_failure_rate Job failure rate percentage')
        output.append('# TYPE scheduler_failure_rate gauge')
        output.append(f"scheduler_failure_rate {metrics.get('failure_rate', 0)}")

        output.append('# HELP scheduler_avg_execution_seconds Average job execution time')
        output.append('# TYPE scheduler_avg_execution_seconds gauge')
        output.append(f"scheduler_avg_execution_seconds {metrics.get('avg_execution_time', 0)}")

        output.append('# HELP scheduler_circuit_breaker_open Circuit breaker status')
        output.append('# TYPE scheduler_circuit_breaker_open gauge')
        cb = health.get('circuit_breaker', {})
        output.append(f"scheduler_circuit_breaker_open {1 if cb.get('open') else 0}")

        return '\n'.join(output) + '\n', 200, {'Content-Type': 'text/plain'}

    except Exception as e:
        logger.error(f"Error generating metrics: {e}", exc_info=True)
        return f"# Error: {str(e)}\n", 500


@scheduler_monitoring_bp.route('/history', methods=['GET'])
@jwt_required()
@require_admin()
def job_execution_history():
    """
    Get job execution history.

    Query parameters:
    - job_id: Filter by specific job ID
    - limit: Maximum number of records (default: 100)
    """
    try:
        job_id = request.args.get('job_id')
        limit = min(int(request.args.get('limit', 100)), 1000)

        history = monitor.get_job_execution_history(job_id, limit)

        return jsonify({
            'count': len(history),
            'executions': history
        })

    except Exception as e:
        logger.error(f"Error getting job history: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@scheduler_monitoring_bp.route('/statistics', methods=['GET'])
@jwt_required()
@require_admin()
def job_statistics():
    """
    Get aggregated job statistics.

    Query parameters:
    - job_id: Get statistics for specific job
    """
    try:
        job_id = request.args.get('job_id')
        stats = monitor.get_job_statistics(job_id)

        return jsonify(stats)

    except Exception as e:
        logger.error(f"Error getting job statistics: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@scheduler_monitoring_bp.route('/alerts', methods=['GET'])
@jwt_required()
@require_admin()
def scheduler_alerts():
    """
    Get current scheduler alerts.

    Returns stuck jobs, recent failures, and health warnings.
    """
    try:
        alerts = {
            'timestamp': datetime.utcnow().isoformat(),
            'stuck_jobs': [],
            'failed_jobs': [],
            'warnings': []
        }

        # Get stuck jobs
        stuck = monitor.get_stuck_jobs()
        if stuck:
            alerts['stuck_jobs'] = stuck
            alerts['warnings'].append({
                'level': 'critical',
                'message': f"{len(stuck)} jobs appear to be stuck",
                'jobs': [j['job_id'] for j in stuck]
            })

        # Get recent failures
        failed = monitor.get_failed_jobs(hours=24)
        if failed:
            alerts['failed_jobs'] = failed

            # Group failures by job
            failures_by_job = {}
            for failure in failed:
                job_id = failure['job_id']
                if job_id not in failures_by_job:
                    failures_by_job[job_id] = []
                failures_by_job[job_id].append(failure)

            # Create warnings for repeated failures
            for job_id, failures in failures_by_job.items():
                if len(failures) >= 3:
                    alerts['warnings'].append({
                        'level': 'warning' if len(failures) < 5 else 'critical',
                        'message': f"Job {job_id} has failed {len(failures)} times in last 24 hours",
                        'job_id': job_id,
                        'failure_count': len(failures)
                    })

        # Check scheduler health
        from flask import current_app
        scheduler_service = current_app.config.get('SCHEDULER_SERVICE')
        if scheduler_service:
            health = monitor.get_scheduler_health(scheduler_service)
            if health['status'] != 'healthy':
                alerts['warnings'].append({
                    'level': 'critical',
                    'message': f"Scheduler status is {health['status']}",
                    'details': health
                })

        alerts['alert_count'] = len(alerts['warnings'])
        alerts['has_critical'] = any(w['level'] == 'critical' for w in alerts['warnings'])

        return jsonify(alerts)

    except Exception as e:
        logger.error(f"Error getting scheduler alerts: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@scheduler_monitoring_bp.route('/jobs/<job_id>/health', methods=['GET'])
@jwt_required()
@require_admin()
def job_health(job_id: str):
    """
    Get health status for a specific job.

    Returns detailed health information including success rate,
    recent failures, and performance metrics.
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM get_job_health_status(%s)", (job_id,))
                result = cursor.fetchone()

                if result:
                    columns = [desc[0] for desc in cursor.description]
                    health = dict(zip(columns, result))

                    # Convert timestamps
                    for key in ['last_success', 'last_failure']:
                        if health.get(key):
                            health[key] = health[key].isoformat()

                    # Get recent executions
                    recent = monitor.get_job_execution_history(job_id, limit=10)
                    health['recent_executions'] = recent

                    return jsonify(health)
                else:
                    return jsonify({'error': 'Job not found'}), 404


    except Exception as e:
        logger.error(f"Error getting job health: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@scheduler_monitoring_bp.route('/refresh-stats', methods=['POST'])
@jwt_required()
@require_admin()
def refresh_statistics():
    """
    Manually refresh the job statistics materialized view.

    This is normally done hourly but can be triggered manually.
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY job_execution_stats")
                conn.commit()

                return jsonify({
                    'success': True,
                    'message': 'Statistics refreshed successfully',
                    'timestamp': datetime.utcnow().isoformat()
                })


    except Exception as e:
        logger.error(f"Error refreshing statistics: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500