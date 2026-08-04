-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 039: Create tables for Edit Asset APIs (Reclassify, Tag, Merge)
-- Date: 2026-02-18
-- Issue: #4 — Tier 2: Edit Asset APIs
-- Changes:
--   - ALTER category_overrides: add subcategory and overridden_by columns
--   - ALTER assets: add merged_into column for merge soft-delete
--   - CREATE asset_manual_tags: per-asset manual tags (separate from rule-based)
--   - CREATE asset_merges: merge audit trail

BEGIN;

-- =============================================================================
-- 1. Extend category_overrides with subcategory and overridden_by
-- =============================================================================
ALTER TABLE category_overrides
    ADD COLUMN IF NOT EXISTS subcategory VARCHAR(100),
    ADD COLUMN IF NOT EXISTS overridden_by VARCHAR(200),
    ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

-- Drop FK so TRUNCATE assets CASCADE (in pipeline) doesn't delete overrides
ALTER TABLE category_overrides
    DROP CONSTRAINT IF EXISTS category_overrides_asset_id_fkey;

-- =============================================================================
-- 2. Add merged_into to assets for merge soft-delete
-- =============================================================================
ALTER TABLE assets
    ADD COLUMN IF NOT EXISTS merged_into UUID;

CREATE INDEX IF NOT EXISTS idx_assets_merged_into
    ON assets(merged_into) WHERE merged_into IS NOT NULL;

-- =============================================================================
-- 3. Manual asset tags (separate from rule-based asset_tags_v2)
-- =============================================================================
CREATE TABLE IF NOT EXISTS asset_manual_tags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id UUID NOT NULL,
    tag_name VARCHAR(100) NOT NULL,
    tag_color VARCHAR(7),
    created_by VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(asset_id, tag_name)
);

CREATE INDEX IF NOT EXISTS idx_asset_manual_tags_asset
    ON asset_manual_tags(asset_id);

-- =============================================================================
-- 4. Merge history / audit trail
-- =============================================================================
CREATE TABLE IF NOT EXISTS asset_merges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    primary_asset_id UUID NOT NULL,
    secondary_asset_id UUID NOT NULL,
    merge_strategy VARCHAR(20) NOT NULL,
    merged_by VARCHAR(200),
    merged_at TIMESTAMPTZ DEFAULT NOW(),
    field_resolutions JSONB
);

CREATE INDEX IF NOT EXISTS idx_asset_merges_primary
    ON asset_merges(primary_asset_id);
CREATE INDEX IF NOT EXISTS idx_asset_merges_secondary
    ON asset_merges(secondary_asset_id);

COMMIT;
