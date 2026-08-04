# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for PipelineService — pipeline execution lifecycle.

Mocks: PostgreSQLConnectionManager, AssetGenerationTriggerService, PostgreSQLAdapter
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4
from datetime import datetime, timedelta, timezone

import pytest

from src.modules.pipeline.pipeline_service import PipelineService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_service():
    """Create a PipelineService with mocked dependencies."""
    conn_mgr = MagicMock()
    trigger_svc = MagicMock()
    with patch(
        'src.modules.pipeline.pipeline_service.PostgreSQLAdapter'
    ) as MockAdapter:
        mock_db = MagicMock()
        MockAdapter.return_value = mock_db
        svc = PipelineService(
            connection_manager=conn_mgr,
            trigger_service=trigger_svc,
        )
    return svc, mock_db, trigger_svc


# ===========================================================================
# Tests
# ===========================================================================


class TestSetCurrentStage:
    """Live current_stage is written on an autonomous (unscoped) connection so
    it commits DURING the scoped pipeline run (#800 live-status regression)."""

    def test_writes_via_autonomous_connection(self):
        svc, _, _ = _build_service()
        run_id = uuid4()
        svc._set_current_stage(run_id, "Scoring")
        svc.connection_manager.execute_autonomous.assert_called_once()
        args = svc.connection_manager.execute_autonomous.call_args.args
        assert "current_stage" in args[0]
        assert args[1] == ("Scoring", str(run_id))

    def test_does_not_use_scoped_adapter(self):
        # Must NOT go through the scoped adapter (db.execute_query) — that write
        # is deferred to end_scope, so the UI would see a frozen stage all run.
        svc, mock_db, _ = _build_service()
        svc._set_current_stage(uuid4(), "Scoring")
        mock_db.execute_query.assert_not_called()

    def test_write_is_guarded_on_the_row_still_being_unfinished(self):
        # Once the watchdog has written the row to 'failed', a still-alive
        # worker's stage write must not put a live stage back on a finished run.
        svc, _, _ = _build_service()
        svc._set_current_stage(uuid4(), "Scoring")
        sql = " ".join(
            svc.connection_manager.execute_autonomous.call_args.args[0].split()
        )
        assert "AND status IN ('pending', 'running')" in sql

    def test_swallows_db_errors(self):
        # A live-status write failure must never abort the pipeline run.
        svc, _, _ = _build_service()
        svc.connection_manager.execute_autonomous.side_effect = RuntimeError("db down")
        svc._set_current_stage(uuid4(), "Scoring")  # must not raise



class TestTriggerRun:
    """Tests for trigger_run()."""

    def test_success(self):
        svc, db, _ = _build_service()
        run_id_row = {'id': str(uuid4())}
        db.execute_query.return_value = [run_id_row]

        with patch('threading.Thread') as MockThread:
            mock_thread = MagicMock()
            MockThread.return_value = mock_thread
            result = svc.trigger_run(triggered_by='manual')

        assert result['success'] is True
        assert 'run_id' in result
        mock_thread.start.assert_called_once()

    def test_commits_run_record_before_starting_worker(self):
        """The pending run row must be committed before the worker thread spawns.

        Manual triggers execute inside the per-request DB scope installed in
        factory.py (before_request begin_scope / teardown_request end_scope), so
        the INSERT that creates the pending pipeline_runs row is buffered on the
        request's connection and stays invisible to other connections until the
        request ends. The background worker (_execute_run) runs on its OWN
        pooled connection and immediately issues
        ``UPDATE pipeline_runs SET status='running', started_at=... WHERE id=…``.
        If the INSERT hasn't committed yet that UPDATE matches zero rows, so
        started_at stays NULL forever — which also freezes the live "Elapsed"
        timer, since get_current_status derives elapsed_seconds from started_at.

        So trigger_run must end the request scope (commit the row) BEFORE
        spawning the worker. Mirrors the chat-stream fix (#735) for the same
        request-scope / worker-thread interaction.
        """
        svc, db, _ = _build_service()
        db.execute_query.return_value = [{'id': str(uuid4())}]
        conn_mgr = svc.connection_manager

        order = []
        conn_mgr.end_scope.side_effect = lambda *a, **k: order.append('end_scope')

        with patch('threading.Thread') as MockThread:
            mock_thread = MagicMock()
            mock_thread.start.side_effect = lambda *a, **k: order.append('thread_start')
            MockThread.return_value = mock_thread
            result = svc.trigger_run(triggered_by='manual')

        assert result['success'] is True
        assert order == ['end_scope', 'thread_start'], (
            "the run record must be committed (request scope ended) before the "
            f"worker thread starts; got call order {order}"
        )

    def test_already_running_returns_409_shape(self):
        svc, db, _ = _build_service()

        existing_run_id = str(uuid4())
        # First call: atomic INSERT returns empty (blocked)
        # Second call: SELECT active run
        db.execute_query.side_effect = [
            [],  # INSERT ... WHERE NOT EXISTS → no rows
            [{'id': existing_run_id}],  # active run lookup
        ]

        result = svc.trigger_run(triggered_by='manual')

        assert result['success'] is False
        assert 'already running' in result['error']
        assert result['run_id'] == existing_run_id

    def test_already_running_no_active_found(self):
        """Race condition: INSERT blocked but active run disappeared."""
        svc, db, _ = _build_service()
        db.execute_query.side_effect = [
            [],  # INSERT blocked
            [],  # active run query returns nothing (race)
        ]

        result = svc.trigger_run(triggered_by='schedule')

        assert result['success'] is False
        assert result['run_id'] is None


