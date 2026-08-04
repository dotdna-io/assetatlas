-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 067: Posture metadata + output consistency CHECK
-- - Adds posture_categories.last_evaluated_at for data-freshness indicator
-- - Adds CHECK constraint on posture_sub_rules so output_mode and output_value
--   are both NULL or both NOT NULL
-- Depends on: 066
-- Idempotent: yes

BEGIN;

-- last_evaluated_at: NULL = never evaluated. Written by PostureEvaluationService.
ALTER TABLE posture_categories
    ADD COLUMN IF NOT EXISTS last_evaluated_at TIMESTAMPTZ;

-- output_mode / output_value must be co-null or co-not-null. Existing rows
-- are already consistent (see migration 066 backfill), so no data repair needed.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'posture_sub_rules_output_consistency'
    ) THEN
        ALTER TABLE posture_sub_rules
            ADD CONSTRAINT posture_sub_rules_output_consistency
            CHECK ((output_mode IS NULL AND output_value IS NULL)
                OR (output_mode IS NOT NULL AND output_value IS NOT NULL));
    END IF;
END$$;

COMMIT;
