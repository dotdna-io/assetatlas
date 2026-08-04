-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 103: Asset label override (per-asset manual override of the
-- computed asset_label virtual field).
-- Issue: #639

BEGIN;

-- Standalone overrides table — no FK to assets so TRUNCATE CASCADE
-- (run every pipeline) doesn't wipe it. Mirrors category_overrides.
CREATE TABLE IF NOT EXISTS asset_label_overrides (
    asset_id      UUID PRIMARY KEY,
    label         TEXT NOT NULL,
    overridden_by VARCHAR(200),
    active        BOOLEAN NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_asset_label_overrides_active
    ON asset_label_overrides(asset_id) WHERE active = true;

-- Materialized override on assets — re-applied each pipeline run so
-- the asset_label virtual SQL can read it without a join.
ALTER TABLE assets
    ADD COLUMN IF NOT EXISTS asset_label_override TEXT;

COMMIT;