class TestGetCurrentStatus:
    """Tests for get_current_status()."""

    def test_running_state(self):
        svc, db, _ = _build_service()
        started = datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc)
        db.execute_query.return_value = [{
            'id': uuid4(),
            'status': 'running',
            'current_stage': 'normalization',
            'started_at': started,
            'triggered_by': 'manual',
        }]

        result = svc.get_current_status()

        assert result['status'] == 'running'
        assert result['current_stage'] == 'normalization'
        assert result['triggered_by'] == 'manual'
        assert result['elapsed_seconds'] is not None
        assert result['elapsed_seconds'] > 0

    def test_idle_with_last_run(self):
        svc, db, _ = _build_service()
        completed = datetime(2026, 2, 1, 13, 0, 0, tzinfo=timezone.utc)

        # First query: no active run
        # Second query: last completed run (JSON sub-paths, not whole stats)
        db.execute_query.side_effect = [
            [],  # no active
            [{
                'id': uuid4(),
                'status': 'completed',
                'completed_at': completed,
                'duration_seconds': 45.5,
                'triggered_by': 'schedule',
                'total_assets_created': 100,
                'total_assets_updated': 50,
                'chronicle_events': None,
            }],
        ]

        result = svc.get_current_status()

        assert result['status'] == 'idle'
        assert result['last_run']['status'] == 'completed'
        assert result['last_run']['duration_seconds'] == 45.5
        assert result['last_run']['triggered_by'] == 'schedule'

    def test_idle_last_run_splits_created_into_new_and_returned(self):
        """Idle last_run reports New (asset_created) and Returned (asset_readded)
        separately, mirroring the Run History table split (PR #794). The stored
        combined total_assets_created is never surfaced as the headline count."""
        svc, db, _ = _build_service()
        completed = datetime(2026, 2, 1, 13, 0, 0, tzinfo=timezone.utc)
        db.execute_query.side_effect = [
            [],  # no active run
            [{
                'id': uuid4(),
                'status': 'completed',
                'completed_at': completed,
                'duration_seconds': 45.5,
                'triggered_by': 'schedule',
                'total_assets_created': 1403,  # combined (untouched)
                'total_assets_updated': 50,
                'chronicle_events': {'asset_created': 319,
                                     'asset_readded': 1084},
            }],
        ]

        last_run = svc.get_current_status()['last_run']

        assert last_run['assets_created'] == 319    # new only
        assert last_run['assets_readded'] == 1084   # returned
        assert last_run['assets_updated'] == 50

    def test_idle_last_run_legacy_run_falls_back_to_combined(self):
        """A run predating chronicle_events keeps the old combined count as New
        with zero Returned, so legacy history still renders."""
        svc, db, _ = _build_service()
        db.execute_query.side_effect = [
            [],  # no active run
            [{
                'id': uuid4(),
                'status': 'completed',
                'completed_at': None,
                'duration_seconds': None,
                'triggered_by': 'manual',
                'total_assets_created': 7,
                'total_assets_updated': 3,
                'chronicle_events': None,
            }],
        ]

        last_run = svc.get_current_status()['last_run']

        assert last_run['assets_created'] == 7   # combined fallback
        assert last_run['assets_readded'] == 0

    def test_idle_no_previous_runs(self):
        svc, db, _ = _build_service()
        db.execute_query.side_effect = [[], []]  # no active, no history

        result = svc.get_current_status()

        assert result['status'] == 'idle'
        assert 'last_run' not in result

    def test_pending_state(self):
        svc, db, _ = _build_service()
        db.execute_query.return_value = [{
            'id': uuid4(),
            'status': 'pending',
            'current_stage': None,
            'started_at': None,
            'triggered_by': 'manual',
        }]

        result = svc.get_current_status()

        assert result['status'] == 'pending'
        assert result['elapsed_seconds'] is None


