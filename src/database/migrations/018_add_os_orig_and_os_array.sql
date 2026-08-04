-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Add os_orig and os fields to preserve all OS information
-- Date: 2025-10-29
-- Reason: Preserve original OS values and array of all normalized OS values
--         alongside the selected best OS value

BEGIN;

-- Add os_orig field to store original OS values (array)
ALTER TABLE assets
ADD COLUMN IF NOT EXISTS os_orig TEXT[];

-- Add os field to store all normalized OS values (array)
ALTER TABLE assets
ADD COLUMN IF NOT EXISTS os TEXT[];

-- Also add to assets_history table
ALTER TABLE assets_history
ADD COLUMN IF NOT EXISTS os_orig TEXT[];

ALTER TABLE assets_history
ADD COLUMN IF NOT EXISTS os TEXT[];

-- Add comments
COMMENT ON COLUMN assets.os_orig IS 'Original OS values from data sources before normalization';
COMMENT ON COLUMN assets.os IS 'Array of all normalized OS values from all data sources';
COMMENT ON COLUMN assets.os_normalized IS 'Single best OS value selected based on confidence, specificity, and recency';

COMMENT ON COLUMN assets_history.os_orig IS 'Original OS values from data sources before normalization';
COMMENT ON COLUMN assets_history.os IS 'Array of all normalized OS values from all data sources';
COMMENT ON COLUMN assets_history.os_normalized IS 'Single best OS value selected based on confidence, specificity, and recency';

COMMIT;