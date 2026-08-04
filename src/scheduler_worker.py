#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
AssetAtlas Scheduler Worker

Dedicated worker process that runs the integration scheduler independently
from the web application. This allows the scheduler to run in a separate
Docker container, preventing duplicate job execution during rolling deployments.

Usage:
    SCHEDULER_MODE=worker python src/scheduler_worker.py

Environment Variables:
    DATABASE_URL        - PostgreSQL connection string (required)
    SCHEDULER_MODE      - Must be set to 'worker' (required)
    FLASK_ENV           - Environment: development, production (default: production)
    LOG_LEVEL           - Logging level: DEBUG, INFO, WARNING, ERROR (default: INFO)

Example:
    DATABASE_URL="postgresql://user:pass@host:5432/db" \\
    SCHEDULER_MODE=worker \\
    FLASK_ENV=production \\
    python src/scheduler_worker.py
"""

import os
import sys
import time
import signal
import logging
from typing import Optional

# Ensure Python path includes the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prometheus_client import start_http_server
from src.api.utils.metrics import (
    scheduler_last_heartbeat,
    track_scheduled_job,
    update_scheduled_jobs_total,
)
from src.database.postgres_connection import PostgreSQLConnectionManager
from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.integrations.services import IntegrationService, SchedulerService
from src.modules.integrations.services.asset_generation_trigger_service import AssetGenerationTriggerService
from src.modules.integrations import discover_connectors
from src.modules.pipeline.pipeline_service import PipelineService
from src.api.modules.settings import SettingsManager
from src.modules.alerts.repository import AlertsRepository
from src.modules.pipeline_watchdog.repository import PipelineWatchdogRepository
from src.modules.pipeline_watchdog.service import PipelineWatchdogService

# Configure logging
logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class SchedulerWorker:
    """
    Standalone scheduler worker that manages integration executions.

    This worker initializes the minimal services needed for the scheduler
    (database, integration service, scheduler service) and runs the scheduler
    indefinitely until stopped.
    """

    def __init__(self):
        """Initialize the scheduler worker"""
        self.scheduler_service: Optional[SchedulerService] = None
        self.pipeline_service: Optional[PipelineService] = None
        self.pipeline_watchdog_service: Optional[PipelineWatchdogService] = None
        self.alerts_service = None  # built DB-direct in _init_services (interval rules)
        self.alert_delivery_service = None  # built DB-direct in _init_services
        self.settings_manager = None  # built DB-direct in _init_services
        self.update_check_service = None  # built DB-direct in _init_services
        self.connection_manager: Optional[PostgreSQLConnectionManager] = None
        self.shutdown_requested = False

        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        signal_name = 'SIGINT' if signum == signal.SIGINT else 'SIGTERM'
        logger.info(f"Received {signal_name}, initiating graceful shutdown...")
        self.shutdown_requested = True

    def _validate_environment(self):
        """Validate required environment variables"""
        # Check SCHEDULER_MODE
        scheduler_mode = os.getenv('SCHEDULER_MODE')
        if scheduler_mode != 'worker':
            logger.error(
                f"SCHEDULER_MODE must be set to 'worker' for scheduler worker. "
                f"Current value: {scheduler_mode}"
            )
            sys.exit(1)

        # Check DATABASE_URL
        database_url = os.getenv('DATABASE_URL')
        if not database_url:
            logger.error("DATABASE_URL environment variable is required")
            sys.exit(1)

        logger.info("Environment validation passed")

    def _init_database(self) -> PostgreSQLAdapter:
        """Initialize database connection"""
        logger.info("Connecting to database...")

        database_url = os.getenv('DATABASE_URL')
        self.connection_manager = PostgreSQLConnectionManager(
            database_url=database_url,
            min_connections=20,
            max_connections=50
        )
        db_adapter = PostgreSQLAdapter(connection_manager=self.connection_manager)

        logger.info("Database connection established")
        return db_adapter

    def _init_services(self, db_adapter: PostgreSQLAdapter):
        """Initialize integration, scheduler, and pipeline services"""
        logger.info("Initializing integration services...")

        # Discover all available connectors
        discover_connectors()

        # Initialize integration service
        integration_service = IntegrationService()
        connector_count = len(integration_service.get_available_connectors())
        logger.info(f"Discovered {connector_count} connector types")

        # Initialize scheduler service
        self.scheduler_service = SchedulerService()
        self.scheduler_service.set_db_references(self.connection_manager, db_adapter)
        self.scheduler_service.initialize(integration_service)

        # Set up Redis for cross-process status sharing
        redis_url = os.getenv('REDIS_URL')
        if redis_url:
            try:
                import redis
                redis_client = redis.from_url(redis_url)
                redis_client.ping()
                self.scheduler_service.set_redis_client(redis_client)
                logger.info("Redis connected for cross-process scheduler status")
            except Exception as e:
                logger.warning(f"Redis not available for scheduler status: {e}")

        logger.info("Integration services initialized")

        # Initialize pipeline service for scheduled pipeline runs
        settings_manager = SettingsManager(self.connection_manager)

        # One DB-direct FieldResolver (no app context) shared by the posture
        # evaluation service and the interval-alert compiler below — one
        # FieldCatalog/cache instead of two, and a build failure degrades those
        # features rather than aborting _init_services (which sys.exits the worker).
        field_resolver = None
        try:
            from src.modules.shared.field_resolver_factory import build_field_resolver
            field_resolver = build_field_resolver(db_adapter)
        except Exception as e:
            logger.warning("Field resolver unavailable (posture + interval alerts degraded): %s", e)

        posture_evaluation_service = None
        if field_resolver is not None:
            try:
                from src.modules.posture_rules.repositories.posture_repository import PostureRepository
                from src.modules.posture_rules.services.posture_evaluation_service import PostureEvaluationService
                from src.modules.tag_rules.services.query_engine_service import QueryEngineService
                posture_evaluation_service = PostureEvaluationService(
                    repository=PostureRepository(db_adapter=db_adapter),
                    query_engine=QueryEngineService(resolver=field_resolver),
                    db_adapter=db_adapter,
                    resolver=field_resolver,
                )
            except Exception as e:
                logger.warning("Posture evaluation service not available in scheduler: %s", e)

        trigger_service = AssetGenerationTriggerService(
            db_adapter=db_adapter,
            settings_manager=settings_manager,
            posture_evaluation_service=posture_evaluation_service,
        )
        self.pipeline_service = PipelineService(
            connection_manager=self.connection_manager,
            trigger_service=trigger_service
        )
        logger.info("Pipeline service initialized")

        # Wire pipeline service into scheduler for event-driven re-sync
        if self.pipeline_service:
            self.scheduler_service.set_pipeline_service(self.pipeline_service)

        # Pipeline watchdog — out-of-band monitor for pipeline health.
        # Uses AlertsRepository without a query compiler (watchdog doesn't
        # evaluate filter rules, only creates/resolves its own alerts).
        alerts_repo = AlertsRepository(db_adapter)
        watchdog_repo = PipelineWatchdogRepository(db_adapter)
        self.pipeline_watchdog_service = PipelineWatchdogService(
            watchdog_repo=watchdog_repo, alert_repo=alerts_repo,
        )
        logger.info("Pipeline watchdog service initialized")

        # Interval-cadence alert evaluation — evaluates the sql/filter rules
        # whose evaluation_cadence='interval' every 5 minutes, independent of the
        # pipeline. Reuses the shared field_resolver so filter rules on
        # group:<group>:<attr> fields resolve rather than silently matching
        # nothing (the #791 lesson; mirrors s20's _get_alerts_service fallback).
        # Wrapped so a build failure can't abort the worker — the scheduler job
        # guards on self.alerts_service being set.
        if field_resolver is not None:
            try:
                from src.modules.alerts.service import AlertsService
                from src.modules.shared.asset_query_compiler import AssetQueryCompiler
                self.alerts_service = AlertsService(AlertsRepository(
                    db_adapter, query_compiler=AssetQueryCompiler(resolver=field_resolver),
                ))
                logger.info("Interval alert evaluation service initialized")
            except Exception as e:
                logger.warning("Interval alert evaluation service unavailable: %s", e)

        # Alert delivery — shared by interval + pipeline-path alert/anomaly
        # evaluation. Stored on self so _start_scheduler can register the drain
        # job, and settings_manager so ALERT_DELIVERY_DRAIN_INTERVAL_SECONDS
        # (and other alerting settings) are readable from there too.
        from src.modules.alert_delivery.wiring import build_alert_delivery_service
        self.settings_manager = settings_manager
        self.alert_delivery_service = build_alert_delivery_service(
            db_adapter, settings_manager=settings_manager)
        if self.alerts_service is not None:
            self.alerts_service.delivery = self.alert_delivery_service
        # The watchdog is constructed above, before the delivery service exists —
        # so it gets the same post-construction wiring, not a ctor arg (#985).
        self.pipeline_watchdog_service.delivery = self.alert_delivery_service
        # Email alert channel (#919) sends via EmailService; the drain runs here
        # with no Flask app context, so inject the sender the channel will use.
        from src.auth.services.email_service import EmailService
        from src.modules.alert_delivery.channels.email_channel import set_email_service
        set_email_service(EmailService(settings_manager))

        # Gated update check — daily, air-gap-safe. Reuses the watchdog's
        # AlertsRepository (no compiler needed: it only creates/resolves its own
        # external alert). Does nothing unless system.enable_update_check is on.
        # Wrapped so a build failure can't abort the worker.
        try:
            from src.modules.alerts.update_check import UpdateCheckService
            self.update_check_service = UpdateCheckService(
                alert_repo=alerts_repo,
                settings_manager=settings_manager,
                current_version=self._read_version(),
                delivery_service=self.alert_delivery_service,
            )
            logger.info("Update-check service initialized")
        except Exception as e:
            logger.warning("Update-check service unavailable: %s", e)

    def _start_scheduler(self):
        """Start the scheduler, sync pipeline schedule, and register periodic re-sync"""
        logger.info("Starting scheduler service...")
        self.scheduler_service.start()
        logger.info("Scheduler service started successfully")

        # Prometheus: record each job's outcome + duration and keep the
        # jobs-total gauge fresh. One listener, registered once after start;
        # it fires for every job (including the periodic (re)sync jobs).
        from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
        self.scheduler_service.scheduler.add_listener(
            self._on_job_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR
        )
        logger.info("Prometheus scheduled-job listener registered")

        # Sync pipeline schedule from settings
        if self.pipeline_service:
            self.pipeline_service.sync_schedule(self.scheduler_service.scheduler)
            logger.info("Pipeline schedule synced")

            # Re-sync pipeline schedule every 5 minutes to pick up settings
            # changes made via the API (which runs in a separate process)
            from apscheduler.triggers.interval import IntervalTrigger
            self.scheduler_service.scheduler.add_job(
                func=self._resync_pipeline_schedule,
                trigger=IntervalTrigger(minutes=5),
                id='pipeline_schedule_resync',
                name='Pipeline Schedule Re-sync',
                replace_existing=True,
                coalesce=True,
                max_instances=1
            )
            logger.info("Pipeline schedule re-sync job registered (every 5 minutes)")

        # Pipeline watchdog — fires pipeline-health alerts independent of the
        # in-pipeline evaluator.
        self.scheduler_service.scheduler.add_job(
            func=self.pipeline_watchdog_service.tick,
            trigger=IntervalTrigger(minutes=5),
            id='pipeline_watchdog',
            name='Pipeline Watchdog',
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        logger.info("Pipeline watchdog registered (every 5 minutes)")

        # Interval-cadence alert rules — evaluate every 5 minutes here in the
        # worker (no app context). Failures are logged, never raised, so a bad
        # rule can't kill the scheduler.
        self.scheduler_service.scheduler.add_job(
            func=self._evaluate_interval_alert_rules,
            trigger=IntervalTrigger(minutes=5),
            id='interval_alert_rules',
            name='Interval Alert Rules',
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        logger.info("Interval alert rules job registered (every 5 minutes)")

        # Alert delivery drain — sends queued deliveries (created by both the
        # interval/pipeline alert paths and anomaly detection) out over their
        # routed channels. Interval configurable via settings so a slow channel
        # can be drained less aggressively without a redeploy.
        drain_interval = 25
        try:
            drain_interval = int(self.settings_manager.get(
                'alerting', 'ALERT_DELIVERY_DRAIN_INTERVAL_SECONDS', 25))
        except Exception:
            pass
        self.scheduler_service.scheduler.add_job(
            func=self._drain_alert_deliveries,
            trigger=IntervalTrigger(seconds=drain_interval),
            id='alert_delivery_drain',
            name='Alert Delivery Drain',
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        logger.info("Alert delivery drain registered (every %ss)", drain_interval)

        # Gated update check — runs daily; does nothing unless the
        # system.enable_update_check setting is on, and swallows all network
        # errors (air-gapped installs stay quiet).
        self.scheduler_service.scheduler.add_job(
            func=self._run_update_check,
            trigger=IntervalTrigger(hours=24),
            id='update_check',
            name='Update Check',
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        logger.info("Update-check job registered (daily)")

        # Check Redis for schedule changes signaled by the API (fast path)
        from apscheduler.triggers.interval import IntervalTrigger
        self.scheduler_service.scheduler.add_job(
            func=self.scheduler_service.resync_dirty_schedules,
            trigger=IntervalTrigger(seconds=10),
            id='dirty_schedule_check',
            name='Dirty Schedule Check',
            replace_existing=True,
            coalesce=True,
            max_instances=1
        )
        logger.info("Dirty schedule check registered (every 10 seconds)")

        # Full re-sync integration schedules every 2 minutes (fallback)
        self.scheduler_service.scheduler.add_job(
            func=self.scheduler_service.resync_schedules,
            trigger=IntervalTrigger(minutes=2),
            id='schedule_resync',
            name='Integration Schedule Re-sync',
            replace_existing=True,
            coalesce=True,
            max_instances=1
        )
        logger.info("Integration schedule re-sync job registered (every 2 minutes)")

        # Prometheus heartbeat — tick every 30 seconds so the staleness alert fires
        # if the scheduler process dies
        self.scheduler_service.scheduler.add_job(
            lambda: scheduler_last_heartbeat.set(time.time()),
            'interval', seconds=30, id='prom_heartbeat', replace_existing=True,
        )
        logger.info("Prometheus heartbeat job registered (every 30 seconds)")

        # Seed the jobs-total gauge now that all jobs are registered; the
        # listener keeps it fresh thereafter.
        try:
            update_scheduled_jobs_total(
                len(self.scheduler_service.scheduler.get_jobs())
            )
        except Exception:
            logger.debug("initial scheduled-jobs-total metric failed", exc_info=True)

    def _on_job_event(self, event):
        """APScheduler listener (EVENT_JOB_EXECUTED | EVENT_JOB_ERROR): record
        each job's outcome + duration and refresh the jobs-total gauge. Never
        raises — a metrics failure must not disturb the scheduler.

        Duration is elapsed-since-scheduled (now - scheduled_run_time); with
        immediate dispatch (max_instances=1, coalesce) that is effectively the
        run time. The jobs-total refresh here also covers the (re)sync jobs:
        when resync_schedules/resync_dirty_schedules finish, their own EXECUTED
        event re-counts the live jobs.
        """
        try:
            status = "failed" if event.exception else "success"
            duration = 0.0
            scheduled = getattr(event, "scheduled_run_time", None)
            if scheduled is not None:
                duration = max(0.0, time.time() - scheduled.timestamp())
            track_scheduled_job(event.job_id, duration, status)
        except Exception:
            logger.debug("scheduled-job metric update failed", exc_info=True)
        try:
            update_scheduled_jobs_total(
                len(self.scheduler_service.scheduler.get_jobs())
            )
        except Exception:
            logger.debug(
                "scheduled-jobs-total metric update failed", exc_info=True
            )

    def _resync_pipeline_schedule(self):
        """Periodically re-sync pipeline schedule from DB settings."""
        if self.pipeline_service and self.scheduler_service:
            self.pipeline_service.sync_schedule(self.scheduler_service.scheduler)

    def _evaluate_interval_alert_rules(self):
        """Evaluate interval-cadence alert rules. Never raises (scheduler job).

        Runs in the scheduler worker with no app context; a failure here is
        logged and swallowed so it can't stop the scheduler or other jobs.
        """
        if not self.alerts_service:
            return
        try:
            stats = self.alerts_service.evaluate_interval_rules()
            logger.info("Interval alert evaluation complete: %s", stats)
        except Exception as e:
            logger.error(
                "Interval alert evaluation failed (non-fatal): %s",
                e, exc_info=True,
            )

    def _drain_alert_deliveries(self):
        """Send queued alert deliveries. Never raises (scheduler job)."""
        if not getattr(self, 'alert_delivery_service', None):
            return
        try:
            self.alert_delivery_service.drain()
        except Exception as e:
            logger.error("Alert delivery drain failed (non-fatal): %s", e, exc_info=True)

    def _read_version(self):
        """App version for the update check.

        Delegates to the canonical reader (APP_VERSION env var > VERSION file >
        fallback). The deployed image sets APP_VERSION and does NOT ship the
        VERSION file, so reading the file alone would yield an empty version and
        silently disable the update check in every container.
        """
        try:
            from src.api.config import _read_version as read_app_version
            return read_app_version()
        except Exception as e:
            logger.warning("Could not determine app version: %s", e)
            return None

    def _run_update_check(self):
        """Run the gated update check. Never raises (scheduler job)."""
        if not self.update_check_service:
            return
        try:
            self.update_check_service.tick()
        except Exception as e:
            logger.error("Update check failed (non-fatal): %s", e, exc_info=True)

    def _stop_scheduler(self):
        """Stop the scheduler gracefully"""
        if self.scheduler_service:
            logger.info("Stopping scheduler service...")
            try:
                self.scheduler_service.shutdown()
                logger.info("Scheduler service stopped successfully")
            except Exception as e:
                logger.error(f"Error stopping scheduler: {e}", exc_info=True)

    def _close_database(self):
        """Close database connection"""
        if self.connection_manager:
            logger.info("Closing database connection...")
            try:
                self.connection_manager.close_all_connections()
                logger.info("Database connection closed")
            except Exception as e:
                logger.error(f"Error closing database connection: {e}", exc_info=True)

    def run(self):
        """
        Run the scheduler worker.

        This method:
        1. Validates environment variables
        2. Initializes database and services
        3. Starts the scheduler
        4. Runs indefinitely until shutdown signal received
        5. Performs graceful shutdown
        """
        try:
            logger.info("=" * 60)
            logger.info("AssetAtlas Scheduler Worker")
            logger.info("=" * 60)

            # Validate environment
            self._validate_environment()

            # Start Prometheus metrics endpoint
            metrics_port = int(os.getenv('SCHEDULER_METRICS_PORT', '9091'))
            try:
                start_http_server(metrics_port)
                logger.info("Scheduler /metrics exposed on :%d", metrics_port)
            except Exception as e:
                logger.warning("Could not start scheduler metrics endpoint: %s — continuing without it", e)

            # Initialize components
            db_adapter = self._init_database()
            self._init_services(db_adapter)
            self._start_scheduler()

            logger.info("=" * 60)
            logger.info("Scheduler worker is running")
            logger.info("Press Ctrl+C to stop")
            logger.info("=" * 60)

            # Run until shutdown requested
            while not self.shutdown_requested:
                time.sleep(1)

            logger.info("Shutdown initiated")

        except Exception as e:
            logger.error(f"Fatal error in scheduler worker: {e}", exc_info=True)
            sys.exit(1)

        finally:
            # Graceful shutdown
            self._stop_scheduler()
            self._close_database()
            logger.info("Scheduler worker stopped")


def main():
    """Entry point for scheduler worker"""
    worker = SchedulerWorker()
    worker.run()


if __name__ == '__main__':
    main()