class TestGetRun:
    """get_run() must expose the New/Returned split (feeds the UI RunDetailPanel
    via /pipeline/runs/<id>), consistent with get_runs and get_current_status."""

    def test_splits_created_and_readded_from_chronicle_events(self):
        svc, db, _ = _build_service()
        db.execute_query.return_value = [{
            'id': uuid4(), 'triggered_by': 'schedule', 'status': 'completed',
            'current_stage': None, 'started_at': None, 'completed_at': None,
            'duration_seconds': None, 'error_message': None, 'created_at': None,
            'stats': {
                'total_assets_created': 1403,  # combined (untouched)
                'total_assets_updated': 50,
                'chronicle_events': {'asset_created': 319,
                                     'asset_readded': 1084},
            },
        }]

        run = svc.get_run(uuid4())

        assert run['assets_created'] == 319    # new only, not combined 1403
        assert run['assets_readded'] == 1084   # returned
        assert run['assets_updated'] == 50

    def test_legacy_run_without_chronicle_events_falls_back(self):
        svc, db, _ = _build_service()
        db.execute_query.return_value = [{
            'id': uuid4(), 'triggered_by': 'manual', 'status': 'completed',
            'current_stage': None, 'started_at': None, 'completed_at': None,
            'duration_seconds': None, 'error_message': None, 'created_at': None,
            'stats': {'total_assets_created': 7, 'total_assets_updated': 3},
        }]

        run = svc.get_run(uuid4())

        assert run['assets_created'] == 7   # combined fallback
        assert run['assets_readded'] == 0


