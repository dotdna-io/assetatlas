# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Regression tests for integration schedule recovery bugs.

Covers:
- test_connection failure should not flip status to ERROR (which would
  make is_enabled() False and break the schedule).
- enable_integration should clear stale last_error and recompute
  next_run_at so the UI doesn't keep showing "Overdue" after re-enable.
- _execute_integration_robust must advance next_run_at on every fire,
  including skip paths (disabled / already-running).
- _execute_integration_robust must treat run_integration() returning
  {'success': False} as a failure, not a success.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.modules.integrations.models.integration import Integration, IntegrationStatus
from src.modules.integrations.services.integration_service import IntegrationService
from src.modules.integrations.services.scheduler_service import SchedulerService


@pytest.fixture
def integration_id():
    return uuid.uuid4()


@pytest.fixture
def integration_svc():
    with patch.object(IntegrationService, '__init__', lambda self, **kw: None):
        svc = IntegrationService()
        svc.repository = MagicMock()
        svc.batch_repository = MagicMock()
        svc.raw_data_repository = MagicMock()
        svc.mapping_service = MagicMock()
        svc.conn_manager = MagicMock()
    return svc


@pytest.fixture
def scheduler_svc():
    svc = SchedulerService.__new__(SchedulerService)
    svc.repository = MagicMock()
    svc.integration_service = MagicMock()
    svc.scheduler = MagicMock()
    svc._running = True
    svc._shutting_down = False
    svc.job_executor = MagicMock()
    svc.metrics = {
        'jobs_scheduled': 0,
        'jobs_executed': 0,
        'jobs_failed': 0,
        'jobs_missed': 0,
        'jobs_timeout': 0,
        'last_error': None,
        'last_success': None,
        'total_execution_time': 0,
        'health_status': 'healthy',
    }
    return svc


def _last_next_run_update(scheduler_svc):
    """Find the scheduler's last update that touched next_run_at."""
    for call in scheduler_svc.repository.update.call_args_list:
        args = call[0]
        if len(args) >= 2 and 'next_run_at' in args[1]:
            return args[1]
    return None


def _enabled_integration(**overrides) -> Integration:
    """Build an enabled integration with a working schedule."""
    defaults = dict(
        id=overrides.pop('id', uuid.uuid4()),
        name=overrides.pop('name', 'Test'),
        integration_type=overrides.pop('integration_type', 'custom_http'),
        category=overrides.pop('category', 'Custom'),
        config=overrides.pop('config', {}),
        field_mapping=overrides.pop('field_mapping', {}),
        status=overrides.pop('status', IntegrationStatus.ENABLED),
        schedule_enabled=overrides.pop('schedule_enabled', True),
        schedule_cron=overrides.pop('schedule_cron', '0 * * * *'),
        schedule_timezone=overrides.pop('schedule_timezone', 'UTC'),
        is_running=overrides.pop('is_running', False),
        last_error=overrides.pop('last_error', None),
        next_run_at=overrides.pop('next_run_at', None),
    )
    defaults.update(overrides)
    return Integration(**defaults)


# ───────────────────────────────────────────────────────────────────────────
# test_connection bug: failure must not flip status to ERROR
# ───────────────────────────────────────────────────────────────────────────

