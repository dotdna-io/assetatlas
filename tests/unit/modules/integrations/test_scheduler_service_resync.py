# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression tests for ``SchedulerService.resync_schedules`` orphan cleanup.

``resync_schedules`` runs at scheduler startup and every 2 minutes. It removes any
scheduler job that is not a currently-configured integration. The worker registers
several long-lived *system* jobs on the same scheduler (``interval_alert_rules``,
``pipeline_watchdog``, ``alert_delivery_drain`` ...). These were being deleted as
"orphans" roughly 2 minutes after every startup — silently disabling interval alert
evaluation and outbound alert delivery on every deployment.

Orphan cleanup must only remove *integration* jobs (whose ids are UUIDs), never the
worker's system jobs (whose ids are plain strings).
"""
import uuid

import pytest
import pytz
from apscheduler.schedulers.background import BackgroundScheduler

from src.modules.integrations.services.scheduler_service import SchedulerService


class _NoIntegrationsRepo:
    """Repository stub: the DB has zero integrations, so every job on the
    scheduler is an orphan-removal candidate."""

    def list_all(self, decrypt=False):
        return []


def _noop():  # a real, storable callable for the scheduled job
    pass


@pytest.fixture
def service():
    svc = SchedulerService(integration_repository=_NoIntegrationsRepo())
    scheduler = BackgroundScheduler(timezone=pytz.UTC)
    scheduler.start(paused=True)  # paused: jobs are stored but never executed
    svc.scheduler = scheduler
    try:
        yield svc
    finally:
        scheduler.shutdown(wait=False)


def test_resync_preserves_system_job_with_string_id(service):
    """A worker-registered system job (string id) must survive a resync even
    though it is not a configured integration."""
    service.scheduler.add_job(
        func=_noop, trigger="interval", minutes=5,
        id="interval_alert_rules", name="Interval Alert Rules",
    )

    service.resync_schedules()

    assert service.scheduler.get_job("interval_alert_rules") is not None


def test_resync_removes_orphaned_integration_job_with_uuid_id(service):
    """A leftover integration job (UUID id) whose integration no longer exists
    must still be removed — the fix must not disable legitimate orphan cleanup."""
    orphan_id = str(uuid.uuid4())
    service.scheduler.add_job(
        func=_noop, trigger="interval", minutes=5,
        id=orphan_id, name="Integration: deleted",
    )

    service.resync_schedules()

    assert service.scheduler.get_job(orphan_id) is None
