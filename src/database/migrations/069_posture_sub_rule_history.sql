-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 069: Posture sub-rule history (audit log)
-- Records every CRUD action on posture_sub_rules with before/after JSONB diffs.
-- sub_rule_id is a soft reference (no FK) so delete history survives the row.
-- Depends on: 068
-- Idempotent: yes

BEGIN;

CREATE TABLE IF NOT EXISTS posture_sub_rule_history (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sub_rule_id         UUID NOT NULL,
    category_id         VARCHAR(64) NOT NULL,
    action              TEXT NOT NULL CHECK (action IN ('create','update','toggle','delete')),
    before_state        JSONB,
    after_state         JSONB,
    changed_by          VARCHAR(255) NOT NULL,
    changed_by_username VARCHAR(255),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_posture_sub_rule_history_sub_rule
    ON posture_sub_rule_history(sub_rule_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_posture_sub_rule_history_category
    ON posture_sub_rule_history(category_id, created_at DESC);

COMMIT;
