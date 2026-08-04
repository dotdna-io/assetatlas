-- SPDX-License-Identifier: AGPL-3.0-or-later
-- ============================================================================
-- Migration 020: Add Category Prediction to Asset Providers
-- ============================================================================
-- Date: 2025-10-29
-- Description: Add category_prediction JSONB column to asset_providers table
--              for storing rule-based category predictions.
--
--              Predictions happen after OS normalization and MAC vendor enrichment
--              in the asset generation pipeline, using pattern-based rules matching on:
--              - os_normalized
--              - manufacturer
--              - model
--              - mac_vendor_enriched
--
--              Structure: {
--                "category": "server",
--                "subcategory": "server",
--                "confidence": 1.0,
--                "rule_type": "os_pattern"
--              }
--
--              Fallback: If no rule matches, category="miscellaneous",
--                       subcategory="unknown", confidence=0.5
-- ============================================================================

-- Add category prediction column to asset_providers
ALTER TABLE asset_providers
    ADD COLUMN IF NOT EXISTS category_prediction JSONB;

-- Create index for category prediction queries
CREATE INDEX IF NOT EXISTS idx_providers_category_prediction
    ON asset_providers USING GIN(category_prediction)
    WHERE category_prediction IS NOT NULL;

-- Add comment for documentation
COMMENT ON COLUMN asset_providers.category_prediction IS
'Rule-based category prediction result stored as JSONB.
Structure: {"category": "server", "subcategory": "server", "confidence": 1.0, "rule_type": "os_pattern"}
Fallback: {"category": "miscellaneous", "subcategory": "unknown", "confidence": 0.5, "rule_type": "fallback"}';

-- Update assets table to support category/subcategory arrays
-- These will be populated from multiple provider records during aggregation
ALTER TABLE assets
    ALTER COLUMN category TYPE TEXT[] USING ARRAY[category]::TEXT[],
    ALTER COLUMN subcategory TYPE TEXT[] USING ARRAY[subcategory]::TEXT[];

-- Add comments
COMMENT ON COLUMN assets.category IS
'Array of categories aggregated from all provider records for this asset';

COMMENT ON COLUMN assets.subcategory IS
'Array of subcategories aggregated from all provider records for this asset';

-- Note: Existing data will be preserved and wrapped in arrays
