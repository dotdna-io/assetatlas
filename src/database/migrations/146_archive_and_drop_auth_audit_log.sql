-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 146: archive auth_audit_log by rename and retire its settings knob.
-- Part of #1294 — PR-B, the final step of the auth_audit_log retirement
-- (PR-0 repointed the last seeded alert rule to audit_trail, PR-A stopped the
-- dual-write so the table has been frozen read-only ever since).
--
-- The original PR-B plan was an operator-side archive (COPY the table to a
-- file on each box) followed by DROP TABLE in this migration. That split does
-- not survive contact with the fleet: customer updates AUTO-RUN migrations,
-- so any step that lives in a runbook can be skipped — and a skipped archive
-- step means the drop destroys the only copy of the compliance history.
-- Archive-by-rename moves the archive INSIDE the migration: renaming to
-- auth_audit_log_archive is zero-copy (data, indexes, the primary key, and
-- the users FK all follow the rename) and instantly consistent, because PR-A
-- already stopped every writer. The archive table is left in place for manual
-- cleanup (pg_dump to file, then DROP) at each deployment's discretion — no
-- migration will remove it, since only the operator can say the history has
-- been preserved off-box.
--
-- Order matters: the RAISE guard runs FIRST. If any alert rule still
-- references auth_audit_log — a customer-authored rule, since PR-0 repointed
-- the last seeded one — the migration fails loudly at apply time instead of
-- silently breaking that rule's alerting against a renamed table. Repoint or
-- delete the rule by hand, then re-run.
--
-- Idempotent: the rename is guarded on to_regclass, so a re-run after a
-- successful apply finds no public.auth_audit_log and does nothing (fresh
-- installs bootstrap from schema-full.sql, which no longer carries the table
-- at all, and hit the same no-op); the guard passes trivially once no rule
-- references the table; the settings DELETE matches zero rows on a re-run.
-- Dual-written to src/database/schema-full.sql: fresh installs get neither
-- the table nor the archive, and this filename is appended to the
-- schema_migrations stamp block.
BEGIN;

-- 1. Fail loudly if any alert rule still queries the table. After a
--    successful apply the table is gone, so this guard can only ever fire
--    BEFORE the rename — never on a legitimate re-run.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM alert_rules WHERE query ILIKE '%auth_audit_log%') THEN
        RAISE EXCEPTION 'auth_audit_log cannot be archived: at least one alert_rules.query still references it (%). Repoint the rule at audit_trail (category=''auth'', fields from details) or delete it, then re-run migration 146.',
            (SELECT string_agg(name, ', ') FROM alert_rules WHERE query ILIKE '%auth_audit_log%');
    END IF;
END $$;

-- 2. Archive by rename. Zero-copy: rows, indexes, PK, and the users FK all
--    follow the table name. Index/constraint names keep their old prefixes —
--    harmless on an archive table nothing queries by name.
DO $$
BEGIN
    IF to_regclass('public.auth_audit_log') IS NOT NULL THEN
        ALTER TABLE public.auth_audit_log RENAME TO auth_audit_log_archive;
    END IF;
END $$;

-- 3. Retire the retention knob. The data_retention entry and the settings
--    schema definition are removed in the same PR; this clears the seeded row
--    on existing installs. Idempotent by nature.
DELETE FROM system_settings
WHERE category = 'retention' AND key = 'AUTH_AUDIT_LOG_RETENTION_DAYS';

COMMIT;
