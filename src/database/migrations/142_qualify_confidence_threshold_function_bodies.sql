-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 142: qualify system_settings inside the confidence-threshold
-- function bodies, so that what pg_dump produces can actually be restored.
--
-- pg_dump emits `SELECT pg_catalog.set_config('search_path', '', false)` -- the
-- CVE-2018-1058 hardening -- and schema-qualifies every name it generates. What
-- it cannot re-qualify is a function BODY: a body is opaque text to pg_dump,
-- copied out verbatim. So the unqualified `FROM system_settings` that migration
-- 110 wrote into confidence_threshold_high()/_medium() survives into every
-- backup exactly as written, and lands in a session whose search_path is empty.
--
-- Both functions are LANGUAGE sql STABLE and four dashboard materialized views
-- call them, so the planner inlines them. `CREATE MATERIALIZED VIEW ... WITH NO
-- DATA` -- what pg_dump emits -- only parses the query, so it survives. The
-- `REFRESH MATERIALIZED VIEW` pg_dump emits in its post-data section PLANS it,
-- inlining expands the body, and the table does not resolve:
--
--   ERROR:  relation "system_settings" does not exist
--   CONTEXT:  SQL function "confidence_threshold_high" during inlining
--
-- deploy/lib/backup.sh's restore_db pipes the dump through `psql -v
-- ON_ERROR_STOP=1`, so that aborts and restore_db dies. Its three callers are
-- all recovery paths -- update.sh's auto-rollback (:324), `update.sh --rollback`
-- (:161), `assetatlas-cli restore` (:237) -- and each one calls restore_db with
-- the stack down and only the DB container up, bringing the rest back on the
-- NEXT line. So the die does not merely fail the restore: it strands the
-- deployment with nothing but a database running (#1196). The nightly
-- assetatlas-backup.timer has meanwhile been writing dumps with the same defect
-- on every install.
--
-- Qualifying the table fixes it at the source and needs no search_path change
-- anywhere. In particular it is NOT fixed by filtering pg_dump's set_config line
-- out of the restore: a restore typically runs as superuser, so that would
-- re-open CVE-2018-1058 -- unqualified names resolving against a permissive path
-- and executing an attacker-planted object that shadows a pg_catalog one.
--
-- FORWARD-LOOKING ONLY. A dump captures the body as it was at dump time, so
-- backups already on disk stay unrestorable and a host keeps producing
-- unrestorable backups until this migration is applied there.
--
-- The bodies below are otherwise byte-identical to migration 110: same
-- (unqualified) function names, same `RETURNS float`, same LANGUAGE sql STABLE,
-- same COALESCE fallbacks to the 0.70/0.45 schema defaults. `float` is what
-- Postgres records as `double precision`, so the return type is unchanged --
-- which matters, because a changed return type is the one thing CREATE OR
-- REPLACE FUNCTION refuses.
--
-- Migration 110 is deliberately NOT edited. Applied migrations are history and
-- the filename is the primary key of schema_migrations.
--
-- The four dependents (mv_asset_quality_metrics, mv_dashboard_summary,
-- mv_provider_coverage, mv_provider_coverage_grouped) need no recreating and no
-- refresh. CREATE OR REPLACE keeps the function's OID, so the pg_depend rows the
-- views hold on it stay valid; and a view stores the CALL, not the expansion --
-- inlining happens at plan time, against whatever the body says then. Their
-- definitions and their populated contents are untouched, and the next refresh
-- (dashboard_repository.refresh_views, or the pipeline) picks up the new body.
--
-- Idempotent: CREATE OR REPLACE writes the same body over itself, so re-running
-- is a no-op. Dual-written to schema-full.sql for fresh installs.
BEGIN;

CREATE OR REPLACE FUNCTION confidence_threshold_high() RETURNS float
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(
        (SELECT value::float FROM public.system_settings
         WHERE category = 'asset_generation' AND key = 'confidence_threshold_high'), 0.70
    );
$$;

CREATE OR REPLACE FUNCTION confidence_threshold_medium() RETURNS float
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(
        (SELECT value::float FROM public.system_settings
         WHERE category = 'asset_generation' AND key = 'confidence_threshold_medium'), 0.45
    );
$$;

COMMIT;
