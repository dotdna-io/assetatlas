-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Fix os_orig to be a single value instead of array
-- Date: 2025-10-29
-- Reason: os_orig should store the single original raw OS value that corresponds
--         to the selected best OS, not an array of all original values

BEGIN;

-- Drop the existing os_orig column (it's currently TEXT[] but should be TEXT)
ALTER TABLE assets
DROP COLUMN IF EXISTS os_orig CASCADE;

-- Add os_orig as TEXT (single value)
ALTER TABLE assets
ADD COLUMN os_orig TEXT;

-- Also update assets_history
ALTER TABLE assets_history
DROP COLUMN IF EXISTS os_orig CASCADE;

ALTER TABLE assets_history
ADD COLUMN os_orig TEXT;

-- Update comments to clarify the schema
COMMENT ON COLUMN assets.os IS 'Array of all raw OS values from all data sources (before normalization)';
COMMENT ON COLUMN assets.os_orig IS 'Single raw OS value that corresponds to the selected best OS (os_normalized)';
COMMENT ON COLUMN assets.os_normalized IS 'Single best normalized OS value selected based on confidence, specificity, and recency';
COMMENT ON COLUMN assets.os_enrichment IS 'OS enrichment data (CPE, vendor, product, etc.) for the selected best OS';

COMMENT ON COLUMN assets_history.os IS 'Array of all raw OS values from all data sources (before normalization)';
COMMENT ON COLUMN assets_history.os_orig IS 'Single raw OS value that corresponds to the selected best OS (os_normalized)';
COMMENT ON COLUMN assets_history.os_normalized IS 'Single best normalized OS value selected based on confidence, specificity, and recency';
COMMENT ON COLUMN assets_history.os_enrichment IS 'OS enrichment data (CPE, vendor, product, etc.) for the selected best OS';

COMMIT;