class TestGetRuns:
    """Tests for get_runs()."""

    def test_empty_history(self):
        svc, db, _ = _build_service()
        db.execute_query.side_effect = [
            [{'total': 0}],  # count
            [],              # no runs
        ]

        result = svc.get_runs(page=1, per_page=20)

        assert result['runs'] == []
        assert result['total'] == 0
        assert result['page'] == 1
        assert result['pages'] == 0

    def test_pagination(self):
        svc, db, _ = _build_service()
        completed = datetime(2026, 2, 1, 13, 0, 0, tzinfo=timezone.utc)

        db.execute_query.side_effect = [
            [{'total': 25}],
            [
                {
                    'id': uuid4(),
                    'triggered_by': 'manual',
                    'status': 'completed',
                    'started_at': completed,
                    'completed_at': completed,
                    'duration_seconds': 30.0,
                    'stats': {'total_assets': 500, 'total_assets_created': 10,
                              'total_assets_updated': 20, 'batches_processed': 3},
                    'error_message': None,
                    'created_at': completed,
                }
            ],
        ]

        result = svc.get_runs(page=1, per_page=10)

        assert result['total'] == 25
        assert result['pages'] == 3  # ceil(25/10)
        assert len(result['runs']) == 1
        run = result['runs'][0]
        assert run['status'] == 'completed'
        assert run['total_assets'] == 500
        assert run['assets_created'] == 10

    def test_run_with_no_stats(self):
        svc, db, _ = _build_service()
        created = datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc)

        db.execute_query.side_effect = [
            [{'total': 1}],
            [{
                'id': uuid4(),
                'triggered_by': 'schedule',
                'status': 'failed',
                'started_at': created,
                'completed_at': created,
                'duration_seconds': 5.0,
                'stats': None,
                'error_message': 'No batches found',
                'created_at': created,
            }],
        ]

        result = svc.get_runs(page=1, per_page=20)
        run = result['runs'][0]
        assert run['total_assets'] == 0
        assert run['assets_created'] == 0
        assert run['error_message'] == 'No batches found'

    def test_stats_as_json_string(self):
        """stats column may arrive as a JSON string."""
        svc, db, _ = _build_service()
        created = datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc)

        db.execute_query.side_effect = [
            [{'total': 1}],
            [{
                'id': uuid4(),
                'triggered_by': 'manual',
                'status': 'completed',
                'started_at': created,
                'completed_at': created,
                'duration_seconds': 10.0,
                'stats': '{"total_assets": 200, "total_assets_created": 5, "total_assets_updated": 3, "batches_processed": 2}',
                'error_message': None,
                'created_at': created,
            }],
        ]

        result = svc.get_runs(page=1, per_page=20)
        run = result['runs'][0]
        assert run['total_assets'] == 200

    def test_filters_and_sort_build_where_and_order(self):
        """status/date filters and sort reach the SQL (count uses them too, so
        `total` reflects the filtered set, not the whole table)."""
        svc, db, _ = _build_service()
        db.execute_query.side_effect = [
            [{'total': 2}],   # filtered count
            [],               # page rows (irrelevant here)
        ]
        start = datetime(2026, 4, 1, tzinfo=timezone.utc)

        svc.get_runs(page=1, per_page=10, status='failed',
                     start_date=start, sort='duration', sort_dir='asc')

        count_sql, count_params = db.execute_query.call_args_list[0].args[:2]
        page_sql, page_params = db.execute_query.call_args_list[1].args[:2]

        # Both COUNT and page query carry the same filters.
        assert 'status = %s' in count_sql
        assert 'created_at >= %s' in count_sql
        assert 'failed' in count_params and start in count_params
        # Sort key maps to its whitelisted column + direction.
        assert 'ORDER BY duration_seconds ASC' in page_sql
        # Page query params end with (per_page, offset).
        assert page_params[-2:] == (10, 0)

    def test_unknown_sort_falls_back_to_created_column(self):
        """Defense-in-depth: even if an unvalidated sort reaches the service,
        it maps through the whitelist (no raw interpolation)."""
        svc, db, _ = _build_service()
        db.execute_query.side_effect = [[{'total': 0}], []]
        svc.get_runs(sort="'; DROP TABLE pipeline_runs; --")
        page_sql = db.execute_query.call_args_list[1].args[0]
        assert 'ORDER BY created_at DESC' in page_sql
        assert 'DROP TABLE' not in page_sql