class TestTestConnectionDoesNotAutoDisable:
    """A failing test_connection used to set status=ERROR which made
    is_enabled() return False; the scheduler then skipped every run and
    next_run_at went stale → UI showed "Overdue" forever."""

    def test_failed_test_does_not_set_status_to_error(self, integration_svc, integration_id):
        integration_svc.repository.get_by_id.return_value = _enabled_integration(id=integration_id)

        with patch(
            'src.modules.integrations.services.integration_service.ConnectorRegistry'
        ) as registry:
            connector = MagicMock()
            connector.test_connection.return_value = {
                'success': False,
                'message': 'Connection refused',
            }
            registry.create_connector.return_value = connector

            integration_svc.test_connection(integration_id)

        update_call = integration_svc.repository.update.call_args
        updates = update_call[0][1] if len(update_call[0]) > 1 else update_call.kwargs.get('updates') or update_call[0][1]
        assert 'status' not in updates, (
            f"test_connection failure must NOT change integration.status; "
            f"got updates={updates}"
        )
        assert updates['last_connection_test_status'] == 'error'

    def test_exception_during_test_does_not_set_status_to_error(self, integration_svc, integration_id):
        integration_svc.repository.get_by_id.return_value = _enabled_integration(id=integration_id)

        with patch(
            'src.modules.integrations.services.integration_service.ConnectorRegistry'
        ) as registry:
            registry.create_connector.side_effect = RuntimeError("DNS lookup failed")

            integration_svc.test_connection(integration_id)

        update_call = integration_svc.repository.update.call_args
        updates = update_call[0][1]
        assert 'status' not in updates, (
            "An exception during test_connection (e.g. transient DNS) must "
            "not flip the integration to ERROR — that breaks the schedule."
        )
        assert updates['last_connection_test_status'] == 'error'
        assert 'DNS lookup failed' in updates['last_error']

    def test_successful_test_clears_last_error(self, integration_svc, integration_id):
        integration_svc.repository.get_by_id.return_value = _enabled_integration(
            id=integration_id, last_error='old failure'
        )

        with patch(
            'src.modules.integrations.services.integration_service.ConnectorRegistry'
        ) as registry:
            connector = MagicMock()
            connector.test_connection.return_value = {
                'success': True, 'message': 'ok'
            }
            registry.create_connector.return_value = connector

            integration_svc.test_connection(integration_id)

        updates = integration_svc.repository.update.call_args[0][1]
        assert updates['last_error'] is None
        assert updates['last_connection_test_status'] == 'success'


# ───────────────────────────────────────────────────────────────────────────
# enable_integration must reset stale state
# ───────────────────────────────────────────────────────────────────────────

class TestEnableIntegrationResetsStaleState:
    """After re-enabling a previously-failed integration, the UI must
    not keep showing the old error or "Overdue" next-run."""

    def test_clears_last_error_and_advances_next_run(self, integration_svc, integration_id):
        stale_next_run = datetime.now(timezone.utc) - timedelta(hours=2)
        integration_svc.repository.get_by_id.return_value = _enabled_integration(
            id=integration_id,
            status=IntegrationStatus.DISABLED,
            last_error='previous failure',
            next_run_at=stale_next_run,
        )
        integration_svc.repository.update.return_value = MagicMock()

        result = integration_svc.enable_integration(integration_id)

        assert result is True
        updates = integration_svc.repository.update.call_args[0][1]
        assert updates['status'] == IntegrationStatus.ENABLED
        assert updates['last_error'] is None
        # next_run_at should be advanced to a future time, not left stale
        assert 'next_run_at' in updates
        assert updates['next_run_at'] > datetime.now(timezone.utc)

    def test_no_schedule_means_no_next_run_update(self, integration_svc, integration_id):
        integration_svc.repository.get_by_id.return_value = _enabled_integration(
            id=integration_id,
            status=IntegrationStatus.DISABLED,
            schedule_enabled=False,
            schedule_cron=None,
        )
        integration_svc.repository.update.return_value = MagicMock()

        integration_svc.enable_integration(integration_id)

        updates = integration_svc.repository.update.call_args[0][1]
        assert 'next_run_at' not in updates

    def test_returns_false_when_integration_missing(self, integration_svc, integration_id):
        integration_svc.repository.get_by_id.return_value = None
        assert integration_svc.enable_integration(integration_id) is False
        integration_svc.repository.update.assert_not_called()


# ───────────────────────────────────────────────────────────────────────────
# Scheduler must advance next_run_at on EVERY fire, even on skip paths
# ───────────────────────────────────────────────────────────────────────────

