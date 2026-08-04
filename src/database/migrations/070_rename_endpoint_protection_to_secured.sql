-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 070: Rename posture category 'endpoint_protection' -> 'secured'
-- Renames:
--   * posture_categories.id             'endpoint_protection' -> 'secured'
--   * posture_categories.name           'Endpoint Protection' -> 'Secured'
--   * posture_sub_rules.category_id     (FK values)
--   * posture_sub_rule_history.category_id column + before_state/after_state JSONB snapshots
--   * assets.posture_rules JSONB key    ('endpoint_protection' -> 'secured')
-- Depends on: 069
-- Idempotent: yes

BEGIN;

-- ---------------------------------------------------------------------------
-- Rename the category row + cascade to sub-rule FK values. The FK is a bare
-- inline REFERENCES (no ON UPDATE CASCADE), so we drop and restore it around
-- the two UPDATEs. DROP IF EXISTS + unconditional ADD keeps this idempotent.
-- ---------------------------------------------------------------------------
ALTER TABLE posture_sub_rules
    DROP CONSTRAINT IF EXISTS posture_sub_rules_category_id_fkey;

UPDATE posture_categories
   SET id = 'secured', name = 'Secured'
 WHERE id = 'endpoint_protection';

UPDATE posture_sub_rules
   SET category_id = 'secured'
 WHERE category_id = 'endpoint_protection';

ALTER TABLE posture_sub_rules
    ADD CONSTRAINT posture_sub_rules_category_id_fkey
    FOREIGN KEY (category_id) REFERENCES posture_categories(id) ON DELETE RESTRICT;

-- ---------------------------------------------------------------------------
-- History table: column + JSONB snapshots. History rows are immutable in
-- spirit, but we rewrite them so future code that uses before_state/after_state
-- to restore a row doesn't try to insert a dangling FK.
-- ---------------------------------------------------------------------------
UPDATE posture_sub_rule_history
   SET category_id = 'secured'
 WHERE category_id = 'endpoint_protection';

UPDATE posture_sub_rule_history
   SET before_state = jsonb_set(before_state, '{category_id}', '"secured"'::jsonb)
 WHERE before_state IS NOT NULL
   AND before_state->>'category_id' = 'endpoint_protection';

UPDATE posture_sub_rule_history
   SET after_state = jsonb_set(after_state, '{category_id}', '"secured"'::jsonb)
 WHERE after_state IS NOT NULL
   AND after_state->>'category_id' = 'endpoint_protection';

-- ---------------------------------------------------------------------------
-- Per-asset evaluated state: rename the JSONB key.
--   posture_rules = {"endpoint_protection": {...}, ...}
--                -> {"secured": {...}, ...}
-- ---------------------------------------------------------------------------
UPDATE assets
   SET posture_rules = (posture_rules - 'endpoint_protection')
                       || jsonb_build_object('secured', posture_rules->'endpoint_protection')
 WHERE posture_rules ? 'endpoint_protection';

COMMIT;