class TestSanitizeStats:
    """Tests for _sanitize_stats() — JSON-safe conversion."""

    def test_minimal_result(self):
        svc, _, _ = _build_service()

        result = {
            'success': True,
            'batches_processed': 2,
            'batches_failed': 0,
            'total_assets': 100,
            'total_assets_created': 10,
            'total_assets_updated': 5,
            'duration_seconds': 30.5,
            'stats': {},
            'batch_results': [],
        }

        stats = svc._sanitize_stats(result)

        assert stats['batches_processed'] == 2
        assert stats['total_assets'] == 100
        assert stats['total_assets_created'] == 10
        assert stats['batch_results'] == []
        assert stats['stage_timings'] == []

    def test_lifts_grace_anomaly_and_confidence_to_top_level(self):
        # #584 §4: anomaly baselines (A2 onsets, A6 confidence) accumulate from
        # pipeline_runs.stats, so the grace + anomaly stage summaries are lifted.
        svc, _, _ = _build_service()
        result = {
            'success': True, 'batches_processed': 1, 'batches_failed': 0,
            'total_assets': 100, 'total_assets_created': 10, 'total_assets_updated': 5,
            'duration_seconds': 1.0, 'batch_results': [],
            'stats': {'stages': {
                'grace_carry_forward': {'carried_forward': 42, 'missing_onsets': 7},
                'anomaly_detection': {
                    'rules_evaluated': 7, 'alerts_created': 2, 'alerts_updated': 1,
                    'alerts_resolved': 0, 'errors': {}, 'learning': [],
                    'shadow': {'anomaly-ml-newcomer': {'count': 3, 'top': []}},
                    'confidence_histogram': {'low': 3, 'medium': 4, 'high': 5}},
            }},
        }
        stats = svc._sanitize_stats(result)
        assert stats['grace_missing_onsets'] == 7
        assert stats['grace_carried_total'] == 42
        assert stats['anomaly']['rules_evaluated'] == 7
        assert stats['anomaly']['alerts_created'] == 2
        assert 'learning' not in stats['anomaly']    # only whitelisted keys lifted
        # #902: shadow mode is gone; a stray legacy `shadow` key in a stage summary
        # must NOT be lifted into pipeline_runs.stats.
        assert 'shadow' not in stats['anomaly']
        assert stats['confidence_histogram'] == {'low': 3, 'medium': 4, 'high': 5}

    def test_no_anomaly_or_grace_stages_leaves_stats_clean(self):
        svc, _, _ = _build_service()
        result = {
            'success': True, 'batches_processed': 0, 'batches_failed': 0,
            'total_assets': 0, 'total_assets_created': 0, 'total_assets_updated': 0,
            'duration_seconds': 0, 'batch_results': [], 'stats': {'stages': {}},
        }
        stats = svc._sanitize_stats(result)
        assert 'grace_missing_onsets' not in stats
        assert 'anomaly' not in stats and 'confidence_histogram' not in stats

    def test_with_stages_and_batches(self):
        svc, _, _ = _build_service()

        result = {
            'success': True,
            'batches_processed': 1,
            'batches_failed': 0,
            'total_assets': 50,
            'total_assets_created': 5,
            'total_assets_updated': 3,
            'duration_seconds': 15.0,
            'stats': {
                'input_records': 60,
                'excluded_records': 10,
                'removed_assets': 2,
                'stages': {
                    'aggregation': {'unique_assets': 50, 'duration': 1.234, 'label': 'Aggregation'},
                    'expansion': {'expanded_records': 70, 'duration': 0.5, 'label': 'Expand Rows'},
                    'chronicle': {
                        'duration': 0.3,
                        'label': 'Change Tracking',
                        'event_type_counts': {'created': 5, 'updated': 3},
                    },
                    'skipped_stage': {'skipped': True, 'duration': 0},
                },
                'per_source': {
                    'batch-001': {'unique_assets': 50, 'excluded': 10},
                },
            },
            'batch_results': [
                {
                    'batch_id': 'batch-001',
                    'success': True,
                    'input_records': 60,
                    'integration_name': 'Jamf',
                    'integration_type': 'jamf',
                    'error': None,
                },
            ],
        }

        stats = svc._sanitize_stats(result)

        assert stats['input_records'] == 60
        assert stats['excluded_records'] == 10
        assert stats['unique_assets'] == 50
        assert stats['expanded_records'] == 70
        assert stats['chronicle_events'] == {'created': 5, 'updated': 3}

        # Batch results enriched with per_source
        assert len(stats['batch_results']) == 1
        br = stats['batch_results'][0]
        assert br['integration_name'] == 'Jamf'
        assert br['unique_assets'] == 50
        assert br['excluded'] == 10

        # Stage timings: skipped stage excluded, names are human-readable labels
        names = [s['name'] for s in stats['stage_timings']]
        assert 'Aggregation' in names
        assert 'Expand Rows' in names
        assert 'skipped_stage' not in names

    def test_missing_keys_default_to_zero(self):
        svc, _, _ = _build_service()
        result = {'success': True, 'stats': {}, 'batch_results': []}

        stats = svc._sanitize_stats(result)

        assert stats['batches_processed'] == 0
        assert stats['total_assets'] == 0
        assert stats['input_records'] == 0
        assert stats['merged_assets'] == 0

    def test_persists_merged_assets(self):
        svc, _, _ = _build_service()
        result = {
            'success': True,
            'stats': {'removed_assets': 5, 'merged_assets': 2},
            'batch_results': [],
        }

        stats = svc._sanitize_stats(result)

        assert stats['merged_assets'] == 2
        assert stats['removed_assets'] == 5


