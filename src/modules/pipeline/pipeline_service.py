# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Pipeline Service

Manages pipeline execution lifecycle: triggering, tracking, status reporting,
and schedule synchronization. Runs the pipeline in a background thread and
updates pipeline_runs records with stage progress and final statistics.
"""

import logging
import threading
import time
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from uuid import UUID, uuid4

from src.database.postgres_connection import PostgreSQLConnectionManager
from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.integrations.services.asset_generation_trigger_service import AssetGenerationTriggerService

logger = logging.getLogger(__name__)


def _split_created_readded(stats: Dict[str, Any]) -> tuple:
    """Split a run's combined CREATED count into ``(new, returned)``.

    New = chronicle ``asset_created``; Returned = ``asset_readded``. Runs
    predating ``chronicle_events`` fall back to the old combined
    ``total_assets_created`` as new with zero returned. The ``or 0`` guards
    None/missing JSON values (e.g. a JSONB sub-path SELECT of an absent key).
    Display-only — the stored ``total_assets_created`` is never mutated (alert
    rules read ``stats->>'total_assets_created'``).

    Shared by get_runs/get_run/get_current_status and the MCP pipeline tools so
    every surface reports the same New/Returned split (issue #796).
    """
    stats = stats or {}
    chron = stats.get('chronicle_events') or {}
    if chron:
        created = chron.get('asset_created', 0) or 0
        readded = chron.get('asset_readded', 0) or 0
        return created, readded
    return stats.get('total_assets_created', 0) or 0, 0


class PipelineService:
    """
    Orchestrates pipeline execution with background threading and run tracking.
    """

    PIPELINE_SCHEDULE_JOB_ID = 'pipeline_auto_run'

    def __init__(
        self,
        connection_manager: PostgreSQLConnectionManager,
        trigger_service: AssetGenerationTriggerService
    ):
        self.connection_manager = connection_manager
        self.trigger_service = trigger_service
        self._db = PostgreSQLAdapter(connection_manager)
        # Tracks the last config applied to the scheduler so periodic re-syncs
        # can short-circuit when nothing changed (avoids resetting next_run_time).
        self._last_applied_schedule_config: Optional[tuple] = None
        logger.info("PipelineService initialized")

    def trigger_run(self, triggered_by: str = 'manual') -> Dict[str, Any]:
        """
        Start a pipeline run. Returns immediately with run_id.
        Rejects if another run is in progress.
        Uses atomic INSERT to prevent TOCTOU race conditions.
        """
        run_id = uuid4()

        # Atomic insert — only succeeds if no active run exists
        result = self._db.execute_query(
            """INSERT INTO pipeline_runs (id, triggered_by, status, created_at)
               SELECT %s, %s, 'pending', NOW()
               WHERE NOT EXISTS (
                   SELECT 1 FROM pipeline_runs WHERE status IN ('pending', 'running')
               )
               RETURNING id""",
            (str(run_id), triggered_by), fetch=True
        )

        if not result:
            # Insert was skipped — another run is active
            active = self._db.execute_query(
                "SELECT id FROM pipeline_runs WHERE status IN ('pending', 'running') LIMIT 1",
                (), fetch=True
            )
            return {
                'success': False,
                'error': 'Pipeline is already running',
                'run_id': str(active[0]['id']) if active else None
            }

        # Commit the pending row before spawning the worker. Manual triggers run
        # inside the per-request DB scope (factory.py before_request), so the
        # INSERT above is buffered on the request's connection and invisible to
        # other connections until teardown. The worker thread runs on its own
        # pooled connection and immediately does
        # `UPDATE pipeline_runs SET started_at=... WHERE id=run_id`; without this
        # commit that UPDATE races the request teardown and usually matches zero
        # rows, leaving started_at NULL (and the live elapsed timer frozen at 0s).
        # end_scope() commits + releases the request connection; it is a
        # re-entrant no-op outside a request (scheduler/CLI), where the INSERT
        # already auto-committed. Same scope/worker interaction as the chat-stream
        # fix (#735).
        self.connection_manager.end_scope()

        # Spawn background thread
        thread = threading.Thread(
            target=self._execute_run,
            args=(run_id,),
            name=f"pipeline-run-{run_id}",
            daemon=True
        )
        thread.start()

        logger.info(f"Pipeline run {run_id} triggered ({triggered_by})")
        return {'success': True, 'run_id': str(run_id)}

    def _execute_run(self, run_id: UUID) -> None:
        """Background thread target. Runs the pipeline and updates the DB."""
        # Use a separate DB adapter for this thread
        db = PostgreSQLAdapter(self.connection_manager)

        started_at = datetime.now(timezone.utc)

        try:
            # Mark as running
            db.execute_query(
                """UPDATE pipeline_runs
                   SET status = 'running', started_at = %s
                   WHERE id = %s""",
                (started_at, str(run_id)), fetch=False
            )

            # Create stage callback. The orchestrator runs the whole stage loop
            # inside a connection scope, so the live current_stage write must go
            # on an AUTONOMOUS connection (see _set_current_stage) — a scoped
            # write would be deferred to end_scope, freezing the displayed stage
            # for the entire run (#800 live-status regression).
            def on_stage_change(stage_name: str):
                self._set_current_stage(run_id, stage_name)

            # Run the pipeline
            result = self.trigger_service.generate_assets_for_latest_batches(
                on_stage_change=on_stage_change,
                pipeline_run_id=run_id,
            )

            # Calculate duration
            completed_at = datetime.now(timezone.utc)
            duration = (completed_at - started_at).total_seconds()

            # Serialize stats to JSON-safe format
            stats = self._sanitize_stats(result)

            # Every terminal UPDATE below is guarded on the row still being
            # unfinished. The pipeline watchdog marks a run 'failed' once it has
            # outlived its window, on the assumption this worker is dead; if the
            # assumption was wrong, an unguarded write here would resurrect the
            # row, silently resolve the operational alert and rewrite history.
            # Zero rows back means the watchdog got there first — say so and
            # leave the row alone.
            if result.get('success'):
                claimed = db.execute_query(
                    """UPDATE pipeline_runs
                       SET status = 'completed', completed_at = %s,
                           duration_seconds = %s, stats = %s, current_stage = NULL
                       WHERE id = %s
                         AND status IN ('pending', 'running')
                       RETURNING id""",
                    (completed_at, round(duration, 2), json.dumps(stats), str(run_id)),
                    fetch=True
                )
                if claimed:
                    logger.info(f"Pipeline run {run_id} completed in {duration:.1f}s")
                else:
                    logger.warning(
                        f"Pipeline run {run_id} finished in {duration:.1f}s but its "
                        f"row was already finalised by the watchdog; leaving it as-is"
                    )
            else:
                error_msg = result.get('error', 'Unknown error')
                claimed = db.execute_query(
                    """UPDATE pipeline_runs
                       SET status = 'failed', completed_at = %s,
                           duration_seconds = %s, stats = %s,
                           error_message = %s, current_stage = NULL
                       WHERE id = %s
                         AND status IN ('pending', 'running')
                       RETURNING id""",
                    (completed_at, round(duration, 2), json.dumps(stats),
                     error_msg, str(run_id)),
                    fetch=True
                )
                if claimed:
                    logger.error(f"Pipeline run {run_id} failed: {error_msg}")
                else:
                    logger.warning(
                        f"Pipeline run {run_id} failed ({error_msg}) but its row was "
                        f"already finalised by the watchdog; leaving it as-is"
                    )

        except Exception as e:
            logger.error(f"Pipeline run {run_id} exception: {e}", exc_info=True)
            completed_at = datetime.now(timezone.utc)
            duration = (completed_at - started_at).total_seconds()
            try:
                claimed = db.execute_query(
                    """UPDATE pipeline_runs
                       SET status = 'failed', completed_at = %s,
                           duration_seconds = %s, error_message = %s,
                           current_stage = NULL
                       WHERE id = %s
                         AND status IN ('pending', 'running')
                       RETURNING id""",
                    (completed_at, round(duration, 2), str(e), str(run_id)),
                    fetch=True
                )
                if not claimed:
                    logger.warning(
                        f"Pipeline run {run_id} raised ({e}) but its row was "
                        f"already finalised by the watchdog; leaving it as-is"
                    )
            except Exception as db_err:
                logger.error(f"Failed to update run {run_id} after error: {db_err}")

        finally:
            # Every terminal path, not just success. A run that failed or
            # raised part-way through may already have reached `TRUNCATE assets
            # CASCADE` and committed whatever stages completed before it
            # stopped, so the cached aggregations describe rows that no longer
            # exist — invalidating only on success left the dashboard quoting
            # figures for data the run had just destroyed, which reads as "the
            # run did nothing" rather than "the run went wrong". We cannot tell
            # from here which failures got that far (an early one — no pending
            # batches, a dead DB — changed nothing), and a needless flush only
            # costs one recompute, whereas a missed one is wrong for 60s. Also
            # covers the watchdog race the success branch was written for: the
            # assets were written whoever owns the row. Safe in a `finally`
            # because _invalidate_dashboard_cache swallows everything — a stale
            # cache must never turn a run into a failure.
            self._invalidate_dashboard_cache(run_id)
            # Flush FIRST, stamp SECOND — keep it that way. A client that sees a
            # new data version immediately refetches the dashboard endpoints, so
            # stamping first would let that refetch race the flush and re-cache
            # the very aggregations this run superseded.
            self._stamp_data_settled(db, run_id)

    def _stamp_data_settled(self, db: PostgreSQLAdapter, run_id: UUID) -> None:
        """Record the moment this run's data settled, for client cache coherency.

        `get_data_version` orders on this column, and nothing but this worker ever
        writes it — which is the whole reason it exists. `completed_at` looks like
        the same fact and is not: the watchdog writes that one too, on any row
        still 'running' past the recovery window, including a run that is merely
        slow and whose worker is still rebuilding `assets`. The version signal then
        moved MID-REBUILD, and because the terminal UPDATEs above are guarded on
        the row still being unfinished, it was never corrected afterwards — every
        open tab re-cached against a half-built asset table and stayed that way
        until the next run entirely (#1185).

        Deliberately UNGUARDED, unlike every other UPDATE in this class. The settle
        moment is a fact only this worker knows, so there is no history to protect
        and no competing writer to lose a race with. Guarding it on `status IN
        ('pending','running')` would reproduce the bug exactly: the run that most
        needs the stamp is the one whose row the watchdog has already taken.

        The timestamp is read here rather than reused from `completed_at` because
        this is the later, truer moment — the row is written and the dashboard
        cache flushed by the time we arrive. Timezone-aware: `pipeline_runs` is all
        `timestamptz`.

        Swallows everything, for the same reason `_invalidate_dashboard_cache`
        does: it runs from a `finally`, after the row has been written, so an
        escaping exception would propagate out of the worker thread and leave a
        finished run recorded as neither completed nor failed. Letting it raise
        costs the run record, which is worse — but note the swallowed cost is not
        symmetrical with the cache flush above: dashboard entries expire on a 60s
        TTL, whereas the client caches this signal drives are only ever dropped by
        a CHANGE of `run_id`, so a lost stamp leaves every open tab serving pre-run
        data until another run settles — up to a full pipeline cadence. Hence the
        WARNING, which says exactly that.
        """
        try:
            db.execute_query(
                "UPDATE pipeline_runs SET data_settled_at = %s WHERE id = %s",
                (datetime.now(timezone.utc), str(run_id)), fetch=False
            )
        except Exception as e:
            logger.warning(
                f"Pipeline run {run_id}: data version not stamped ({e}); clients "
                f"will keep serving pre-run data until another run settles"
            )

    def _invalidate_dashboard_cache(self, run_id: UUID) -> None:
        """Drop the cached dashboard aggregations this run has just invalidated.

        The dashboard endpoints cache for 60s, and nothing used to clear them —
        so a finished pipeline kept showing the PREVIOUS run's numbers until
        every entry aged out, and the operator refreshed repeatedly wondering
        whether the run had done anything.

        This is the one point both callers reach: the scheduler's cadence run
        and the API's manual trigger both land in `_execute_run`. It is
        deliberately after the `pipeline_runs` row has been finalised — the
        cache should only be dropped once the data behind it is committed and
        visible.

        Import is local because this module is imported by the scheduler, which
        must not pull in the Flask app package at import time.
        """
        try:
            from src.api.utils.caching import invalidate_dashboard_cache
            removed = invalidate_dashboard_cache()
            logger.info(
                f"Pipeline run {run_id}: cleared {removed} dashboard cache "
                f"entries"
            )
        except Exception as e:
            # Never let a cache flush turn a successful run into a failed one.
            logger.warning(
                f"Pipeline run {run_id}: dashboard cache not cleared ({e}); "
                f"entries will expire on their own TTL"
            )

    def _set_current_stage(self, run_id: UUID, stage_name: str) -> None:
        """Write the live ``current_stage`` for a running pipeline.

        Uses an autonomous (autocommit, unscoped) connection so the update is
        visible to the polling UI immediately. The orchestrator runs its stage
        loop inside a connection scope; a normal scoped write would be deferred
        to ``end_scope``, freezing the displayed stage for the whole run (#800).
        Failures are swallowed — losing a progress update must not abort the run.

        Guarded on the row still being unfinished, for the same reason the
        terminal UPDATEs in _execute_run are: once the watchdog has written the
        row to 'failed', a still-alive worker's stage write would put a live
        stage name back on a finished run.
        """
        try:
            self.connection_manager.execute_autonomous(
                """UPDATE pipeline_runs SET current_stage = %s
                   WHERE id = %s
                     AND status IN ('pending', 'running')""",
                (stage_name, str(run_id)),
            )
        except Exception as e:
            logger.warning(f"Failed to update stage for run {run_id}: {e}")

    def _sanitize_stats(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Extract JSON-serializable stats from pipeline result."""
        stats = {}
        # Zero here is load-bearing, not just a display value: get_data_version
        # reads this sub-path back and treats a completed run at zero batches as
        # provably a no-op, excluding it from the cache-coherency signal. It
        # coalesces an ABSENT count to 1 precisely because "unknown" must not be
        # read as "nothing happened" — this default says the opposite, and the
        # trigger service's exception path already returns a result with no
        # count in it. Only the `status = 'completed'` half of that predicate
        # keeps such a run visible today; any new path that reports success
        # must carry a real `batches_processed`, or a run that truncated and
        # refilled `assets` will silently stop invalidating client caches.
        stats['batches_processed'] = result.get('batches_processed', 0)
        stats['batches_failed'] = result.get('batches_failed', 0)
        stats['total_assets'] = result.get('total_assets', 0)
        stats['total_assets_created'] = result.get('total_assets_created', 0)
        stats['total_assets_updated'] = result.get('total_assets_updated', 0)
        stats['duration_seconds'] = result.get('duration_seconds', 0)

        # Extract pipeline-level metrics from generation_stats
        gen_stats = result.get('stats', {})
        stages = gen_stats.get('stages', {})

        stats['input_records'] = gen_stats.get('input_records', 0)
        stats['excluded_records'] = gen_stats.get('excluded_records', 0)
        stats['removed_assets'] = gen_stats.get('removed_assets', 0)
        stats['merged_assets'] = gen_stats.get('merged_assets', 0)

        # Unique assets from aggregation stage
        agg = stages.get('aggregation', {})
        if isinstance(agg, dict) and 'unique_assets' in agg:
            stats['unique_assets'] = agg['unique_assets']

        # Expanded records (pre-aggregation row count)
        exp = stages.get('expansion', {})
        if isinstance(exp, dict) and 'expanded_records' in exp:
            stats['expanded_records'] = exp['expanded_records']

        # Chronicle event breakdown
        chronicle = stages.get('chronicle', {})
        if isinstance(chronicle, dict):
            event_counts = chronicle.get('event_type_counts', {})
            if event_counts:
                stats['chronicle_events'] = event_counts

        # Lift grace-stage onsets/carries, the anomaly summary, and the fleet
        # confidence histogram to the top of stats so anomaly baselines (A2, A6)
        # accumulate per run from pipeline_runs.stats.
        grace = stages.get('grace_carry_forward', {})
        if isinstance(grace, dict):
            if 'missing_onsets' in grace:
                stats['grace_missing_onsets'] = grace['missing_onsets']
            if 'carried_forward' in grace:
                stats['grace_carried_total'] = grace['carried_forward']
        anomaly = stages.get('anomaly_detection', {})
        if isinstance(anomaly, dict) and anomaly:
            stats['anomaly'] = {k: anomaly[k] for k in
                                ('rules_evaluated', 'alerts_created', 'alerts_updated',
                                 'alerts_resolved', 'errors') if k in anomaly}
            if 'confidence_histogram' in anomaly:
                stats['confidence_histogram'] = anomaly['confidence_histogram']

        # Include per-batch summaries with data source info
        per_source = gen_stats.get('per_source', {})
        batch_summaries = []
        for br in result.get('batch_results', []):
            batch_id = br.get('batch_id', '')
            summary = {
                'batch_id': batch_id,
                'success': br.get('success'),
                'input_records': br.get('input_records', 0),
                'error': br.get('error')
            }
            if br.get('integration_name'):
                summary['integration_name'] = br['integration_name']
            if br.get('integration_type'):
                summary['integration_type'] = br['integration_type']
            # Merge per-source pipeline stats (unique assets, excluded)
            source_stats = per_source.get(batch_id, {})
            if source_stats:
                summary['unique_assets'] = source_stats.get('unique_assets', 0)
                summary['excluded'] = source_stats.get('excluded', 0)
            batch_summaries.append(summary)
        stats['batch_results'] = batch_summaries

        # Per-stage timings (label recorded by PipelineStage.execute)
        stage_timings = []
        for stage_name, stage_data in stages.items():
            if isinstance(stage_data, dict) and not stage_data.get('skipped'):
                stage_timings.append({
                    'name': stage_data.get('label', stage_name),
                    'duration': round(stage_data.get('duration', 0), 3),
                })
        stats['stage_timings'] = stage_timings

        return stats

    def get_current_status(self) -> Dict[str, Any]:
        """Get current pipeline status (running or idle)."""
        active = self._db.execute_query(
            """SELECT id, status, current_stage, started_at, triggered_by
               FROM pipeline_runs
               WHERE status IN ('pending', 'running')
               ORDER BY created_at DESC LIMIT 1""",
            (), fetch=True
        )

        if active:
            run = active[0]
            elapsed = None
            if run.get('started_at'):
                elapsed = (datetime.now(timezone.utc) - run['started_at']).total_seconds()
            return {
                'status': run['status'],
                'run_id': str(run['id']),
                'current_stage': run.get('current_stage'),
                'elapsed_seconds': round(elapsed, 1) if elapsed else None,
                'triggered_by': run.get('triggered_by')
            }

        # Idle — include last run info. Fetch only the small JSON sub-paths the
        # New/Returned split needs (this endpoint is polled ~every 15s), not the
        # whole stats blob (batch_results, stage_timings, per-source summaries).
        last = self._db.execute_query(
            """SELECT id, status, completed_at, duration_seconds, triggered_by,
                      stats->'total_assets_created' as total_assets_created,
                      stats->'total_assets_updated' as total_assets_updated,
                      stats->'chronicle_events' as chronicle_events
               FROM pipeline_runs
               ORDER BY created_at DESC LIMIT 1""",
            (), fetch=True
        )

        result = {'status': 'idle'}
        if last:
            run = last[0]
            created, readded = _split_created_readded({
                'chronicle_events': run.get('chronicle_events'),
                'total_assets_created': run.get('total_assets_created'),
            })
            result['last_run'] = {
                'run_id': str(run['id']),
                'status': run['status'],
                'completed_at': run['completed_at'].isoformat() if run.get('completed_at') else None,
                'duration_seconds': float(run['duration_seconds']) if run.get('duration_seconds') else None,
                'triggered_by': run.get('triggered_by'),
                'assets_created': created,
                'assets_readded': readded,
                'assets_updated': run.get('total_assets_updated') or 0,
            }
        return result

    def get_data_version(self) -> Dict[str, Any]:
        """Identify the data currently in `assets`, for client cache coherency.

        Every run rebuilds the asset table from scratch (`TRUNCATE assets
        CASCADE`), so the moment one finishes, every cache the UI holds —
        Explorer results, field pickers, value dropdowns, the rule builders —
        describes data that is gone. Clients poll this and drop their caches
        when `run_id` changes, which is why it must be readable by any
        authenticated role, not just admins.

        Ordered by `data_settled_at` rather than filtered to a completed status:
        a FAILED run truncated the table and committed whatever stages ran
        before it stopped, so it invalidates exactly the caches a successful
        one does. `data_settled_at IS NOT NULL` excludes the pending/running row
        a trigger has just created — a run that is only STARTING must not bump
        the version, or every tab would refetch against half-replaced data.

        The ordering key is `data_settled_at`, NOT `completed_at`, and that is a
        fix rather than a detail (#1185). `completed_at` has two writers: this
        worker, whose terminal UPDATE is guarded on the row still being
        unfinished, and `recover_orphaned_running_runs`, which stamps it on any
        row still 'running' past the recovery window — including a run that is
        merely slow and whose worker is still writing. On that run the watchdog's
        mid-rebuild stamp moved the version, every tab re-cached against a
        half-built asset table, and because the worker's own UPDATE then matched
        zero rows by design, nothing ever corrected it: no second bump arrived
        until the next run entirely. `data_settled_at` is written only by
        `_stamp_data_settled`, from the `finally` that already flushes the
        dashboard cache, so it names the moment the data settled whoever owns the
        row by then.

        `completed_at` is still what comes back — the response shape is
        `{run_id, completed_at, status}` and clients key off `run_id` alone — but
        read it as the status-transition timestamp, not the settle moment. On a
        watchdog-finalised run it PREDATES the stamp, sometimes by the whole
        remaining duration of the run.

        The one case this loses that ordering on `completed_at` had: a worker that
        genuinely died (host killed, OOM) never stamps, so its partial rebuild
        goes unreported until the next run settles. That is the deliberate trade —
        the alternative is trusting a column whose value, on exactly the runs that
        matter, was written by a process that did not know what the table
        contained.

        One run is excluded, the one that provably changed nothing:
        `generate_assets_for_latest_batches` returns early — success, zero
        batches — when `_get_latest_pending_batches` comes back empty, so the
        orchestrator never starts and the TRUNCATE never fires, yet the row is
        still stamped `completed` and every open tab throws its whole cache
        away for it (#1185).

        Be precise about when that happens, because the name suggests more
        than the query does: `_get_latest_pending_batches` re-picks the latest
        completed batch of every ENABLED integration on every run, with no
        filter on whether it was generated before — the pipeline TRUNCATEs, so
        it needs all sources every time. It is therefore empty only when no
        enabled integration has a completed batch carrying records at all: an
        install with nothing wired up yet, or one whose integrations have all
        been disabled. A run whose sources merely have not re-synced still
        rebuilds `assets` from the same batches, and still bumps the version.

        Both halves of that predicate carry weight. `status = 'completed'`
        keeps a FAILED run in even at zero batches — it may have got past the
        TRUNCATE before it stopped, so only completed AND zero is provably a
        no-op. The COALESCE is what stops the exclusion being silent: `stats`
        is nullable (the INSERT writes none, and a watchdog-finalised run never
        gets any), and with the sub-path NULL, `NOT (true AND NULL)` is NULL
        rather than true — Postgres would drop every such row, an install with
        history would start answering all-nulls, and nothing would raise.
        Defaulting to 1, a count that cannot be a no-op, keeps rows whose batch
        count is unknown.

        Deliberately narrow: polled once a minute by every open tab, so it
        selects three columns and never the `stats` blob itself — the WHERE
        reads one scalar sub-path out of it, as `get_current_status` does.
        All-null on a fresh install (no run has ever finished) rather than an
        error.
        """
        rows = self._db.execute_query(
            """SELECT id, status, completed_at
               FROM pipeline_runs
               WHERE data_settled_at IS NOT NULL
                 AND NOT (status = 'completed'
                          AND COALESCE((stats->>'batches_processed')::int, 1) = 0)
               ORDER BY data_settled_at DESC LIMIT 1""",
            (), fetch=True
        )

        if not rows:
            return {'run_id': None, 'completed_at': None, 'status': None}

        run = rows[0]
        completed_at = run.get('completed_at')
        return {
            'run_id': str(run['id']),
            'completed_at': completed_at.isoformat() if completed_at else None,
            'status': run.get('status'),
        }

    # Map the chat/API sort key to its column. Whitelisted so the value can
    # be interpolated into ORDER BY (which can't be parameterized) without
    # SQL-injection risk; callers should still validate the enum for a clean
    # user-facing error.
    _RUN_SORT_COLUMNS = {
        "created": "created_at",
        "started": "started_at",
        "completed": "completed_at",
        "duration": "duration_seconds",
        "status": "status",
    }

    def get_runs(self, page: int = 1, per_page: int = 20, *,
                 status: str = None, start_date=None, end_date=None,
                 sort: str = "created", sort_dir: str = "desc") -> Dict[str, Any]:
        """Get paginated run history.

        Optional ``status`` and ``start_date``/``end_date`` (on ``created_at``)
        filters narrow the history; ``sort`` (created|started|completed|
        duration|status) + ``sort_dir`` (asc|desc) reorder it. The COUNT uses
        the same filters so ``total`` reflects the filtered set.
        """
        offset = (page - 1) * per_page

        order_col = self._RUN_SORT_COLUMNS.get(sort, "created_at")
        order_dir = "ASC" if str(sort_dir).lower() == "asc" else "DESC"

        conditions = []
        params: list = []
        if status:
            conditions.append("status = %s")
            params.append(status)
        if start_date is not None:
            conditions.append("created_at >= %s")
            params.append(start_date)
        if end_date is not None:
            conditions.append("created_at <= %s")
            params.append(end_date)
        where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        # Total count (same filters → total reflects the filtered set)
        count_result = self._db.execute_query(
            f"SELECT COUNT(*) as total FROM pipeline_runs {where_sql}",
            tuple(params), fetch=True
        )
        total = count_result[0]['total'] if count_result else 0

        # Fetch page
        runs = self._db.execute_query(
            f"""SELECT id, triggered_by, status, started_at, completed_at,
                      duration_seconds, stats, error_message, created_at
               FROM pipeline_runs
               {where_sql}
               ORDER BY {order_col} {order_dir} NULLS LAST
               LIMIT %s OFFSET %s""",
            tuple(params + [per_page, offset]), fetch=True
        )

        items = []
        for run in runs:
            item = {
                'id': str(run['id']),
                'triggered_by': run['triggered_by'],
                'status': run['status'],
                'started_at': run['started_at'].isoformat() if run.get('started_at') else None,
                'completed_at': run['completed_at'].isoformat() if run.get('completed_at') else None,
                'duration_seconds': float(run['duration_seconds']) if run.get('duration_seconds') else None,
                'error_message': run.get('error_message'),
                'created_at': run['created_at'].isoformat() if run.get('created_at') else None,
            }

            # Extract summary stats from JSONB
            stats = run.get('stats')
            if stats:
                if isinstance(stats, str):
                    stats = json.loads(stats)
                item['total_assets'] = stats.get('total_assets', 0)
                # New = asset_created only; Returned = asset_readded, with the
                # legacy combined-count fallback (see _split_created_readded).
                item['assets_created'], item['assets_readded'] = \
                    _split_created_readded(stats)
                item['assets_updated'] = stats.get('total_assets_updated', 0)
                item['assets_removed'] = stats.get('removed_assets', 0)
                item['assets_merged'] = stats.get('merged_assets', 0)
                item['batches_processed'] = stats.get('batches_processed', 0)
            else:
                item['total_assets'] = 0
                item['assets_created'] = 0
                item['assets_readded'] = 0
                item['assets_updated'] = 0
                item['assets_removed'] = 0
                item['assets_merged'] = 0
                item['batches_processed'] = 0

            items.append(item)

        return {
            'runs': items,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page if per_page else 0
        }

    def get_run(self, run_id: UUID) -> Optional[Dict[str, Any]]:
        """Get a single run's full details."""
        runs = self._db.execute_query(
            """SELECT id, triggered_by, status, current_stage, started_at,
                      completed_at, duration_seconds, stats, error_message, created_at
               FROM pipeline_runs WHERE id = %s""",
            (str(run_id),), fetch=True
        )

        if not runs:
            return None

        run = runs[0]
        stats = run.get('stats')
        if stats and isinstance(stats, str):
            stats = json.loads(stats)
        created, readded = _split_created_readded(stats or {})

        return {
            'id': str(run['id']),
            'triggered_by': run['triggered_by'],
            'status': run['status'],
            'current_stage': run.get('current_stage'),
            'started_at': run['started_at'].isoformat() if run.get('started_at') else None,
            'completed_at': run['completed_at'].isoformat() if run.get('completed_at') else None,
            'duration_seconds': float(run['duration_seconds']) if run.get('duration_seconds') else None,
            'stats': stats,
            # New/Returned split (feeds the UI RunDetailPanel), consistent with
            # get_runs; legacy runs fall back to the combined count.
            'assets_created': created,
            'assets_readded': readded,
            'assets_updated': (stats or {}).get('total_assets_updated', 0),
            'error_message': run.get('error_message'),
            'created_at': run['created_at'].isoformat() if run.get('created_at') else None,
        }

    def sync_schedule(self, scheduler) -> None:
        """Apply pipeline schedule settings to APScheduler. Idempotent."""
        try:
            enabled = self._get_setting('asset_generation', 'pipeline_schedule_enabled')
            mode = None
            cron_expr = None
            tz_name = None
            interval_hours = None

            if not enabled:
                desired_config: tuple = ('disabled',)
            else:
                mode = self._get_setting('asset_generation', 'pipeline_schedule_mode')
                if mode == 'cron':
                    from src.modules.integrations.shared.cron_validator import expand_preset
                    cron_expr = expand_preset(
                        self._get_setting('asset_generation', 'pipeline_schedule_cron')
                    )
                    tz_name = self._get_setting('asset_generation', 'pipeline_schedule_timezone')
                    desired_config = ('cron', cron_expr, tz_name)
                else:
                    interval_hours = self._get_setting(
                        'asset_generation', 'pipeline_schedule_interval_hours'
                    )
                    desired_config = ('interval', interval_hours)

            # Existence check guards against an external process removing the job
            # while our cached fingerprint still matches the DB.
            existing_job = scheduler.get_job(self.PIPELINE_SCHEDULE_JOB_ID)
            if desired_config == self._last_applied_schedule_config:
                if not enabled and existing_job is None:
                    return
                if enabled and existing_job is not None:
                    return

            if existing_job is not None:
                scheduler.remove_job(self.PIPELINE_SCHEDULE_JOB_ID)
                logger.info("Removed existing pipeline schedule job")

            if not enabled:
                logger.info("Pipeline schedule disabled")
            elif mode == 'cron':
                from apscheduler.triggers.cron import CronTrigger
                import pytz

                parts = cron_expr.split()
                trigger = CronTrigger(
                    minute=parts[0],
                    hour=parts[1],
                    day=parts[2],
                    month=parts[3],
                    day_of_week=parts[4],
                    timezone=pytz.timezone(tz_name)
                )
                scheduler.add_job(
                    func=self._scheduled_trigger,
                    trigger=trigger,
                    id=self.PIPELINE_SCHEDULE_JOB_ID,
                    name='Pipeline Auto Run',
                    replace_existing=True,
                    coalesce=True,
                    max_instances=1
                )
                logger.info(f"Pipeline scheduled with cron '{cron_expr}' ({tz_name})")
            else:
                from apscheduler.triggers.interval import IntervalTrigger

                # Anchor the interval to the last completed run so a scheduler
                # restart doesn't reset the cadence. IntervalTrigger with no
                # start_date implicitly anchors at "now", so every restart (each
                # redeploy) re-adds the job with next_run = now + interval —
                # frequent auto-updates then starve the schedule (three deploys
                # in one day pushed a 4h pipeline into a ~10h gap). With
                # start_date pinned to the last run, fire times stay on the fixed
                # grid (last_run + k*interval) and a restart just picks the next
                # slot. No anchor yet (brand-new install) → default now+interval.
                anchor = self._interval_anchor()
                trigger = (
                    IntervalTrigger(hours=interval_hours, start_date=anchor)
                    if anchor is not None
                    else IntervalTrigger(hours=interval_hours)
                )
                scheduler.add_job(
                    func=self._scheduled_trigger,
                    trigger=trigger,
                    id=self.PIPELINE_SCHEDULE_JOB_ID,
                    name='Pipeline Auto Run',
                    replace_existing=True,
                    coalesce=True,
                    max_instances=1
                )
                logger.info(
                    "Pipeline scheduled to run every %s hours%s",
                    interval_hours,
                    f" (anchored at {anchor.isoformat()})" if anchor else "",
                )

            self._last_applied_schedule_config = desired_config

        except Exception as e:
            logger.error(f"Failed to sync pipeline schedule: {e}", exc_info=True)

    def _interval_anchor(self) -> Optional[datetime]:
        """The last completed run's ``completed_at``, used as the interval
        schedule's ``start_date`` so the cadence survives scheduler restarts.

        Returns None when no run has ever completed (brand-new install) or the
        lookup fails, so the caller falls back to the default now+interval
        anchor. Uses ``completed_at`` to stay consistent with the interval
        fallback in ``_get_pipeline_next_run`` (pipeline blueprint).
        """
        try:
            rows = self._db.execute_query(
                """SELECT completed_at FROM pipeline_runs
                   WHERE status = 'completed' AND completed_at IS NOT NULL
                   ORDER BY completed_at DESC LIMIT 1""",
                (), fetch=True
            )
        except Exception as e:
            logger.warning("Could not read pipeline schedule anchor: %s", e)
            return None
        if rows:
            completed_at = rows[0].get('completed_at')
            if isinstance(completed_at, datetime):
                return completed_at
        return None

    def _scheduled_trigger(self) -> None:
        """Called by APScheduler for scheduled runs."""
        logger.info("Scheduled pipeline trigger firing")
        result = self.trigger_run(triggered_by='schedule')
        if not result.get('success'):
            logger.warning(f"Scheduled pipeline trigger rejected: {result.get('error')}")

    def _get_setting(self, category: str, key: str):
        """Read a setting value from system_settings, falling back to schema default."""
        from src.api.modules.settings.settings_schema import get_default_value
        try:
            rows = self._db.execute_query(
                "SELECT value FROM system_settings WHERE category = %s AND key = %s",
                (category, key), fetch=True
            )
            if rows:
                val = rows[0]['value']
                if isinstance(val, str):
                    # Try JSON parse (handles 'true', '168', '"quoted"')
                    # Fall back to raw string (handles 'cron', '0 * * * *', 'UTC')
                    try:
                        return json.loads(val)
                    except (json.JSONDecodeError, ValueError):
                        return val
                return val
            return get_default_value(category, key)
        except Exception:
            return get_default_value(category, key)
