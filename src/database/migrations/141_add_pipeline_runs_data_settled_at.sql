-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 141: give the pipeline worker an UNCONTESTED column to record when a
-- run's data actually settled, and index the read that consumes it.
--
-- GET /api/pipeline/data-version reports which run's output is currently in the
-- `assets` table. Every open browser tab polls it once a minute and drops its
-- entire client-side cache when the reported run id changes, because a run
-- rebuilds the table from scratch (TRUNCATE assets CASCADE). That signal used to
-- be derived from pipeline_runs.completed_at -- and completed_at has TWO writers:
--
--   * pipeline_service._execute_run, when the worker finishes. Guarded on
--     `status IN ('pending','running')` so a late worker cannot resurrect a row
--     the watchdog already finalised, silently resolve the operational alert and
--     rewrite run history (#1166/#1170).
--   * pipeline_watchdog.recover_orphaned_running_runs, on any row still 'running'
--     past the recovery window. Its own docstring names the cost: it "kills the
--     row of a run that is merely slow (releasing trigger_run()'s concurrency
--     guard while its worker is still writing)".
--
-- On a slow-but-alive run those two combine into a signal that is wrong twice
-- over. The watchdog stamps completed_at MID-RUN, so within 60s every tab
-- re-caches against a half-rebuilt assets table. Then when the worker genuinely
-- finishes, its guarded UPDATE matches zero rows -- deliberately -- so
-- completed_at is never corrected and no second bump ever arrives. Tabs stay
-- wrong until the next run entirely, which on the default 24-hour cadence is a
-- long time to serve data that no longer exists.
--
-- The watchdog's ownership contract is correct and is not renegotiated here: it
-- exists to unwedge trigger_run()'s concurrency guard, and it cannot know whether
-- the worker is dead. So the worker gets a column of its own instead, written in
-- the same `finally` that already invalidates the dashboard cache -- the one place
-- that knows when the data settled, whoever owns the row by then. Note the
-- asymmetry that pointed at this fix: the DASHBOARD cache never had the gap,
-- precisely because its invalidation already fires from that `finally`. Only the
-- data-version signal took its moment from a contested status column.
--
-- Nothing but the worker writes data_settled_at, so its UPDATE carries no status
-- predicate. Adding one would reproduce the bug exactly: the run that most needs
-- the stamp is the run whose row the watchdog has taken.
--
-- pipeline_runs is a plain table, not a TimescaleDB hypertable, so ADD COLUMN is
-- unremarkable -- no chunk rewrite, no compression policy to weigh.
--
-- Idempotent (IF NOT EXISTS on both DDL statements, and the backfill is guarded on
-- data_settled_at IS NULL). Dual-written to schema-full.sql for fresh installs.
BEGIN;

ALTER TABLE pipeline_runs
  ADD COLUMN IF NOT EXISTS data_settled_at timestamptz;

-- REQUIRED, not optional: get_data_version filters on `data_settled_at IS NOT
-- NULL`, so without this every existing install answers all-nulls the moment 141
-- lands -- no tab learns anything until its next run finishes, i.e. up to a full
-- pipeline cadence of every client believing the asset table has never been
-- rebuilt. completed_at is the right source for a row that has ALREADY finished:
-- it is contested going forward, but for history it is the only record of when
-- that run's data landed.
--   Both guards matter. `data_settled_at IS NULL` makes a re-run a no-op instead
-- of resetting a stamp the worker has since written. `completed_at IS NOT NULL`
-- keeps the row of a run that has not reached a terminal state out: backfilling a
-- pending/running run would hand the version signal a rebuild that has not
-- happened yet, which is the exact failure this migration exists to stop.
--   It does NOT keep out the one in-flight row SQL cannot recognise: a
-- watchdog-finalised run whose worker is still writing already has status
-- 'failed' and a mid-run `completed_at`, so applying 141 during such a run
-- backfills it and moves the version once, mid-rebuild. Nothing in the row says
-- otherwise, so there is no predicate that would exclude it. Bounded, unlike the
-- old behaviour: `_stamp_data_settled` is unguarded, so when that worker finishes
-- it overwrites the backfilled value and the version corrects itself.
UPDATE pipeline_runs SET data_settled_at = completed_at
  WHERE completed_at IS NOT NULL AND data_settled_at IS NULL;

-- Finding 3 of the same issue (#1185): pipeline_runs carried indexes on created_at,
-- started_at and status only, so the data-version read -- one row, ordered
-- descending, polled once a minute by every open tab -- was a sort over the whole
-- table. The partial predicate mirrors the query's own filter, so unsettled rows
-- (pending, running, and the watchdog-finalised row whose worker is still writing)
-- stay out of the index entirely. Rides this migration so it costs no extra
-- deployment debt.
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_settled
  ON pipeline_runs (data_settled_at DESC)
  WHERE data_settled_at IS NOT NULL;

COMMIT;
