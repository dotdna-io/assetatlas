-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 073: Clear orphan posture state from assets.posture_rules
--
-- #455 Chunk 4 truncated posture_sub_rules (migration 071) but left the
-- per-asset evaluation cache in assets.posture_rules intact. Every category
-- whose sub-rules were wiped continues to report stale pass/fail coverage
-- in the Rules page and through any chart/filter keyed on
-- posture_state:<cat> until the next scheduled posture evaluation runs —
-- which can be hours or days later.
--
-- This migration strips the JSONB keys of any category that currently has
-- zero enabled sub-rules. Categories with enabled rules are left untouched
-- (their evaluation is still valid). Safe to re-run; assets with already
-- clean posture_rules are skipped by the `?|` pre-check.
--
-- The same cleanup runs inline from sub_rule_management_service on
-- delete/toggle-off-last now, so going forward this migration's
-- responsibility is only the historic backlog from 071.
--
-- Depends on: 072
-- Idempotent: yes

BEGIN;

DO $$
DECLARE
    empty_cats text[];
BEGIN
    SELECT array_agg(c.id)
      INTO empty_cats
      FROM posture_categories c
     WHERE NOT EXISTS (
         SELECT 1 FROM posture_sub_rules sr
          WHERE sr.category_id = c.id AND sr.enabled = true
     );

    IF empty_cats IS NOT NULL AND cardinality(empty_cats) > 0 THEN
        UPDATE assets
           SET posture_rules = posture_rules - empty_cats
         WHERE posture_rules ?| empty_cats;
    END IF;
END$$;

COMMIT;
