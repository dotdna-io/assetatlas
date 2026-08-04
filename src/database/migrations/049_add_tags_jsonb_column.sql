-- SPDX-License-Identifier: AGPL-3.0-or-later
BEGIN;

-- Add tags JSONB column to assets table
ALTER TABLE assets ADD COLUMN IF NOT EXISTS tags JSONB DEFAULT '{}'::jsonb;

-- GIN index for fast JSONB key lookups
CREATE INDEX IF NOT EXISTS idx_assets_tags ON assets USING GIN(tags);

-- Backfill: build tags map from existing asset_tags_v2 + asset_manual_tags
UPDATE assets a
SET tags = COALESCE(combined.tag_map, '{}'::jsonb)
FROM (
    SELECT
        asset_id,
        jsonb_object_agg(tag_name, true) AS tag_map
    FROM (
        SELECT asset_id, tag_name FROM asset_tags_v2
        UNION
        SELECT asset_id, tag_name FROM asset_manual_tags
    ) all_tags
    GROUP BY asset_id
) combined
WHERE a.id = combined.asset_id;

COMMIT;
