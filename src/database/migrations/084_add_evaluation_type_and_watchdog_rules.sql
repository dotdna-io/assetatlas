-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 082: Add evaluation_type column and seed pipeline-watchdog rules
-- Date: 2026-04-24
-- Requires: migration 078 (target_filters, compliance_criteria columns)
--
-- Introduces a third evaluation path ('external') for rules that are evaluated
-- by an out-of-band service (the pipeline watchdog) rather than the in-pipeline
-- alert evaluator. Seeds three system-owned rules used by the watchdog.

BEGIN;

-- 1. Add the evaluation_type dimension. Default 'sql' so any pre-existing row
--    the backfill misses keeps the pre-migration behavior.
ALTER TABLE alert_rules
    ADD COLUMN IF NOT EXISTS evaluation_type TEXT NOT NULL DEFAULT 'sql'
        CHECK (evaluation_type IN ('sql', 'filter', 'external'));

-- 2. Backfill: rows with a SQL query are 'sql'; everything else is 'filter'
--    (matches the exclusive invariant the old alert_rules_path_check enforced).
--    Skip rows already tagged 'external' so re-runs don't clobber the watchdog rules.
UPDATE alert_rules
   SET evaluation_type = CASE
       WHEN query IS NOT NULL THEN 'sql'
       ELSE 'filter'
   END
 WHERE evaluation_type != 'external';

-- 3. Replace path check with evaluation_type-aware version that also permits
--    external rules with no query and no filter payload.
ALTER TABLE alert_rules DROP CONSTRAINT IF EXISTS alert_rules_path_check;
ALTER TABLE alert_rules ADD CONSTRAINT alert_rules_path_check
    CHECK (
        (evaluation_type = 'sql'
            AND query IS NOT NULL
            AND target_filters IS NULL AND compliance_criteria IS NULL)
     OR (evaluation_type = 'filter'
            AND query IS NULL
            AND (target_filters IS NOT NULL OR compliance_criteria IS NOT NULL))
     OR (evaluation_type = 'external'
            AND query IS NULL
            AND target_filters IS NULL AND compliance_criteria IS NULL)
    );

-- 4. Seed the three watchdog rules. ON CONFLICT makes this re-runnable.
INSERT INTO alert_rules
    (name, slug, type, severity, description, evaluation_type, source)
VALUES
    ('Pipeline stale',
     'pipeline-stale', 'operational', 'critical',
     'Fires when no successful asset-generation run has completed in 2x the expected cadence.',
     'external', 'system'),
    ('Pipeline last run failed',
     'pipeline-last-run-failed', 'operational', 'high',
     'Fires when the most recently finished asset-generation run ended in failure.',
     'external', 'system'),
    ('Pipeline stuck',
     'pipeline-stuck', 'operational', 'high',
     'Fires when a pipeline run has been running (or pending) longer than the configured max expected duration.',
     'external', 'system')
ON CONFLICT (slug) DO NOTHING;

COMMIT;
