-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Add os_normalized and category to asset_providers
-- Date: 2025-11-03
-- Purpose: Enable consistency checking for OS and category across data sources in V2 scoring
--
-- Adds:
-- - os_normalized (TEXT): Normalized OS value for consistency checks
-- - category (TEXT): Asset category for consistency checks
-- - subcategory (TEXT): Asset subcategory for consistency checks
--
-- These fields allow V2 confidence scoring to detect when different data sources
-- report different OS normalizations or category classifications for the same asset.

-- Add os_normalized column
ALTER TABLE asset_providers
ADD COLUMN IF NOT EXISTS os_normalized TEXT;

-- Add category column
ALTER TABLE asset_providers
ADD COLUMN IF NOT EXISTS category TEXT;

-- Add subcategory column
ALTER TABLE asset_providers
ADD COLUMN IF NOT EXISTS subcategory TEXT;

-- Add indexes for these columns (useful for consistency queries)
CREATE INDEX IF NOT EXISTS idx_providers_os_normalized ON asset_providers(os_normalized)
WHERE os_normalized IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_providers_category ON asset_providers(category)
WHERE category IS NOT NULL;

-- Add comments
COMMENT ON COLUMN asset_providers.os_normalized IS 'Normalized OS value from this provider (for consistency checks)';
COMMENT ON COLUMN asset_providers.category IS 'Asset category from this provider (for consistency checks)';
COMMENT ON COLUMN asset_providers.subcategory IS 'Asset subcategory from this provider (for consistency checks)';
