# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Super Robust Production Scheduler Service

Enterprise-grade scheduler with comprehensive fault tolerance, monitoring,
and recovery mechanisms. Designed for 99.9% uptime with zero job loss.
"""

import logging
import os
import uuid
import time
import threading
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime, timedelta
from functools import wraps
from contextlib import contextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.events import (
    EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, EVENT_JOB_MISSED,
    EVENT_JOB_SUBMITTED, EVENT_JOB_MAX_INSTANCES
)
import pytz

from src.modules.integrations.models import Integration
from src.modules.integrations.repositories import IntegrationRepository
from src.modules.integrations.shared.cron_validator import (
    calculate_next_run,
    expand_preset,
    is_valid_cron
)
from src.modules.integrations.shared.scheduler_signal import (
    DIRTY_SET_KEY,
    DIRTY_SET_TTL_SECONDS,
    signal_schedule_change,
)
from src.api.utils.log_retention import LogRetentionService

logger = logging.getLogger(__name__)


def _is_integration_job_id(job_id) -> bool:
    """True if ``job_id`` is an integration job id.

    Integration jobs are keyed by the integration UUID (``str(integration.id)``);
    the worker's long-lived system jobs (``interval_alert_rules``,
    ``pipeline_watchdog``, ``alert_delivery_drain`` ...) use plain string ids. Only
    UUID-shaped ids are eligible for orphan removal so a system job is never mistaken
    for a deleted integration and deleted out from under the running worker.
    """
    try:
        uuid.UUID(str(job_id))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


class CircuitBreaker:
    """
    Circuit breaker pattern to prevent cascading failures.
    """
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = None
        self._lock = threading.Lock()

    def call(self, func: Callable, *args, **kwargs):
        """Execute function with circuit breaker protection."""
        with self._lock:
            if self.is_open():
                if self.should_attempt_reset():
                    self.failure_count = 0
                    self.last_failure_time = None
                else:
                    raise Exception(f"Circuit breaker is open (failures: {self.failure_count})")

        try:
            result = func(*args, **kwargs)
            with self._lock:
                self.failure_count = 0  # Reset on success
            return result
        except Exception as e:
            with self._lock:
                self.failure_count += 1
                self.last_failure_time = datetime.utcnow()
            raise

    def is_open(self) -> bool:
        """Check if circuit breaker is open."""
        return self.failure_count >= self.failure_threshold

    def should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if not self.last_failure_time:
            return True
        elapsed = (datetime.utcnow() - self.last_failure_time).total_seconds()
        return elapsed >= self.recovery_timeout


class JobExecutor:
    """
    Robust job executor with timeout, retry, and error handling.
    """
    def __init__(self, timeout: int = 3600, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries
        self.circuit_breaker = CircuitBreaker()

    def execute_with_timeout(self, func: Callable, *args, **kwargs):
        """Execute function with timeout protection."""
        result = [None]
        exception = [None]

        def target():
            try:
                result[0] = func(*args, **kwargs)
            except Exception as e:
                exception[0] = e

        thread = threading.Thread(target=target)
        thread.daemon = True
        thread.start()
        thread.join(self.timeout)

        if thread.is_alive():
            # Job is still running after timeout
            logger.error(f"Job execution timed out after {self.timeout} seconds")
            raise TimeoutError(f"Job execution exceeded {self.timeout} seconds")

        if exception[0]:
            raise exception[0]

        return result[0]

    def execute_with_retry(self, func: Callable, *args, **kwargs):
        """Execute function with exponential backoff retry."""
        last_exception = None

        for attempt in range(self.max_retries):
            try:
                return self.circuit_breaker.call(
                    self.execute_with_timeout, func, *args, **kwargs
                )
            except Exception as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff
                    logger.warning(
                        f"Job execution attempt {attempt + 1} failed, "
                        f"retrying in {wait_time} seconds: {e}"
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(f"Job execution failed after {self.max_retries} attempts: {e}")

        raise last_exception


class SchedulerService:
    """
    Production-grade scheduler service with comprehensive fault tolerance.

    Features:
    - Automatic recovery from database on startup
    - Circuit breaker pattern for failure isolation
    - Exponential backoff retry logic
    - Job execution timeouts
    - Comprehensive error handling
    - Health monitoring and metrics
    - Graceful shutdown
    - Job execution history
    - Misfire handling
    - Database connection resilience
    """

    def __init__(self, integration_repository: Optional[IntegrationRepository] = None):
        """Initialize the super robust scheduler."""
        self.repository = integration_repository or IntegrationRepository()
        self.scheduler = None
        self.integration_service = None
        self._running = False
        self._shutting_down = False
        self.job_executor = JobExecutor(
            timeout=int(os.getenv('SCHEDULER_JOB_TIMEOUT', '3600')),  # 1 hour default
            max_retries=int(os.getenv('SCHEDULER_MAX_RETRIES', '3'))
        )

        # Metrics tracking
        self.metrics = {
            'started_at': None,
            'jobs_scheduled': 0,
            'jobs_executed': 0,
            'jobs_failed': 0,
            'jobs_missed': 0,
            'jobs_timeout': 0,
            'last_error': None,
            'last_success': None,
            'total_execution_time': 0,
            'health_status': 'initializing'
        }

        # Health check thread
        self._health_check_thread = None
        self._health_check_interval = int(os.getenv('SCHEDULER_HEALTH_CHECK_INTERVAL', '60'))

        # Redis client for cross-process status sharing (optional)
        self._redis_client = None

        # Pipeline service for event-driven schedule re-sync (optional)
        self._pipeline_service = None

        # Database references for maintenance jobs that need DB access (optional)
        self._connection_manager = None
        self._db_adapter = None

    def set_db_references(self, connection_manager, db_adapter):
        """Set database references for maintenance jobs that need DB access."""
        self._connection_manager = connection_manager
        self._db_adapter = db_adapter

    def set_redis_client(self, redis_client):
        """Set Redis client for cross-process status sharing."""
        self._redis_client = redis_client

    def set_pipeline_service(self, pipeline_service):
        """Set pipeline service for event-driven schedule re-sync."""
        self._pipeline_service = pipeline_service

    def initialize(self, integration_service):
        """
        Initialize the scheduler with comprehensive configuration.

        Args:
            integration_service: IntegrationService instance for executing integrations
        """
        self.integration_service = integration_service

        # Configure job stores
        jobstores = {
            'default': MemoryJobStore()
        }

        # Configure executors with optimal thread pool
        max_workers = int(os.getenv('SCHEDULER_MAX_WORKERS', '5'))
        executors = {
            'default': ThreadPoolExecutor(max_workers=max_workers),
            'monitoring': ThreadPoolExecutor(max_workers=1)  # Dedicated for health checks
        }

        # Robust job defaults
        job_defaults = {
            'coalesce': True,  # Combine missed runs
            'max_instances': 1,  # Only one instance per job
            'misfire_grace_time': int(os.getenv('SCHEDULER_MISFIRE_GRACE', '900'))  # 15 minutes
        }

        # Create scheduler
        self.scheduler = BackgroundScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults,
            timezone=pytz.UTC
        )

        # Add comprehensive event listeners
        self._setup_event_listeners()

        self.metrics['health_status'] = 'initialized'
        logger.info("SchedulerService initialized")

    def _setup_event_listeners(self):
        """Set up comprehensive event monitoring."""
        self.scheduler.add_listener(
            self._job_executed_listener,
            EVENT_JOB_EXECUTED
        )
        self.scheduler.add_listener(
            self._job_error_listener,
            EVENT_JOB_ERROR
        )
        self.scheduler.add_listener(
            self._job_missed_listener,
            EVENT_JOB_MISSED
        )
        self.scheduler.add_listener(
            self._job_submitted_listener,
            EVENT_JOB_SUBMITTED
        )
        self.scheduler.add_listener(
            self._job_max_instances_listener,
            EVENT_JOB_MAX_INSTANCES
        )

    def start(self):
        """
        Start the scheduler with automatic recovery and health monitoring.
        """
        if not self.scheduler:
            raise RuntimeError("Scheduler not initialized. Call initialize() first.")

        if self._running:
            logger.warning("Scheduler is already running")
            return

        try:
            # Start the scheduler
            self.scheduler.start()
            self._running = True
            self.metrics['started_at'] = datetime.utcnow()
            self.metrics['health_status'] = 'healthy'

            # Recover all schedules from database
            recovered = self._recover_all_schedules()

            # Schedule maintenance jobs (log retention, etc.)
            self._schedule_maintenance_jobs()

            # Start health monitoring
            self._start_health_monitoring()

            logger.info(
                f"SchedulerService started successfully. "
                f"Recovered {recovered} jobs from database"
            )

        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}", exc_info=True)
            self.metrics['health_status'] = 'error'
            raise

    def _recover_all_schedules(self) -> int:
        """
        Recover all schedules from database with resilience.

        Returns:
            Number of successfully recovered schedules
        """
        logger.info("Starting schedule recovery from database...")

        if self.integration_service is not None:
            try:
                self.integration_service.cleanup_stale_running_flags()
            except Exception as e:
                logger.error(f"Failed to clear stale is_running flags: {e}")

        recovered_count = 0
        failed_count = 0

        try:
            # Retry database connection if needed
            max_db_retries = 3
            for attempt in range(max_db_retries):
                try:
                    integrations = self.repository.list_all(decrypt=False)
                    break
                except Exception as e:
                    if attempt < max_db_retries - 1:
                        logger.warning(f"Database connection attempt {attempt + 1} failed, retrying...")
                        time.sleep(2 ** attempt)
                    else:
                        logger.error("Failed to connect to database for schedule recovery")
                        raise

            for integration in integrations:
                if (
                    integration.is_enabled()
                    and integration.schedule_enabled
                    and integration.schedule_cron
                ):
                    try:
                        self._schedule_integration_job(integration, recovery_mode=True)
                        recovered_count += 1
                        logger.debug(f"Recovered schedule for integration: {integration.name}")
                    except Exception as e:
                        failed_count += 1
                        logger.error(
                            f"Failed to recover schedule for integration {integration.name}: {e}"
                        )

            if failed_count > 0:
                logger.warning(
                    f"Schedule recovery completed with {failed_count} failures "
                    f"out of {recovered_count + failed_count} total"
                )

            self.metrics['jobs_scheduled'] = recovered_count
            return recovered_count

        except Exception as e:
            logger.error(f"Critical error during schedule recovery: {e}", exc_info=True)
            self.metrics['health_status'] = 'degraded'
            return recovered_count

    def resync_dirty_schedules(self):
        """Drain Redis dirty signals; SPOP per element so a crash mid-loop
        doesn't lose unprocessed ids."""
        if not self._redis_client:
            return

        max_per_tick = 100

        for _ in range(max_per_tick):
            try:
                raw_id = self._redis_client.spop(DIRTY_SET_KEY)
            except Exception as e:
                logger.debug(f"Error popping dirty schedule: {e}")
                return
            if raw_id is None:
                return

            integration_id = raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id)
            try:
                integration = self.repository.get_by_id(integration_id, decrypt=False)
                if not integration:
                    # Deleted — remove job if present
                    if self.scheduler.get_job(integration_id):
                        self.scheduler.remove_job(integration_id)
                    continue

                if (
                    integration.is_enabled()
                    and integration.schedule_enabled
                    and integration.schedule_cron
                ):
                    self._schedule_integration_job(integration, recovery_mode=True)
                    logger.info(f"Resynced schedule for {integration.name} (API signal)")
                else:
                    if self.scheduler.get_job(str(integration.id)):
                        self.scheduler.remove_job(str(integration.id))
                        logger.info(f"Removed schedule for {integration.name} (API signal)")
            except Exception as e:
                logger.error(f"Failed to resync dirty schedule {integration_id}: {e}")
                try:
                    self._redis_client.sadd(DIRTY_SET_KEY, integration_id)
                    self._redis_client.expire(DIRTY_SET_KEY, DIRTY_SET_TTL_SECONDS)
                except Exception as readd_err:
                    logger.debug(f"Failed to re-queue dirty signal: {readd_err}")

    def resync_schedules(self):
        """
        Full re-sync of integration schedules from DB. Called every 2 minutes
        as a fallback in case Redis signals are missed.
        """
        # Also process any pending dirty signals
        self.resync_dirty_schedules()

        try:
            integrations = self.repository.list_all(decrypt=False)
        except Exception as e:
            logger.error(f"Failed to read integrations for schedule resync: {e}")
            return

        current_job_ids = {
            j.id for j in self.scheduler.get_jobs()
            if j.id not in (
                'pipeline_schedule_resync', 'schedule_resync',
                'dirty_schedule_check',
                'pipeline_auto_run', 'maintenance_log_retention',
                'maintenance_data_retention',
            )
        }

        for integration in integrations:
            job_id = str(integration.id)

            should_be_scheduled = (
                integration.is_enabled()
                and integration.schedule_enabled
                and integration.schedule_cron
            )

            if should_be_scheduled:
                existing_job = self.scheduler.get_job(job_id)
                if existing_job:
                    # Check if cron changed
                    cron_expr = expand_preset(integration.schedule_cron)
                    try:
                        tz = pytz.timezone(integration.schedule_timezone)
                    except pytz.UnknownTimeZoneError:
                        tz = pytz.UTC
                    new_trigger = CronTrigger.from_crontab(cron_expr, timezone=tz)
                    old_fields = {f.name: str(f) for f in existing_job.trigger.fields}
                    new_fields = {f.name: str(f) for f in new_trigger.fields}
                    if old_fields == new_fields:
                        current_job_ids.discard(job_id)
                        continue

                # Schedule changed or new — update it
                try:
                    self._schedule_integration_job(integration, recovery_mode=True)
                    logger.info(f"Resynced schedule for {integration.name}")
                except Exception as e:
                    logger.error(f"Failed to resync schedule for {integration.name}: {e}")
                current_job_ids.discard(job_id)

            else:
                if self.scheduler.get_job(job_id):
                    self.scheduler.remove_job(job_id)
                    logger.info(f"Removed schedule for {integration.name}")
                current_job_ids.discard(job_id)

        # Remove jobs for deleted integrations. Only integration jobs (UUID ids) are
        # eligible: the worker re-registers its system jobs on this scheduler every
        # startup, and deleting one here silently disables it (e.g. interval alert
        # evaluation, alert delivery) until the next restart.
        integration_ids = {str(i.id) for i in integrations}
        for orphan_id in current_job_ids:
            if orphan_id in integration_ids or not _is_integration_job_id(orphan_id):
                continue
            self.scheduler.remove_job(orphan_id)
            logger.info(f"Removed orphaned job {orphan_id}")

    def _schedule_maintenance_jobs(self):
        """
        Schedule built-in maintenance jobs like log retention cleanup.
        These jobs run independently of user-configured integration schedules.
        """
        logger.info("Scheduling maintenance jobs...")

        # Schedule log retention cleanup - runs daily at 3:00 AM UTC
        try:
            log_retention_cron = os.getenv('LOG_RETENTION_CRON', '0 3 * * *')
            log_retention_enabled = os.getenv('LOG_RETENTION_ENABLED', 'true').lower() == 'true'

            if log_retention_enabled:
                self.scheduler.add_job(
                    func=self._run_log_retention_cleanup,
                    trigger=CronTrigger.from_crontab(log_retention_cron, timezone=pytz.UTC),
                    id='maintenance_log_retention',
                    name='Log Retention Cleanup',
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True,
                    misfire_grace_time=3600  # 1 hour grace period
                )
                logger.info(
                    f"Scheduled log retention cleanup job with cron: {log_retention_cron}"
                )
            else:
                logger.info("Log retention cleanup is disabled via LOG_RETENTION_ENABLED=false")

        except Exception as e:
            logger.error(f"Failed to schedule log retention job: {e}", exc_info=True)

        # Schedule data retention cleanup - runs daily at 4:00 AM UTC
        try:
            data_retention_cron = os.getenv('DATA_RETENTION_CRON', '0 4 * * *')
            data_retention_enabled = os.getenv('DATA_RETENTION_ENABLED', 'true').lower() == 'true'

            if data_retention_enabled:
                self.scheduler.add_job(
                    func=self._run_data_retention_cleanup,
                    trigger=CronTrigger.from_crontab(data_retention_cron, timezone=pytz.UTC),
                    id='maintenance_data_retention',
                    name='Data Retention Cleanup',
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True,
                    misfire_grace_time=3600
                )
                logger.info(
                    f"Scheduled data retention cleanup job with cron: {data_retention_cron}"
                )
            else:
                logger.info("Data retention cleanup is disabled via DATA_RETENTION_ENABLED=false")

        except Exception as e:
            logger.error(f"Failed to schedule data retention job: {e}", exc_info=True)

        try:
            self.scheduler.add_job(
                func=self._run_wizard_temp_cleanup,
                trigger=CronTrigger.from_crontab('17 * * * *', timezone=pytz.UTC),
                id='maintenance_wizard_temp_cleanup',
                name='Wizard Temp Integration Cleanup',
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=3600,
            )
            logger.info("Scheduled wizard temp-integration cleanup (hourly at :17)")
        except Exception as e:
            logger.error(f"Failed to schedule wizard temp cleanup job: {e}", exc_info=True)

    def _run_wizard_temp_cleanup(self):
        """Sweep orphan ``__test_*`` integrations older than 1 hour."""
        try:
            deleted = self.integration_service.cleanup_wizard_temp_integrations()
            if deleted:
                logger.info(f"Wizard temp cleanup: removed {deleted} orphan(s)")
        except Exception as e:
            logger.error(f"Wizard temp cleanup failed: {e}", exc_info=True)

    def _run_log_retention_cleanup(self):
        """
        Execute log retention cleanup tasks.
        Archives old logs and removes expired archives.
        """
        logger.info("Starting scheduled log retention cleanup...")
        start_time = datetime.utcnow()

        try:
            log_dir = os.getenv('LOG_DIR', 'logs')
            retention_service = LogRetentionService(log_dir=log_dir)

            # Archive old rotated logs
            archived_count, archived_files = retention_service.archive_old_logs()
            if archived_count > 0:
                logger.info(f"Archived {archived_count} log files: {archived_files}")

            # Clean up expired logs (audit, app, and archives)
            # Returns (audit_removed, app_removed) but also cleans archives internally
            audit_deleted, app_deleted = retention_service.cleanup_old_logs()
            total_deleted = audit_deleted + app_deleted
            if total_deleted > 0:
                logger.info(
                    f"Deleted {total_deleted} expired logs "
                    f"(audit: {audit_deleted}, app: {app_deleted})"
                )

            duration = (datetime.utcnow() - start_time).total_seconds()
            logger.info(
                f"Log retention cleanup completed in {duration:.2f}s. "
                f"Archived: {archived_count}, Deleted: {total_deleted}"
            )

            # Update metrics
            self.metrics.setdefault('maintenance_jobs', {})
            self.metrics['maintenance_jobs']['log_retention'] = {
                'last_run': datetime.utcnow().isoformat(),
                'duration_seconds': duration,
                'archived_count': archived_count,
                'deleted_count': total_deleted,
                'status': 'success'
            }

        except Exception as e:
            logger.error(f"Log retention cleanup failed: {e}", exc_info=True)
            self.metrics.setdefault('maintenance_jobs', {})
            self.metrics['maintenance_jobs']['log_retention'] = {
                'last_run': datetime.utcnow().isoformat(),
                'status': 'failed',
                'error': str(e)
            }

    def _run_data_retention_cleanup(self):
        """
        Execute data retention cleanup for database tables.
        Deletes rows older than configured retention periods.
        """
        logger.info("Starting scheduled data retention cleanup...")
        start_time = datetime.utcnow()

        try:
            if not self._connection_manager or not self._db_adapter:
                raise RuntimeError(
                    "Database references not set on SchedulerService. "
                    "Call set_db_references() before scheduling data retention jobs."
                )

            from src.modules.data_retention import DataRetentionService
            from src.api.modules.settings.settings_manager import SettingsManager

            settings_manager = SettingsManager(self._connection_manager)
            service = DataRetentionService(
                db_adapter=self._db_adapter,
                settings_manager=settings_manager
            )
            summary = service.run_cleanup()

            duration = (datetime.utcnow() - start_time).total_seconds()
            total_deleted = sum(v for v in summary.values() if v > 0)

            logger.info(
                f"Data retention cleanup completed in {duration:.2f}s. "
                f"Total deleted: {total_deleted}, Details: {summary}"
            )

            self.metrics.setdefault('maintenance_jobs', {})
            self.metrics['maintenance_jobs']['data_retention'] = {
                'last_run': datetime.utcnow().isoformat(),
                'duration_seconds': duration,
                'total_deleted': total_deleted,
                'details': {k: v for k, v in summary.items()},
                'status': 'success'
            }

        except Exception as e:
            logger.error(f"Data retention cleanup failed: {e}", exc_info=True)
            self.metrics.setdefault('maintenance_jobs', {})
            self.metrics['maintenance_jobs']['data_retention'] = {
                'last_run': datetime.utcnow().isoformat(),
                'status': 'failed',
                'error': str(e)
            }

    @staticmethod
    def _should_persist_next_run(stored: Optional[datetime], recovery_mode: bool) -> bool:
        """In recovery mode, only persist if stored value is missing or stale.

        Avoids clobbering a fresh ``next_run_at`` written by an in-flight
        run that just completed, while still healing legacy/leftover
        past values on worker startup.
        """
        if not recovery_mode:
            return True
        if stored is None:
            return True
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=pytz.UTC)
        return stored < datetime.now(pytz.UTC)

    def _schedule_integration_job(self, integration: Integration, recovery_mode: bool = False):
        """Schedule (or replace) the APScheduler job for an integration.

        ``recovery_mode`` is True when called from startup, full resync,
        or dirty-signal pickup; it tells _should_persist_next_run to
        preserve fresh stored values.
        """
        job_id = str(integration.id)

        try:
            # Expand preset if needed
            cron_expr = expand_preset(integration.schedule_cron)

            # Get timezone with validation
            try:
                tz = pytz.timezone(integration.schedule_timezone)
            except pytz.UnknownTimeZoneError:
                logger.error(f"Invalid timezone {integration.schedule_timezone}, using UTC")
                tz = pytz.UTC

            # Create cron trigger
            trigger = CronTrigger.from_crontab(cron_expr, timezone=tz)

            # Remove existing job if present
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
                logger.debug(f"Removed existing job for integration {integration.name}")

            # Add job with wrapper for robustness
            self.scheduler.add_job(
                func=self._execute_integration_robust,
                trigger=trigger,
                id=job_id,
                name=f"Integration: {integration.name}",
                args=[integration.id, integration.name],
                replace_existing=True,
                max_instances=1,
                coalesce=True
            )

            next_run = calculate_next_run(integration.schedule_cron, integration.schedule_timezone)
            if next_run and self._should_persist_next_run(
                integration.next_run_at, recovery_mode
            ):
                self.repository.update(integration.id, {'next_run_at': next_run})

            logger.info(f"Successfully scheduled integration: {integration.name} ({job_id})")

        except Exception as e:
            logger.error(f"Failed to schedule integration {integration.name}: {e}", exc_info=True)
            raise

    def _execute_integration_robust(self, integration_id: uuid.UUID, integration_name: str):
        """Execute an integration with timeout, retry, and metrics.

        ``is_running`` is owned by ``run_integration``'s own finally;
        stale flags from crashed processes are cleared at startup by
        ``cleanup_stale_running_flags``. The outer ``finally`` here only
        advances ``next_run_at`` so a single skip/failure can't leave
        the UI showing "Overdue" forever.
        """
        start_time = datetime.utcnow()
        execution_id = str(uuid.uuid4())

        logger.info(f"Starting robust execution of integration {integration_name} (exec_id: {execution_id})")

        if self._shutting_down:
            logger.warning(f"Skipping integration {integration_name} due to shutdown in progress")
            return

        integration = self._get_integration_safe(integration_id)
        if not integration:
            logger.error(f"Integration {integration_id} not found at scheduled time; skipping")
            self.metrics['jobs_failed'] += 1
            self.metrics['last_error'] = f"Integration {integration_id} not found"
            return

        try:
            skip_reason = self._skip_reason(integration)
            if skip_reason:
                logger.warning(f"Integration {integration_name} skipped: {skip_reason}")
                self._log_job_execution(
                    job_id=str(integration_id),
                    execution_id=execution_id,
                    job_name=integration_name,
                    started_at=start_time,
                    completed_at=datetime.utcnow(),
                    status='skipped',
                    message=skip_reason,
                )
                return

            try:
                result = self.job_executor.execute_with_retry(
                    self.integration_service.run_integration,
                    integration_id
                )
            except TimeoutError as e:
                self.metrics['jobs_timeout'] += 1
                self._handle_job_failure(
                    integration_id, integration_name, execution_id,
                    start_time, e, 'timeout'
                )
                return
            except Exception as e:
                self._handle_job_failure(
                    integration_id, integration_name, execution_id,
                    start_time, e, 'failed'
                )
                return

            # run_integration catches connector exceptions and returns
            # {'success': False}; treat those as failures, not successes.
            end_time = datetime.utcnow()
            duration = (end_time - start_time).total_seconds()

            if isinstance(result, dict) and result.get('success') is False:
                err_msg = (
                    result.get('details')
                    or result.get('message')
                    or f"Integration run failed: {result.get('error')}"
                )
                self._handle_job_failure(
                    integration_id, integration_name, execution_id,
                    start_time, Exception(err_msg), 'failed'
                )
                return

            self.metrics['jobs_executed'] += 1
            self.metrics['last_success'] = end_time
            self.metrics['total_execution_time'] += duration

            record_count = (
                result.get('record_count', 0)
                if isinstance(result, dict) else 0
            )

            self._log_job_execution(
                job_id=str(integration_id),
                execution_id=execution_id,
                job_name=integration_name,
                started_at=start_time,
                completed_at=end_time,
                status='success',
                duration_seconds=duration,
                records_processed=record_count,
                message=f"Completed successfully with {record_count} records"
            )

            logger.info(
                f"Integration {integration_name} completed successfully in {duration:.2f}s "
                f"with {record_count} records"
            )

        except Exception as e:
            logger.error(f"Critical error executing integration {integration_name}: {e}", exc_info=True)
            self.metrics['jobs_failed'] += 1
            self.metrics['last_error'] = str(e)

        finally:
            # Re-fetch: the user may have changed the cron during the run.
            try:
                current = self._get_integration_safe(integration_id)
                next_run = None
                if (
                    current
                    and current.schedule_enabled
                    and current.schedule_cron
                ):
                    next_run = calculate_next_run(
                        current.schedule_cron,
                        current.schedule_timezone,
                    )
                self._update_integration_safe(
                    integration_id, {'next_run_at': next_run}
                )
            except Exception as cleanup_err:
                logger.error(
                    f"Failed to advance next_run_at for {integration_id}: {cleanup_err}"
                )

    @staticmethod
    def _skip_reason(integration) -> Optional[str]:
        if integration.is_running:
            return 'Already running'
        if not integration.is_enabled():
            return 'Integration disabled'
        return None

    def _handle_job_failure(self, integration_id: uuid.UUID, integration_name: str,
                          execution_id: str, start_time: datetime,
                          error: Exception, status: str):
        """Handle job failure with proper logging and metrics."""
        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()

        self.metrics['jobs_failed'] += 1
        self.metrics['last_error'] = str(error)

        # Log failure
        self._log_job_execution(
            job_id=str(integration_id),
            execution_id=execution_id,
            job_name=integration_name,
            started_at=start_time,
            completed_at=end_time,
            status=status,
            duration_seconds=duration,
            error_message=str(error)
        )

        logger.error(
            f"Integration {integration_name} {status} after {duration:.2f}s: {error}"
        )

    def _get_integration_safe(self, integration_id: uuid.UUID) -> Optional[Integration]:
        """Get integration with error handling."""
        try:
            return self.repository.get_by_id(integration_id)
        except Exception as e:
            logger.error(f"Failed to get integration {integration_id}: {e}")
            return None

    def _update_integration_safe(self, integration_id: uuid.UUID, updates: Dict[str, Any]):
        """Update integration with error handling."""
        try:
            self.repository.update(integration_id, updates)
        except Exception as e:
            logger.error(f"Failed to update integration {integration_id}: {e}")

    def _log_job_execution(self, **kwargs):
        """Log job execution to the job_executions table for audit trail."""
        try:
            with self.repository._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO job_executions
                            (job_id, execution_id, job_name, started_at, completed_at,
                             duration_seconds, status, records_processed, error_message, message)
                        VALUES
                            (%(job_id)s, %(execution_id)s, %(job_name)s, %(started_at)s,
                             %(completed_at)s, %(duration_seconds)s, %(status)s,
                             %(records_processed)s, %(error_message)s, %(message)s)
                        """,
                        {
                            'job_id': kwargs.get('job_id'),
                            'execution_id': kwargs.get('execution_id'),
                            'job_name': kwargs.get('job_name'),
                            'started_at': kwargs.get('started_at'),
                            'completed_at': kwargs.get('completed_at'),
                            'duration_seconds': kwargs.get('duration_seconds'),
                            'status': kwargs.get('status'),
                            'records_processed': kwargs.get('records_processed'),
                            'error_message': kwargs.get('error_message'),
                            'message': kwargs.get('message'),
                        }
                    )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to log job execution: {e}")

    def _start_health_monitoring(self):
        """Start health monitoring thread."""
        def health_check():
            while self._running and not self._shutting_down:
                try:
                    self._perform_health_check()
                except Exception as e:
                    logger.error(f"Health check failed: {e}")

                time.sleep(self._health_check_interval)

        self._health_check_thread = threading.Thread(target=health_check)
        self._health_check_thread.daemon = True
        self._health_check_thread.start()
        logger.info(f"Health monitoring started (interval: {self._health_check_interval}s)")

    def _publish_status_to_redis(self):
        """Publish scheduler status to Redis for cross-process visibility."""
        if not self._redis_client:
            return
        try:
            import json
            status = {
                'running': self._running,
                'health_status': self.metrics['health_status'],
                'jobs_scheduled': self.metrics['jobs_scheduled'],
                'jobs_executed': self.metrics['jobs_executed'],
                'jobs_failed': self.metrics['jobs_failed'],
                'last_success': self.metrics['last_success'].isoformat() if self.metrics['last_success'] else None,
                'last_error': self.metrics['last_error'],
                'updated_at': datetime.utcnow().isoformat(),
            }
            if self.scheduler:
                jobs = self.scheduler.get_jobs()
                status['jobs'] = [
                    {
                        'id': job.id,
                        'name': job.name,
                        'next_run_time': getattr(job, 'next_run_time', None).isoformat() if getattr(job, 'next_run_time', None) else None,
                    }
                    for job in jobs
                ]
            self._redis_client.setex('scheduler:status', 120, json.dumps(status))
        except Exception as e:
            logger.debug(f"Failed to publish scheduler status to Redis: {e}")

    def _check_pipeline_resync(self):
        """Check Redis flag for pipeline settings changes and trigger re-sync."""
        if not self._redis_client or not self._pipeline_service or not self.scheduler:
            return
        try:
            flag = self._redis_client.getdel('scheduler:settings_changed')
            if flag:
                logger.info("Pipeline settings changed, triggering schedule re-sync")
                self._pipeline_service.sync_schedule(self.scheduler)
        except Exception as e:
            logger.debug(f"Failed to check pipeline re-sync flag: {e}")

    def _perform_health_check(self):
        """Perform comprehensive health check with structured logging."""
        previous_status = self.metrics['health_status']
        health_issues = []

        try:
            # Check scheduler is running
            if not self.scheduler.running:
                self.metrics['health_status'] = 'error'
                health_issues.append('scheduler_not_running')
                logger.error(
                    "Scheduler health check failed: not running",
                    extra={
                        'event_type': 'integration_health_check',
                        'check_type': 'scheduler_running',
                        'status': 'error',
                        'previous_status': previous_status,
                        'issues': health_issues
                    }
                )
                return

            # Check database connectivity
            try:
                # Just try to list integrations to check DB connectivity
                self.repository.list_all()
            except Exception as e:
                self.metrics['health_status'] = 'degraded'
                health_issues.append('database_connectivity')
                logger.error(
                    f"Database health check failed: {e}",
                    extra={
                        'event_type': 'integration_health_check',
                        'check_type': 'database_connectivity',
                        'status': 'degraded',
                        'previous_status': previous_status,
                        'error_message': str(e),
                        'issues': health_issues
                    }
                )
                return

            # Check job failure rate
            if self.metrics['jobs_executed'] > 10:
                failure_rate = self.metrics['jobs_failed'] / self.metrics['jobs_executed']
                if failure_rate > 0.5:
                    self.metrics['health_status'] = 'degraded'
                    health_issues.append('high_failure_rate')
                    logger.warning(
                        f"High job failure rate: {failure_rate:.2%}",
                        extra={
                            'event_type': 'integration_health_check',
                            'check_type': 'failure_rate',
                            'status': 'degraded',
                            'previous_status': previous_status,
                            'failure_rate_percent': round(failure_rate * 100, 2),
                            'jobs_executed': self.metrics['jobs_executed'],
                            'jobs_failed': self.metrics['jobs_failed'],
                            'issues': health_issues
                        }
                    )
                    return

            # All checks passed
            self.metrics['health_status'] = 'healthy'

            # Log status transition if changed to healthy
            if previous_status != 'healthy':
                logger.info(
                    f"Integration health recovered: {previous_status} -> healthy",
                    extra={
                        'event_type': 'integration_health_check',
                        'status': 'healthy',
                        'previous_status': previous_status,
                        'jobs_executed': self.metrics['jobs_executed'],
                        'jobs_failed': self.metrics['jobs_failed'],
                        'jobs_scheduled': self.metrics['jobs_scheduled'],
                        'issues': []
                    }
                )

            # Check for pipeline settings changes via Redis flag
            self._check_pipeline_resync()

            # Publish status to Redis for cross-process visibility
            self._publish_status_to_redis()

        except Exception as e:
            self.metrics['health_status'] = 'error'
            health_issues.append('health_check_exception')
            logger.error(
                f"Health check error: {e}",
                extra={
                    'event_type': 'integration_health_check',
                    'status': 'error',
                    'previous_status': previous_status,
                    'error_message': str(e),
                    'error_type': type(e).__name__,
                    'issues': health_issues
                }
            )

    def get_health_status(self) -> Dict[str, Any]:
        """
        Get comprehensive health status for monitoring.

        Returns:
            Dictionary with health metrics
        """
        uptime = None
        if self.metrics['started_at']:
            uptime = (datetime.utcnow() - self.metrics['started_at']).total_seconds()

        jobs = self.scheduler.get_jobs() if self.scheduler else []
        next_runs = [
            getattr(job, 'next_run_time', None)
            for job in jobs
            if getattr(job, 'next_run_time', None)
        ]

        failure_rate = 0
        if self.metrics['jobs_executed'] > 0:
            failure_rate = self.metrics['jobs_failed'] / self.metrics['jobs_executed']

        avg_execution_time = 0
        if self.metrics['jobs_executed'] > 0:
            avg_execution_time = self.metrics['total_execution_time'] / self.metrics['jobs_executed']

        return {
            'status': self.metrics['health_status'],
            'running': self._running,
            'uptime_seconds': uptime,
            'metrics': {
                'jobs_scheduled': len(jobs),
                'jobs_executed': self.metrics['jobs_executed'],
                'jobs_failed': self.metrics['jobs_failed'],
                'jobs_missed': self.metrics['jobs_missed'],
                'jobs_timeout': self.metrics['jobs_timeout'],
                'failure_rate': round(failure_rate * 100, 2),
                'avg_execution_time': round(avg_execution_time, 2)
            },
            'next_run': min(next_runs).isoformat() if next_runs else None,
            'last_success': self.metrics['last_success'].isoformat() if self.metrics['last_success'] else None,
            'last_error': self.metrics['last_error'],
            'circuit_breaker': {
                'open': self.job_executor.circuit_breaker.is_open(),
                'failure_count': self.job_executor.circuit_breaker.failure_count
            }
        }

    def get_scheduler_status(self) -> Dict[str, Any]:
        """
        Get simplified scheduler status for basic monitoring.

        Returns:
            Dictionary with basic scheduler status (running, job_count, next_run)
        """
        jobs = self.scheduler.get_jobs() if self.scheduler else []
        next_runs = [
            getattr(job, 'next_run_time', None)
            for job in jobs
            if getattr(job, 'next_run_time', None)
        ]

        return {
            'running': self._running,
            'job_count': len(jobs),
            'next_run': min(next_runs).isoformat() if next_runs else None
        }

    def schedule_integration(self, integration_id: uuid.UUID, cron: str,
                           timezone: str = 'UTC') -> Dict[str, Any]:
        """
        Schedule an integration for automated execution.

        Args:
            integration_id: Integration UUID
            cron: Cron expression or preset
            timezone: Timezone for schedule evaluation

        Returns:
            Result dictionary with success status and details
        """
        try:
            # Validate cron expression
            if not is_valid_cron(cron):
                return {
                    'success': False,
                    'error': 'INVALID_CRON',
                    'message': 'Invalid cron expression'
                }

            # Validate timezone
            try:
                pytz.timezone(timezone)
            except pytz.UnknownTimeZoneError:
                return {
                    'success': False,
                    'error': 'INVALID_TIMEZONE',
                    'message': f'Invalid timezone: {timezone}'
                }

            # Get integration
            integration = self.repository.get_by_id(integration_id)
            if not integration:
                return {
                    'success': False,
                    'error': 'NOT_FOUND',
                    'message': 'Integration not found'
                }

            # Calculate next run time
            next_run = calculate_next_run(cron, timezone)

            # Update integration in database
            self.repository.update(integration_id, {
                'schedule_enabled': True,
                'schedule_cron': cron,
                'schedule_timezone': timezone,
                'next_run_at': next_run
            })

            # Reload integration
            integration = self.repository.get_by_id(integration_id)

            # Add/update job in scheduler if running in this process
            if self._running:
                self._schedule_integration_job(integration)

            # Signal the scheduler worker (separate process) to pick up the change
            signal_schedule_change(integration_id, redis_client=self._redis_client)

            logger.info(f"Scheduled integration {integration.name} ({integration_id})")

            return {
                'success': True,
                'message': 'Integration scheduled successfully',
                'next_run_at': next_run.isoformat() if next_run else None
            }

        except Exception as e:
            logger.error(f"Error scheduling integration: {e}", exc_info=True)
            return {
                'success': False,
                'error': 'EXCEPTION',
                'message': f'Failed to schedule integration: {str(e)}'
            }

    def unschedule_integration(self, integration_id: uuid.UUID) -> Dict[str, Any]:
        """
        Remove scheduling for an integration.

        Args:
            integration_id: Integration UUID

        Returns:
            Result dictionary with success status
        """
        try:
            # Get integration
            integration = self.repository.get_by_id(integration_id)
            if not integration:
                return {
                    'success': False,
                    'error': 'NOT_FOUND',
                    'message': 'Integration not found'
                }

            # Update integration in database
            self.repository.update(integration_id, {
                'schedule_enabled': False,
                'next_run_at': None
            })

            # Remove job from scheduler if running in this process
            if self._running:
                job_id = str(integration_id)
                if self.scheduler.get_job(job_id):
                    self.scheduler.remove_job(job_id)
                    logger.debug(f"Removed job for integration {integration.name} ({job_id})")

            # Signal the scheduler worker (separate process) to pick up the change
            signal_schedule_change(integration_id, redis_client=self._redis_client)

            logger.info(f"Unscheduled integration {integration.name} ({integration_id})")

            return {
                'success': True,
                'message': 'Integration unscheduled successfully'
            }

        except Exception as e:
            logger.error(f"Error unscheduling integration: {e}", exc_info=True)
            return {
                'success': False,
                'error': 'EXCEPTION',
                'message': f'Failed to unschedule integration: {str(e)}'
            }

    def trigger_integration(self, integration_id: uuid.UUID) -> Dict[str, Any]:
        """
        Trigger an immediate one-off execution of an integration.

        Submits a date-triggered APScheduler job that runs once without
        affecting the integration's recurring schedule.

        Args:
            integration_id: Integration UUID

        Returns:
            Result dictionary with success status and execution_id
        """
        try:
            integration = self.repository.get_by_id(integration_id)
            if not integration:
                return {
                    'success': False,
                    'error': 'NOT_FOUND',
                    'message': 'Integration not found'
                }

            if integration.is_running:
                return {
                    'success': False,
                    'error': 'ALREADY_RUNNING',
                    'message': 'Integration is already running'
                }

            if not self._running:
                return {
                    'success': False,
                    'error': 'SCHEDULER_NOT_RUNNING',
                    'message': 'Scheduler is not running'
                }

            execution_id = str(uuid.uuid4())

            self.scheduler.add_job(
                func=self._execute_integration_robust,
                trigger='date',
                id=f"manual_{execution_id}",
                name=f"Manual: {integration.name}",
                args=[integration.id, integration.name],
                replace_existing=False,
                max_instances=1,
            )

            logger.info(
                f"Triggered manual execution of {integration.name} "
                f"(execution_id={execution_id})"
            )

            return {
                'success': True,
                'execution_id': execution_id,
                'message': f'Integration {integration.name} triggered successfully'
            }

        except Exception as e:
            logger.error(f"Error triggering integration: {e}", exc_info=True)
            return {
                'success': False,
                'error': 'EXCEPTION',
                'message': f'Failed to trigger integration: {str(e)}'
            }

    def shutdown(self, wait: bool = True, timeout: int = 30):
        """
        Graceful shutdown with timeout protection.

        Args:
            wait: Whether to wait for running jobs
            timeout: Maximum time to wait for jobs to complete
        """
        if not self._running:
            logger.warning("Scheduler is not running")
            return

        logger.info(f"Starting graceful shutdown (wait={wait}, timeout={timeout}s)...")
        self._shutting_down = True
        self.metrics['health_status'] = 'shutting_down'

        try:
            # Stop accepting new jobs
            if self.scheduler:
                # Wait for running jobs with timeout
                if wait:
                    start_time = time.time()
                    while True:
                        running_jobs = [j for j in self.scheduler.get_jobs() if j.next_run_time]
                        if not running_jobs:
                            break
                        if time.time() - start_time > timeout:
                            logger.warning(f"Shutdown timeout reached, forcing stop")
                            break
                        time.sleep(1)

                self.scheduler.shutdown(wait=False)

            self._running = False
            logger.info("Scheduler shutdown complete")

        except Exception as e:
            logger.error(f"Error during shutdown: {e}", exc_info=True)

    # Event listeners
    def _job_executed_listener(self, event):
        """Handle successful job execution with structured logging."""
        # Calculate execution time if available
        execution_time_ms = None
        if hasattr(event, 'scheduled_run_time') and event.scheduled_run_time:
            execution_time_ms = (datetime.utcnow() - event.scheduled_run_time.replace(tzinfo=None)).total_seconds() * 1000

        logger.info(
            f"Schedule executed successfully: {event.job_id}",
            extra={
                'job_id': event.job_id,
                'event_type': 'job_executed',
                'scheduled_time': event.scheduled_run_time.isoformat() if hasattr(event, 'scheduled_run_time') and event.scheduled_run_time else None,
                'execution_time_ms': round(execution_time_ms, 2) if execution_time_ms else None,
                'retval': str(event.retval)[:200] if hasattr(event, 'retval') and event.retval else None,
                'jobs_executed_total': self.metrics['jobs_executed'],
                'health_status': self.metrics['health_status']
            }
        )

    def _job_error_listener(self, event):
        """Handle job error with structured logging."""
        # Extract exception details
        exception_type = type(event.exception).__name__ if hasattr(event, 'exception') and event.exception else 'Unknown'
        exception_message = str(event.exception)[:500] if hasattr(event, 'exception') and event.exception else 'No exception details'

        logger.error(
            f"Schedule execution failed: {event.job_id}",
            extra={
                'job_id': event.job_id,
                'event_type': 'job_error',
                'exception_type': exception_type,
                'exception_message': exception_message,
                'scheduled_time': event.scheduled_run_time.isoformat() if hasattr(event, 'scheduled_run_time') and event.scheduled_run_time else None,
                'jobs_failed_total': self.metrics['jobs_failed'],
                'traceback': str(event.traceback)[:1000] if hasattr(event, 'traceback') and event.traceback else None,
                'health_status': self.metrics['health_status']
            },
            exc_info=False  # Already have traceback in extra
        )

    def _job_missed_listener(self, event):
        """Handle missed job with structured logging."""
        self.metrics['jobs_missed'] += 1

        # Calculate how late the job is
        delay_seconds = None
        if hasattr(event, 'scheduled_run_time') and event.scheduled_run_time:
            delay_seconds = (datetime.utcnow() - event.scheduled_run_time.replace(tzinfo=None)).total_seconds()

        logger.warning(
            f"Schedule missed: {event.job_id}",
            extra={
                'job_id': event.job_id,
                'event_type': 'job_missed',
                'scheduled_time': event.scheduled_run_time.isoformat() if hasattr(event, 'scheduled_run_time') and event.scheduled_run_time else None,
                'delay_seconds': round(delay_seconds, 2) if delay_seconds else None,
                'jobs_missed_total': self.metrics['jobs_missed'],
                'health_status': self.metrics['health_status'],
                'reason': 'Scheduler was not running or job was delayed beyond misfire_grace_time'
            }
        )

    def _job_submitted_listener(self, event):
        """Handle job submission with structured logging."""
        logger.info(
            f"Schedule triggered: {event.job_id}",
            extra={
                'job_id': event.job_id,
                'event_type': 'job_submitted',
                'scheduled_time': event.scheduled_run_time.isoformat() if hasattr(event, 'scheduled_run_time') and event.scheduled_run_time else None,
                'jobs_scheduled_total': self.metrics['jobs_scheduled'],
                'health_status': self.metrics['health_status']
            }
        )

    def _job_max_instances_listener(self, event):
        """Handle max instances reached with structured logging."""
        logger.warning(
            f"Schedule skipped (max instances): {event.job_id}",
            extra={
                'job_id': event.job_id,
                'event_type': 'job_max_instances',
                'scheduled_time': event.scheduled_run_time.isoformat() if hasattr(event, 'scheduled_run_time') and event.scheduled_run_time else None,
                'health_status': self.metrics['health_status'],
                'reason': 'Previous execution still running (max_instances=1)'
            }
        )