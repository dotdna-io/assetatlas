-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 072: Convert posture_sub_rules.output_value from TEXT to JSONB
--
-- #455 Chunk 3 reshaped output_value from a plain string to a JSON dict
-- ({"mode":"field","field_id":{...}} or {"mode":"text","text":"..."}) but
-- never altered the column type. json.dumps() + TEXT stored a string, and
-- both the Python evaluator (isinstance(raw_output, dict) gate) and the
-- frontend (reads .mode/.field_id) treated the value as a parsed object.
-- Result: posture output silently disabled at runtime, and the Rules UI
-- crashed in encodeFieldId(undefined) when rendering any rule that had
-- output configured.
--
-- Depends on: 071
-- Idempotent: yes (guarded by information_schema lookup)
--
-- All existing rows are either NULL or valid JSON produced by
-- _serialise_output_value, so the USING cast cannot fail.

BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'posture_sub_rules'
          AND column_name = 'output_value'
          AND data_type <> 'jsonb'
    ) THEN
        ALTER TABLE posture_sub_rules
            ALTER COLUMN output_value TYPE jsonb USING output_value::jsonb;
    END IF;
END$$;

COMMIT;
