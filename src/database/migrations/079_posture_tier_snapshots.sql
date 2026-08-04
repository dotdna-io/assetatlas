-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 079: posture_tier_snapshots — historical totals for dashboard deltas
-- Depends on: 064 (posture_rules) and 070 (rename to 'secured')
-- Idempotent: yes (IF NOT EXISTS on table + index)

BEGIN;

CREATE TABLE IF NOT EXISTS posture_tier_snapshots (
    id               BIGSERIAL PRIMARY KEY,
    snapshot_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    secured_count    INTEGER NOT NULL,
    monitored_count  INTEGER NOT NULL,
    discovered_count INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_posture_tier_snapshots_at_desc
    ON posture_tier_snapshots(snapshot_at DESC);

COMMIT;
