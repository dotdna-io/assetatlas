-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 014: Add OS Enrichment JSONB Column
-- This migration adds a JSONB column to store OS enrichment data from the normalizer
-- Including: CPE, vendor, product, version, family, and confidence

-- Add os_enrichment JSONB column to assets table
ALTER TABLE assets
ADD COLUMN os_enrichment jsonb;

-- Create GIN index for efficient JSONB queries
CREATE INDEX idx_assets_os_enrichment ON assets USING gin(os_enrichment);

-- Add comment explaining the column
COMMENT ON COLUMN assets.os_enrichment IS 'OS enrichment data from normalizer including CPE, vendor, product, version, family, and confidence';

-- Example structure:
-- {
--   "cpe": "cpe:2.3:o:microsoft:windows_10:*:*:*:*:*:*:*:*",
--   "vendor": "microsoft",
--   "product": "windows_10",
--   "version": "10.0.19045",
--   "os_family": "windows",
--   "confidence": 0.95
-- }

-- Add os_enrichment to assets_history table as well
ALTER TABLE assets_history
ADD COLUMN os_enrichment jsonb;

COMMENT ON COLUMN assets_history.os_enrichment IS 'OS enrichment data from normalizer (historical snapshot)';
