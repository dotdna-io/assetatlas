-- SPDX-License-Identifier: AGPL-3.0-or-later
-- 111_posture_coverage_snapshots.sql
-- Per-(posture control × asset category) coverage snapshots, written once per
-- pipeline run by the posture evaluation stage (alongside posture_tier_snapshots).
-- Backs the dashboard Trend card's posture-coverage metrics.
BEGIN;

CREATE TABLE IF NOT EXISTS posture_coverage_snapshots (
    id             BIGSERIAL PRIMARY KEY,
    snapshot_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    control_id     TEXT NOT NULL,
    asset_category TEXT NOT NULL,
    pass_count     INTEGER NOT NULL,   -- posture_rules -> control ->> 'state' = 'true'  (covered)
    fail_count     INTEGER NOT NULL,   -- 'false' (in scope, uncovered)
    na_count       INTEGER NOT NULL    -- 'n/a' or missing (out of scope)
);

CREATE INDEX IF NOT EXISTS idx_posture_coverage_snapshots_lookup
    ON posture_coverage_snapshots (control_id, snapshot_at DESC);

COMMIT;
