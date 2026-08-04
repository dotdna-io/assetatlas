-- SPDX-License-Identifier: AGPL-3.0-or-later
-- 040_add_confidence_breakdown.sql
-- Add confidence_breakdown JSONB column to store scoring sub-components.
-- This enables the asset detail UI to show why an asset received its confidence score.
--
-- Schema:
-- {
--   "version": 2,
--   "base_score": 0.82,
--   "source_reliability": 0.88,
--   "source_count_score": 0.89,
--   "data_completeness": 0.65,
--   "consistency_penalty": 1.0,
--   "recency_penalty": 1.0,
--   "identifier_cap": 1.0,
--   "source_count": 4,
--   "identifier_type": "serial_number",
--   "missing_fields": ["location", "owner"],
--   "conflicting_fields": [],
--   "days_since_seen": 0
-- }

BEGIN;

-- Add to assets table
ALTER TABLE assets
    ADD COLUMN IF NOT EXISTS confidence_breakdown JSONB;

COMMENT ON COLUMN assets.confidence_breakdown IS 'Confidence scoring breakdown: sub-scores, penalties, and metadata from AssetScorer V2';

-- Add to assets_history table for archival consistency
ALTER TABLE assets_history
    ADD COLUMN IF NOT EXISTS confidence_breakdown JSONB;

COMMENT ON COLUMN assets_history.confidence_breakdown IS 'Confidence scoring breakdown: sub-scores, penalties, and metadata from AssetScorer V2';

COMMIT;
