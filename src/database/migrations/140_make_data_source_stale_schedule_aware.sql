-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 140: re-key the "Data Source Stale" alert onto two independent
-- signals -- each integration's own schedule, plus a schedule-blind sync-age
-- backstop -- replacing a single hardcoded 24-hour window.
--
-- The rule fired on any enabled integration whose last_sync_at was older than a
-- literal `interval '24 hours'`. That window was invisible and untunable: the
-- threshold JSON advertised {"value": 24, "metric": "hours_since_sync"} but
-- nothing read it, and system-owned rules reject edits (update_rule raises
-- SystemRuleImmutableError for source='system'), so an operator could not change
-- it at all. Meanwhile every integration already carries its own schedule
-- (schedule_enabled / schedule_cron / schedule_timezone) and a scheduler-maintained
-- next_run_at.
--
-- With most sources scheduled roughly daily, the fixed window equalled the sync
-- period, so a sync slipping by minutes tripped a `high` alert: on the shared dev
-- DB all 34 enabled integrations matched the old rule at once. The failure is
-- two-sided -- an hourly source could be dead for 23 hours and stay silent, and a
-- weekly one would alert six days out of seven.
--
-- New shape: an integration is stale when EITHER branch below matches. They answer
-- different questions and fail in different directions, so neither alone is enough.
--
-- BRANCH 1 -- past due for its own scheduled run ("did the scheduler service it?").
--   next_run_at is the scheduler's per-integration "due at" pointer.
--   _execute_integration_robust advances it in a finally block after every attempt
--   (success or failure), so one bad sync cannot wedge it. A next_run_at left far in
--   the past therefore means the run was never serviced. This is the sharp signal:
--   it catches an hourly source within its own grace window instead of waiting out a
--   global age. No cron parsing in SQL required.
--     Anchor: COALESCE(next_run_at, last_sync_at, created_at, '-infinity'). Each
--     fallback covers a state the one before it cannot see:
--       * next_run_at IS NULL on a *scheduled* integration is itself a fault -- the
--         scheduler never computed a due time -- so the chain keeps walking rather
--         than treating NULL as healthy.
--       * last_sync_at covers a re-enabled integration: it has synced before, so its
--         last sync is a sound lower bound for "when should this have run by".
--       * created_at covers a JUST-CREATED one, which has neither. next_run_at is
--         populated asynchronously (integration_service.update_integration only
--         signals the scheduler; the 10-second dirty-schedule check computes it, with
--         the 2-minute resync as fallback) while the interval alert job evaluates
--         every 5 minutes -- so without this element a brand-new integration fires a
--         `high` alert, and dispatches outbound deliveries, before the source has had
--         any chance to sync at all. With the scheduler worker down it fired 100% of
--         the time. Anchoring on created_at makes it wait one grace window instead.
--       * '-infinity' stays as the terminal element because created_at is NULLABLE
--         (DEFAULT now(), but no NOT NULL). Dropping it would make a row with a NULL
--         created_at silently un-alertable -- failing open on exactly the malformed
--         row most likely to need attention.
--
-- BRANCH 2 -- no sync ATTEMPT in N hours ("did the job ever actually run?").
--   Note what last_sync_at is and is not: integration_repository.update_sync_status
--   writes `last_sync_at = NOW()` for status 'failed' as well as 'success', so the
--   column is the last sync ATTEMPT, not the last successful refresh. Branch 2 is
--   therefore a backstop against "the job never ran at all", NOT against "the data
--   never landed" -- a source that fires hourly and fails every time keeps
--   last_sync_at fresh and stays out of this branch entirely (sync-failures is the
--   rule that owns that case). Do not read the branch as coverage for silent
--   no-op syncs; it is not.
--   Branch 1 alone is not sufficient, because next_run_at heals. _recover_schedules
--   re-creates EVERY scheduled job at worker start and _should_persist_next_run
--   overwrites any stored value already in the past, so a scheduler that crash-loops
--   -- or simply restarts on each deploy, which is the normal path in production --
--   pushes the pointer back into the future on every boot. The rule would then go
--   quiet on precisely the sources it exists to watch: on the shared dev DB 14 of 34
--   enabled integrations sat over a MONTH stale (last_sync_at 2026-06-24,
--   last_sync_status 'success') with a future next_run_at, invisible to branch 1 and
--   caught by the old 24-hour rule.
--     So branch 2 asks the independent question directly, off last_sync_at, which no
--     restart rewrites. Anchor: COALESCE(last_sync_at, created_at, '-infinity') --
--     same reasoning as branch 1, and the created_at element is what keeps a
--     never-synced brand-new integration out of this branch too. A plain
--     `last_sync_at IS NULL OR last_sync_at < ...` would re-fire the day-zero alert
--     that branch 1's created_at anchor exists to prevent.
--     Window default 48 hours, not 24: at 24 the window equalled the sync period for
--     a daily source and that is what produced the original false-positive storm. 48
--     gives a daily source a full extra period of headroom. It is deliberately a
--     blunt instrument -- a source scheduled LESS often than the window will trip it
--     every cycle, which is why it is a tunable setting (max 8760) rather than a
--     literal. Branch 1 remains the precise per-source signal; branch 2 only has to
--     be loose enough to stay quiet in steady state and tight enough to notice a
--     source that has genuinely stopped.
--     KNOWN FALSE POSITIVE: because the window is global and the schedule is
--     per-integration, any source scheduled LESS often than the window is stale by
--     this branch's definition for most of its own cycle -- the integration wizard
--     ships Weekly ('0 0 * * 0') and Monthly ('0 0 1 * *') presets, so a healthy
--     weekly source matches ~5 days in 7 at the 48h default. The same applies to a
--     source whose schedule was disabled (unschedule_integration, behind the
--     schedule/disable endpoint, sets schedule_enabled=false AND nulls next_run_at,
--     so syncs stop) and is later re-enabled: last_sync_at is already older than the
--     window, so branch 2 fires on the next 5-minute evaluation, before the first
--     scheduled run has had any chance to happen. Note this is not a regression --
--     the 24h rule this replaces had the same shape, twice as tightly -- but it is
--     the part of the old behaviour the new one does NOT fix. A deployment mixing
--     daily and weekly/monthly sources cannot tune around it with one global
--     setting; closing it properly needs the per-source period, i.e. cron arithmetic
--     in SQL or a scheduler-maintained "previous due time" column. Neither belongs
--     in this migration; no follow-up is filed yet.
--
-- Shared guards on both branches:
--   * schedule_enabled = false is EXCLUDED. Those are manual/upload-driven sources
--     with no declared refresh expectation; under the old rule they alerted forever
--     and were the single largest false-positive source.
--   * is_running = true is EXCLUDED. next_run_at is advanced only in the finally
--     AFTER every JobExecutor attempt (SCHEDULER_JOB_TIMEOUT 3600s x SCHEDULER_MAX_
--     RETRIES 3), and last_sync_at is written only when a sync finishes, so an
--     integration looks both past-due and unrefreshed for the whole duration of its
--     own run; without this guard a sync that outlives either window -- a slow full
--     sync, or one whose first attempt hits the 1-hour timeout and is retried --
--     fires a `high` alert while it is actively refreshing. Safe to trust: a flag
--     stranded by a crashed process is cleared at API/scheduler startup by
--     cleanup_stale_running_flags, which probes the advisory lock run_integration
--     actually holds rather than believing the flag.
--
-- subject_sublabel names WHICH branch matched, so an operator can tell "overdue for
-- its scheduled run" from "has not refreshed since X" without re-deriving it from the
-- integration record; when both match it says both. It is built with concat_ws rather
-- than a `||` chain: `||` propagates NULL, so one unguarded nullable operand blanks
-- the whole string, and concat_ws instead drops NULL arguments and can never itself
-- return NULL. The WHERE guarantees at least one branch arm is present.
--
-- Each window comes from its own setting, so the two are tuned separately:
-- alerting.DATA_SOURCE_STALE_GRACE_MINUTES (default 60) and
-- alerting.DATA_SOURCE_MAX_SYNC_AGE_HOURS (default 48). Both are read inline as a
-- scalar subquery wrapped in COALESCE with a literal fallback. The COALESCE is
-- load-bearing: migration 119's ootb-grace-expiry-preview reads its setting through a
-- CTE cross-joined to the driving table, which silently returns zero rows -- and
-- therefore never fires -- if the settings row is missing. A scalar subquery degrades
-- to the default instead of going blank.
--
-- The `trim(s.value) ~ '^[0-9]{1,6}$'` filter extends that same safety to a row that
-- exists but holds junk. system_settings.value is TEXT, and installer scripts write
-- the table directly via psql (deploy/lib/*.sh), so '' or 'abc' is storable; a bare
-- s.value::int would raise, execute_rule_query would propagate, and evaluate_rule
-- swallows that as 'error' -- leaving the rule permanently dead AND any already-active
-- alert never resolved. Filtering in the subquery makes a malformed value miss, so
-- COALESCE falls back to the default. The {1,6} bound also keeps the ::int cast off an
-- out-of-range literal (max 999999, well above either setting's ceiling).
--   The filter guards the CAST, not the RANGE: '0' passes it, so a value written
--   below either setting's declared minimum (the Settings API clamps to min 5 / min 1,
--   but a psql-level write does not) is used verbatim. A grace of 0 makes every
--   scheduled integration match from the instant it falls due until the scheduler
--   services it. Clamp with GREATEST/LEAST here if that write path ever matters.
--
-- evaluation_cadence also moves 'pipeline' -> 'interval'. Under 'pipeline' the check
-- only ran during an asset-generation run, so a broken pipeline meant data-source
-- staleness was never checked at all -- and a stale source is a common *cause* of a
-- bad run. The 5-minute scheduler job (_evaluate_interval_alert_rules) is
-- independent of the pipeline. Three consequences of that move, none of them
-- obvious from the cadence column alone:
--   * The rule now runs ONLY inside src/scheduler_worker.py. Branch 1's premise is
--     "a next_run_at left in the past means the run was never serviced" -- which is
--     exactly the state a dead or crash-looping scheduler worker produces, and in
--     that state nothing evaluates this rule at all. Under 'pipeline' a manually
--     triggered run (API process) would still have surfaced it. The scheduler's own
--     liveness is watched by the pipeline watchdog / system-scheduler-stalled, not
--     here.
--   * scheduler_worker._init_services only builds self.alerts_service when
--     build_field_resolver() succeeded (a failure is logged as a warning and
--     swallowed), so a resolver build failure silences this rule even though it is
--     pure SQL and needs no query compiler.
--   * AlertsService._record_event returns early when pipeline_run_id is None, and
--     the interval path always passes None -- so this rule stops writing alert_events
--     rows and its #906 firing-history panel will show nothing from here on.
--
-- silence_duration stays NULL deliberately: one alert covers many integrations, so
-- a per-rule silence would mask a newly stale source while an unrelated one sits in
-- its silence window.
--
-- Idempotent (settings insert is ON CONFLICT DO NOTHING; the rule updates are
-- assignments to fixed values). Dual-written to schema-full.sql for fresh installs.
BEGIN;

-- Both windows. Named value_type AND default_value: both are NOT NULL with no
-- default, and Postgres validates the proposed tuple before it detects the
-- conflict, so ON CONFLICT never rescues an INSERT that omits them. DO NOTHING
-- rather than DO UPDATE so a re-run never stomps an operator's tuned value.
INSERT INTO system_settings (category, key, value, value_type, default_value,
                             description, validation_rule, is_sensitive, requires_restart)
VALUES
    ('alerting', 'DATA_SOURCE_STALE_GRACE_MINUTES', '60', 'integer', '60',
     'Minutes an integration may be past its own scheduled sync time before the Data Source Stale alert fires.',
     '{"min": 5, "max": 10080}', FALSE, FALSE),
    ('alerting', 'DATA_SOURCE_MAX_SYNC_AGE_HOURS', '48', 'integer', '48',
     'Hours an integration may go without any sync at all before the Data Source Stale alert fires, regardless of its schedule.',
     '{"min": 1, "max": 8760}', FALSE, FALSE)
ON CONFLICT (category, key) DO NOTHING;

UPDATE alert_rules SET query = $q$SELECT i.id AS subject_id,
       i.name AS subject_label,
       concat_ws(' · ',
           i.integration_type,
           CASE WHEN COALESCE(i.next_run_at, i.last_sync_at, i.created_at, '-infinity'::timestamptz) <
                     now() - (COALESCE((SELECT s.value::int
                                        FROM system_settings s
                                        WHERE s.category = 'alerting'
                                          AND s.key = 'DATA_SOURCE_STALE_GRACE_MINUTES'
                                          AND trim(s.value) ~ '^[0-9]{1,6}$'), 60) || ' minutes')::interval
                THEN COALESCE('overdue for its scheduled run since '
                              || to_char(i.next_run_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') || ' UTC',
                              'overdue for its scheduled run (next run never computed)')
           END,
           CASE WHEN COALESCE(i.last_sync_at, i.created_at, '-infinity'::timestamptz) <
                     now() - (COALESCE((SELECT s.value::int
                                        FROM system_settings s
                                        WHERE s.category = 'alerting'
                                          AND s.key = 'DATA_SOURCE_MAX_SYNC_AGE_HOURS'
                                          AND trim(s.value) ~ '^[0-9]{1,6}$'), 48) || ' hours')::interval
                THEN COALESCE('has not refreshed since '
                              || to_char(i.last_sync_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') || ' UTC',
                              'has never refreshed')
           END
       ) AS subject_sublabel
FROM integrations i
WHERE i.status = 'enabled'
  AND i.schedule_enabled
  AND NOT i.is_running
  AND (COALESCE(i.next_run_at, i.last_sync_at, i.created_at, '-infinity'::timestamptz) <
       now() - (COALESCE((SELECT s.value::int
                          FROM system_settings s
                          WHERE s.category = 'alerting'
                            AND s.key = 'DATA_SOURCE_STALE_GRACE_MINUTES'
                            AND trim(s.value) ~ '^[0-9]{1,6}$'), 60) || ' minutes')::interval
       OR
       COALESCE(i.last_sync_at, i.created_at, '-infinity'::timestamptz) <
       now() - (COALESCE((SELECT s.value::int
                          FROM system_settings s
                          WHERE s.category = 'alerting'
                            AND s.key = 'DATA_SOURCE_MAX_SYNC_AGE_HOURS'
                            AND trim(s.value) ~ '^[0-9]{1,6}$'), 48) || ' hours')::interval)$q$
    WHERE slug = 'data-source-stale';

-- Copy + metadata. The old description and threshold both asserted 24 hours, which
-- the query no longer implements; threshold is display-only but must not contradict
-- it, and it must name BOTH settings or an operator tunes the wrong one. value 0 /
-- operator '>' mirrors sync-failures: fire on any matching row.
UPDATE alert_rules
   SET description = 'A data source is overdue for its own scheduled sync by more than the grace window (Alerting setting DATA_SOURCE_STALE_GRACE_MINUTES, default 60 minutes), or has not refreshed at all within DATA_SOURCE_MAX_SYNC_AGE_HOURS (default 48). Sources with no schedule, and syncs currently in flight, are not checked.',
       threshold = '{"unit": "integrations", "value": 0, "metric": "stale_integrations", "operator": ">", "grace_setting": "alerting.DATA_SOURCE_STALE_GRACE_MINUTES", "max_sync_age_setting": "alerting.DATA_SOURCE_MAX_SYNC_AGE_HOURS"}'::jsonb,
       evaluation_cadence = 'interval'
 WHERE slug = 'data-source-stale';

COMMIT;
