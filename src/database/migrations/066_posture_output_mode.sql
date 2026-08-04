-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 066: Split Rule Output into {mode, value} for field-or-text selection
-- mode='field'  -> value is an asset field name; evaluator fetches the field's value
-- mode='text'   -> value is literal text used as-is
-- mode=NULL     -> no output
-- Depends on: 065
-- Idempotent: yes

BEGIN;

ALTER TABLE posture_sub_rules
    ADD COLUMN IF NOT EXISTS output_mode TEXT
    CHECK (output_mode IN ('field', 'text'));

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'posture_sub_rules' AND column_name = 'output_field'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'posture_sub_rules' AND column_name = 'output_value'
    ) THEN
        ALTER TABLE posture_sub_rules RENAME COLUMN output_field TO output_value;
    END IF;
END$$;

-- Backfill mode for existing rules that have a non-null output value
UPDATE posture_sub_rules
    SET output_mode = 'field'
    WHERE output_value IS NOT NULL AND output_mode IS NULL;

COMMIT;
