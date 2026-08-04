-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 065: Add output_field column to posture_sub_rules
-- Per-asset posture_rules JSONB shape changes from "state_string" to
-- {"state": "...", "output": "..."} to store the extracted field value
-- from a rule's output_field.
-- Depends on: 064
-- Idempotent: yes

BEGIN;

ALTER TABLE posture_sub_rules
    ADD COLUMN IF NOT EXISTS output_field TEXT;

COMMIT;