class TestSyncSchedule:
    """Tests for sync_schedule() — pipeline schedule registration with APScheduler."""

    INTERVAL_4H = {
        'pipeline_schedule_enabled': True,
        'pipeline_schedule_mode': 'interval',
        'pipeline_schedule_interval_hours': 4,
    }
    INTERVAL_2H = {
        'pipeline_schedule_enabled': True,
        'pipeline_schedule_mode': 'interval',
        'pipeline_schedule_interval_hours': 2,
    }
    CRON_4H = {
        'pipeline_schedule_enabled': True,
        'pipeline_schedule_mode': 'cron',
        'pipeline_schedule_cron': '0 */4 * * *',
        'pipeline_schedule_timezone': 'UTC',
    }
    DISABLED = {'pipeline_schedule_enabled': False}

    def _patch_settings(self, svc, values):
        return patch.object(svc, '_get_setting', side_effect=lambda _c, k: values[k])

    def _seed_first_sync(self, svc, scheduler, settings):
        """Run an initial sync_schedule with no existing job, then reset mocks."""
        scheduler.get_job.return_value = None
        with self._patch_settings(svc, settings):
            svc.sync_schedule(scheduler)
        scheduler.add_job.reset_mock()
        scheduler.remove_job.reset_mock()

    def test_idempotent_when_settings_unchanged(self):
        """Re-syncing with the same settings must NOT replace the existing job.

        Regression: a 5-minute periodic re-sync called sync_schedule which
        unconditionally removed and re-added the job with a fresh
        IntervalTrigger, resetting next_run_time to "now + interval" each
        time. With a 4h interval and 5min re-sync, the pipeline never fired.
        """
        from apscheduler.triggers.interval import IntervalTrigger

        svc, _, _ = _build_service()
        scheduler = MagicMock()
        self._seed_first_sync(svc, scheduler, self.INTERVAL_4H)

        existing_job = MagicMock()
        existing_job.trigger = IntervalTrigger(hours=4)
        scheduler.get_job.return_value = existing_job

        with self._patch_settings(svc, self.INTERVAL_4H):
            svc.sync_schedule(scheduler)

        assert scheduler.add_job.call_count == 0
        assert scheduler.remove_job.call_count == 0

    def test_re_adds_when_interval_hours_changes(self):
        """When interval_hours changes, the job must be re-registered."""
        from apscheduler.triggers.interval import IntervalTrigger

        svc, _, _ = _build_service()
        scheduler = MagicMock()
        self._seed_first_sync(svc, scheduler, self.INTERVAL_4H)

        existing_job = MagicMock()
        existing_job.trigger = IntervalTrigger(hours=4)
        scheduler.get_job.return_value = existing_job

        with self._patch_settings(svc, self.INTERVAL_2H):
            svc.sync_schedule(scheduler)

        assert scheduler.add_job.call_count == 1

    def test_idempotent_when_cron_unchanged(self):
        """Re-syncing with the same cron settings must NOT replace the job."""
        from apscheduler.triggers.cron import CronTrigger

        svc, _, _ = _build_service()
        scheduler = MagicMock()
        self._seed_first_sync(svc, scheduler, self.CRON_4H)

        existing_job = MagicMock()
        existing_job.trigger = CronTrigger.from_crontab('0 */4 * * *')
        scheduler.get_job.return_value = existing_job

        with self._patch_settings(svc, self.CRON_4H):
            svc.sync_schedule(scheduler)

        assert scheduler.add_job.call_count == 0
        assert scheduler.remove_job.call_count == 0

    def test_re_adds_when_mode_changes_interval_to_cron(self):
        """Switching from interval mode to cron must re-register the job."""
        from apscheduler.triggers.interval import IntervalTrigger

        svc, _, _ = _build_service()
        scheduler = MagicMock()
        self._seed_first_sync(svc, scheduler, self.INTERVAL_4H)

        existing_job = MagicMock()
        existing_job.trigger = IntervalTrigger(hours=4)
        scheduler.get_job.return_value = existing_job

        with self._patch_settings(svc, self.CRON_4H):
            svc.sync_schedule(scheduler)

        assert scheduler.remove_job.call_count == 1
        assert scheduler.add_job.call_count == 1

    def test_removes_job_when_disabled_after_being_enabled(self):
        """Flipping enabled from True → False must remove the existing job."""
        from apscheduler.triggers.interval import IntervalTrigger

        svc, _, _ = _build_service()
        scheduler = MagicMock()
        self._seed_first_sync(svc, scheduler, self.INTERVAL_4H)

        existing_job = MagicMock()
        existing_job.trigger = IntervalTrigger(hours=4)
        scheduler.get_job.return_value = existing_job

        with self._patch_settings(svc, self.DISABLED):
            svc.sync_schedule(scheduler)

        assert scheduler.remove_job.call_count == 1
        assert scheduler.add_job.call_count == 0

    def test_idempotent_when_disabled_and_no_job_present(self):
        """Re-syncing while already disabled must be a complete no-op."""
        svc, _, _ = _build_service()
        scheduler = MagicMock()
        self._seed_first_sync(svc, scheduler, self.DISABLED)

        scheduler.get_job.return_value = None
        with self._patch_settings(svc, self.DISABLED):
            svc.sync_schedule(scheduler)

        assert scheduler.add_job.call_count == 0
        assert scheduler.remove_job.call_count == 0

    def test_re_adds_if_job_disappeared_despite_unchanged_config(self):
        """An externally removed job must be re-registered even when config matches cache."""
        svc, _, _ = _build_service()
        scheduler = MagicMock()
        self._seed_first_sync(svc, scheduler, self.INTERVAL_4H)

        scheduler.get_job.return_value = None
        with self._patch_settings(svc, self.INTERVAL_4H):
            svc.sync_schedule(scheduler)

        assert scheduler.add_job.call_count == 1

    def test_interval_trigger_anchored_to_last_completed_run(self):
        """A scheduler restart must NOT reset the interval cadence.

        IntervalTrigger with no start_date implicitly anchors at "now", so
        every restart (each redeploy) re-adds the job with next_run = now +
        interval. Frequent auto-updates then starve the schedule: on 113 three
        deploys in one day pushed a 4h pipeline from its 08:36 slot to 15:16 —
        a ~10h gap on a 4h cadence. Anchoring start_date to the last completed
        run keeps fire times on the fixed grid (last_run + k*interval), so a
        restart picks the next grid slot instead of resetting the clock.
        """
        from apscheduler.triggers.interval import IntervalTrigger

        svc, mock_db, _ = _build_service()
        scheduler = MagicMock()
        scheduler.get_job.return_value = None

        anchor = datetime(2026, 2, 1, 4, 0, 0, tzinfo=timezone.utc)
        # _get_setting is patched (bypasses the DB), so the interval path's only
        # DB read is the last-completed-run lookup that computes the anchor.
        mock_db.execute_query.return_value = [{'completed_at': anchor}]

        with self._patch_settings(svc, self.INTERVAL_4H):
            svc.sync_schedule(scheduler)

        trigger = scheduler.add_job.call_args.kwargs['trigger']
        assert isinstance(trigger, IntervalTrigger)
        # Anchored to the last run, not to "now".
        assert trigger.start_date == anchor

        # Consequence: the next fire sits on the fixed grid (anchor + interval)
        # and is identical whether the restart lands 10 min or 2 h after the
        # last run — the cadence never drifts forward across restarts.
        slot = anchor + timedelta(hours=4)
        assert trigger.get_next_fire_time(None, anchor + timedelta(minutes=10)) == slot
        assert trigger.get_next_fire_time(None, anchor + timedelta(hours=2)) == slot

    def test_interval_falls_back_to_now_when_no_completed_run(self):
        """Brand-new install (no completed runs yet): register the job with the
        default now+interval anchor rather than crashing on a missing anchor."""
        from apscheduler.triggers.interval import IntervalTrigger

        svc, mock_db, _ = _build_service()
        scheduler = MagicMock()
        scheduler.get_job.return_value = None
        mock_db.execute_query.return_value = []  # no prior runs

        with self._patch_settings(svc, self.INTERVAL_4H):
            svc.sync_schedule(scheduler)

        # No completed run → no anchor → the fallback (unanchored) path is taken.
        assert svc._interval_anchor() is None
        trigger = scheduler.add_job.call_args.kwargs['trigger']
        assert isinstance(trigger, IntervalTrigger)
        assert trigger.interval == timedelta(hours=4)
        # Unanchored: APScheduler defaults start_date to ~now, so the first fire
        # is in the future (now + interval), not a past grid slot.
        assert trigger.start_date > datetime(2026, 7, 1, tzinfo=timezone.utc)
