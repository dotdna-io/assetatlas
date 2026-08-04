-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 068: Add display_order to posture_sub_rules
-- Per-category ordering for first-wins output selection. Existing rows are
-- backfilled from created_at rank (oldest = lowest display_order = wins first).
-- Depends on: 067
-- Idempotent: yes

BEGIN;

ALTER TABLE posture_sub_rules
    ADD COLUMN IF NOT EXISTS display_order SMALLINT NOT NULL DEFAULT 0;

-- Backfill: per-category, rank by created_at ASC starting at 0. The WHERE clause
-- only touches rows still at default 0 so re-runs don't clobber user edits.
UPDATE posture_sub_rules AS p
SET display_order = ranked.rn - 1
FROM (
    SELECT id, ROW_NUMBER() OVER (PARTITION BY category_id ORDER BY created_at ASC) AS rn
    FROM posture_sub_rules
) AS ranked
WHERE p.id = ranked.id AND p.display_order = 0;

CREATE INDEX IF NOT EXISTS idx_posture_sub_rules_order
    ON posture_sub_rules(category_id, display_order);

COMMIT;