class TestSchedulerAdvancesNextRunOnSkip:
    """A single skip used to leave next_run_at stale forever — UI then
    showed "Overdue" and the integration never recovered."""

    def test_disabled_integration_still_advances_next_run(self, scheduler_svc, integration_id):
        scheduler_svc.repository.get_by_id.return_value = _enabled_integration(
            id=integration_id,
            status=IntegrationStatus.DISABLED,
        )

        scheduler_svc._execute_integration_robust(integration_id, 'Test')

        update = _last_next_run_update(scheduler_svc)
        assert update is not None, (
            "Even when skipping a disabled integration, next_run_at must "
            "advance so the UI doesn't show 'Overdue' forever."
        )
        assert update['next_run_at'] > datetime.now(timezone.utc)
        # is_running must NOT appear in the skip-path update — we have no
        # reason to clear it (a concurrent run in another process might
        # legitimately hold it).
        assert 'is_running' not in update

    def test_already_running_still_advances_next_run(self, scheduler_svc, integration_id):
        scheduler_svc.repository.get_by_id.return_value = _enabled_integration(
            id=integration_id,
            is_running=True,
        )

        scheduler_svc._execute_integration_robust(integration_id, 'Test')

        update = _last_next_run_update(scheduler_svc)
        assert update is not None
        assert update['next_run_at'] > datetime.now(timezone.utc)
        # Live concurrent run owns is_running; outer finally must leave it.
        assert 'is_running' not in update

    def test_run_failure_still_advances_next_run(self, scheduler_svc, integration_id):
        scheduler_svc.repository.get_by_id.return_value = _enabled_integration(
            id=integration_id
        )
        # run_integration raises (simulating a connector error that
        # escapes the try/except inside run_integration)
        scheduler_svc.job_executor.execute_with_retry.side_effect = RuntimeError("boom")

        scheduler_svc._execute_integration_robust(integration_id, 'Test')

        update = _last_next_run_update(scheduler_svc)
        assert update is not None
        assert update['next_run_at'] > datetime.now(timezone.utc)

    def test_run_returns_failure_dict_advances_next_run(self, scheduler_svc, integration_id):
        scheduler_svc.repository.get_by_id.return_value = _enabled_integration(
            id=integration_id
        )
        scheduler_svc.job_executor.execute_with_retry.return_value = {
            'success': False,
            'message': 'Auth failed',
            'error': 'HTTPError',
        }

        scheduler_svc._execute_integration_robust(integration_id, 'Test')

        update = _last_next_run_update(scheduler_svc)
        assert update is not None
        assert update['next_run_at'] > datetime.now(timezone.utc)


# ───────────────────────────────────────────────────────────────────────────
# Scheduler must treat run_integration's failure dict as a failure
# ───────────────────────────────────────────────────────────────────────────

class TestSchedulerChecksRunResultSuccess:
    """run_integration() catches connector exceptions and returns
    {'success': False, ...} rather than re-raising. The scheduler used
    to record this as a successful run, inverting all the failure
    metrics and skipping the failure log entry."""

    def test_failure_dict_increments_jobs_failed(self, scheduler_svc, integration_id):
        scheduler_svc.repository.get_by_id.return_value = _enabled_integration(
            id=integration_id
        )
        scheduler_svc.job_executor.execute_with_retry.return_value = {
            'success': False,
            'message': 'Auth failed',
            'details': 'HTTP 401',
        }

        scheduler_svc._execute_integration_robust(integration_id, 'Test')

        assert scheduler_svc.metrics['jobs_failed'] == 1
        assert scheduler_svc.metrics['jobs_executed'] == 0
        assert scheduler_svc.metrics['last_error'] is not None

    def test_failure_dict_logs_failure_status(self, scheduler_svc, integration_id):
        scheduler_svc.repository.get_by_id.return_value = _enabled_integration(
            id=integration_id
        )
        scheduler_svc.job_executor.execute_with_retry.return_value = {
            'success': False,
            'message': 'Auth failed',
        }

        with patch.object(scheduler_svc, '_log_job_execution') as log_mock:
            scheduler_svc._execute_integration_robust(integration_id, 'Test')

        # The handler logs the failure with status='failed'
        statuses = [
            kw.get('status') for _, kw in log_mock.call_args_list
        ]
        assert 'failed' in statuses, (
            f"Expected at least one 'failed' status in {statuses}"
        )
        assert 'success' not in statuses

    def test_success_dict_increments_jobs_executed(self, scheduler_svc, integration_id):
        scheduler_svc.repository.get_by_id.return_value = _enabled_integration(
            id=integration_id
        )
        scheduler_svc.job_executor.execute_with_retry.return_value = {
            'success': True,
            'record_count': 42,
        }

        scheduler_svc._execute_integration_robust(integration_id, 'Test')

        assert scheduler_svc.metrics['jobs_executed'] == 1
        assert scheduler_svc.metrics['jobs_failed'] == 0


# ───────────────────────────────────────────────────────────────────────────
# Bug D: scheduler finally block must use CURRENT integration state
# ───────────────────────────────────────────────────────────────────────────

