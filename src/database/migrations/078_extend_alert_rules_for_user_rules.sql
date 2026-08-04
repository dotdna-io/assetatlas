-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 078: Extend alert_rules for user-created filter-based rules
-- Date: 2026-04-23

BEGIN;

-- User rules don't have a SQL query; make it nullable
ALTER TABLE alert_rules ALTER COLUMN query DROP NOT NULL;

-- New columns for filter-based user rules
ALTER TABLE alert_rules
    ADD COLUMN IF NOT EXISTS target_filters      jsonb,
    ADD COLUMN IF NOT EXISTS compliance_criteria jsonb,
    ADD COLUMN IF NOT EXISTS silence_duration    integer,
    ADD COLUMN IF NOT EXISTS created_by          uuid REFERENCES users(id) ON DELETE SET NULL;

-- Every rule must have exactly one evaluation path:
--   SQL path  : query IS NOT NULL, filters/criteria NULL   (system rules)
--   Filter path: query IS NULL, target_filters or compliance_criteria present (user rules)
ALTER TABLE alert_rules DROP CONSTRAINT IF EXISTS alert_rules_path_check;
ALTER TABLE alert_rules ADD CONSTRAINT alert_rules_path_check
    CHECK (
        (query IS NOT NULL AND target_filters IS NULL AND compliance_criteria IS NULL)
        OR
        (query IS NULL AND (target_filters IS NOT NULL OR compliance_criteria IS NOT NULL))
    );

-- Partial index for evaluator enabled-rules lookup (already filtered in Stage 20)
CREATE INDEX IF NOT EXISTS idx_alert_rules_enabled_source
    ON alert_rules (enabled, source) WHERE enabled = true;

COMMIT;