class TestSchedulerFinallyRefetchesIntegration:
    """If the user unschedules / changes the cron during a run, the
    finally block must use the CURRENT state — not the snapshot fetched
    at the start of the run — so it doesn't resurrect a just-disabled
    schedule or use the OLD cron."""

    def test_unschedule_during_run_clears_next_run(self, scheduler_svc, integration_id):
        # Snapshot at start of run: scheduled & enabled
        snapshot = _enabled_integration(id=integration_id)
        # State after run finishes: schedule_enabled flipped to False
        # (user unscheduled mid-run)
        after = _enabled_integration(
            id=integration_id, schedule_enabled=False, schedule_cron=None
        )
        scheduler_svc.repository.get_by_id.side_effect = [snapshot, after]
        scheduler_svc.job_executor.execute_with_retry.return_value = {
            'success': True, 'record_count': 1
        }

        scheduler_svc._execute_integration_robust(integration_id, 'Test')

        # Locate the finally update
        update = None
        for call in scheduler_svc.repository.update.call_args_list:
            if 'next_run_at' in call[0][1]:
                update = call[0][1]
        assert update is not None
        assert update['next_run_at'] is None, (
            "Finally must use current state: schedule was disabled during the "
            "run, so next_run_at should be cleared, not resurrected."
        )

    def test_cron_change_during_run_uses_new_cron(self, scheduler_svc, integration_id):
        snapshot = _enabled_integration(
            id=integration_id, schedule_cron='0 * * * *'
        )
        # Cron changed to every-minute during the run
        after = _enabled_integration(
            id=integration_id, schedule_cron='* * * * *'
        )
        scheduler_svc.repository.get_by_id.side_effect = [snapshot, after]
        scheduler_svc.job_executor.execute_with_retry.return_value = {
            'success': True, 'record_count': 1
        }

        scheduler_svc._execute_integration_robust(integration_id, 'Test')

        update = None
        for call in scheduler_svc.repository.update.call_args_list:
            if 'next_run_at' in call[0][1]:
                update = call[0][1]
        # Every-minute cron → next run is < 60 seconds away
        delta = (update['next_run_at'] - datetime.now(timezone.utc)).total_seconds()
        assert 0 < delta <= 60, (
            f"Should use the NEW cron (every minute → <60s), got delta={delta}s"
        )


# ───────────────────────────────────────────────────────────────────────────
# Bug B: disable_integration unschedules + clears next_run_at
# ───────────────────────────────────────────────────────────────────────────

class TestDisableIntegrationStopsScheduling:

    def test_disable_clears_next_run_at_and_signals(self, integration_svc, integration_id):
        integration_svc.repository.update.return_value = MagicMock()

        with patch(
            'src.modules.integrations.services.integration_service.signal_schedule_change'
        ) as signal_mock:
            result = integration_svc.disable_integration(integration_id)

        assert result is True
        updates = integration_svc.repository.update.call_args[0][1]
        assert updates['status'] == IntegrationStatus.DISABLED
        assert updates['next_run_at'] is None, (
            "Disabling must clear next_run_at so the UI doesn't show a "
            "phantom future run for a disabled integration."
        )
        signal_mock.assert_called_once_with(integration_id)


# ───────────────────────────────────────────────────────────────────────────
# Bug C: update_integration signals scheduler when schedule fields change
# ───────────────────────────────────────────────────────────────────────────

class TestUpdateIntegrationSignalsOnScheduleChange:

    def test_schedule_cron_change_signals_scheduler(self, integration_svc, integration_id):
        integration_svc.repository.update.return_value = MagicMock()

        with patch(
            'src.modules.integrations.services.integration_service.signal_schedule_change'
        ) as signal_mock:
            integration_svc.update_integration(
                integration_id, {'schedule_cron': '*/5 * * * *'}
            )

        signal_mock.assert_called_once_with(integration_id)

    def test_unrelated_change_does_not_signal(self, integration_svc, integration_id):
        integration_svc.repository.update.return_value = MagicMock()

        with patch(
            'src.modules.integrations.services.integration_service.signal_schedule_change'
        ) as signal_mock:
            integration_svc.update_integration(
                integration_id, {'name': 'Renamed'}
            )

        signal_mock.assert_not_called()


# ───────────────────────────────────────────────────────────────────────────
# Bug G: list_integrations hides wizard temp rows
# ───────────────────────────────────────────────────────────────────────────

class TestWizardTempIntegrationFilter:

    def test_default_excludes_test_prefix(self, integration_svc):
        normal = _enabled_integration(name='Production Jamf')
        temp = _enabled_integration(name='__test_1715378400000')
        integration_svc.repository.list_all.return_value = [normal, temp]

        result = integration_svc.list_integrations()

        names = [i.name for i in result]
        assert 'Production Jamf' in names
        assert '__test_1715378400000' not in names

    def test_include_wizard_temp_returns_them(self, integration_svc):
        normal = _enabled_integration(name='Production Jamf')
        temp = _enabled_integration(name='__test_1715378400000')
        integration_svc.repository.list_all.return_value = [normal, temp]

        result = integration_svc.list_integrations(include_wizard_temp=True)
        names = [i.name for i in result]
        assert '__test_1715378400000' in names


# ───────────────────────────────────────────────────────────────────────────
# is_running is owned by run_integration; scheduler must never write it
# ───────────────────────────────────────────────────────────────────────────

class TestSchedulerNeverWritesIsRunning:

    def test_run_path_does_not_touch_is_running(self, scheduler_svc, integration_id):
        scheduler_svc.repository.get_by_id.return_value = _enabled_integration(
            id=integration_id, is_running=False
        )
        scheduler_svc.job_executor.execute_with_retry.return_value = {
            'success': True, 'record_count': 1
        }

        scheduler_svc._execute_integration_robust(integration_id, 'Test')

        is_running_updates = [
            call[0][1].get('is_running')
            for call in scheduler_svc.repository.update.call_args_list
            if 'is_running' in call[0][1]
        ]
        assert is_running_updates == []


# ───────────────────────────────────────────────────────────────────────────
# cleanup_stale_running_flags
# ───────────────────────────────────────────────────────────────────────────

class TestCleanupStaleRunningFlags:
    """The advisory-lock probe is the source of truth for "currently
    running" — flags whose lock can be acquired are stale (their owning
    process crashed); flags whose lock can't be acquired belong to a
    live session and must be left alone."""

    def _make_svc_with_lock_conn(self, rows, lock_responses):
        """Build a service with a mocked dedicated lock connection.

        rows: list of (id,) tuples returned by SELECT ... is_running=TRUE
        lock_responses: list of bools — pg_try_advisory_lock results,
            one per row in order.
        """
        with patch.object(IntegrationService, '__init__', lambda self, **kw: None):
            svc = IntegrationService()
            svc.repository = MagicMock()
            svc.conn_manager = MagicMock()
            svc.conn_manager.database_url = 'postgresql://test'

        cursor = MagicMock()
        # First fetchall = the SELECT result; subsequent fetchone() calls
        # return advisory-lock probe results, alternating with UPDATE
        # rowcount which we don't check.
        cursor.fetchall.return_value = rows
        cursor.fetchone.side_effect = [(b,) for b in lock_responses]
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)

        conn = MagicMock()
        conn.cursor.return_value = cursor

        return svc, conn, cursor

    def test_stale_flag_is_cleared_when_lock_acquirable(self, integration_id):
        svc, conn, cursor = self._make_svc_with_lock_conn(
            rows=[(str(integration_id),)],
            lock_responses=[True],  # lock acquired → flag is stale
        )

        with patch(
            'src.modules.integrations.services.integration_service.psycopg2.connect',
            return_value=conn,
        ):
            cleared = svc.cleanup_stale_running_flags()

        assert cleared == 1
        # An UPDATE clearing the flag must have been issued
        update_calls = [
            c for c in cursor.execute.call_args_list
            if c[0][0].strip().upper().startswith('UPDATE')
        ]
        assert len(update_calls) == 1, (
            f"Expected exactly one UPDATE; got {len(update_calls)}"
        )

    def test_live_run_flag_is_left_alone(self, integration_id):
        svc, conn, cursor = self._make_svc_with_lock_conn(
            rows=[(str(integration_id),)],
            lock_responses=[False],  # lock held → live run, leave flag
        )

        with patch(
            'src.modules.integrations.services.integration_service.psycopg2.connect',
            return_value=conn,
        ):
            cleared = svc.cleanup_stale_running_flags()

        assert cleared == 0
        update_calls = [
            c for c in cursor.execute.call_args_list
            if c[0][0].strip().upper().startswith('UPDATE')
        ]
        assert update_calls == [], (
            "Must not clear is_running for an integration whose advisory "
            f"lock is held by a live session; got UPDATE: {update_calls}"
        )

    def test_closes_dedicated_connection(self, integration_id):
        # Closing the dedicated connection releases all session locks;
        # leaking it would leak any held advisory locks too.
        svc, conn, cursor = self._make_svc_with_lock_conn(
            rows=[(str(integration_id),)],
            lock_responses=[True],
        )

        with patch(
            'src.modules.integrations.services.integration_service.psycopg2.connect',
            return_value=conn,
        ):
            svc.cleanup_stale_running_flags()

        conn.close.assert_called()
