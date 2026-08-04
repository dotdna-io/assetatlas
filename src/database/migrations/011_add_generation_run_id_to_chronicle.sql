-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Add generation_run_id to asset_chronicle table
-- Purpose: Track which pipeline run generated each chronicle event
-- Date: 2025-10-27

-- Add generation_run_id column to asset_chronicle
ALTER TABLE asset_chronicle
ADD COLUMN IF NOT EXISTS generation_run_id UUID;

-- Create index for efficient querying by run_id
CREATE INDEX IF NOT EXISTS idx_chronicle_generation_run
ON asset_chronicle(generation_run_id);

-- Add comment explaining the field
COMMENT ON COLUMN asset_chronicle.generation_run_id IS
'UUID of the asset generation pipeline run that created this event. Links to asset_generation_service generation_run_id.';

-- Optional: Backfill existing records with NULL (already done by default)
-- UPDATE asset_chronicle SET generation_run_id = NULL WHERE generation_run_id IS NULL;

-- Verify the column was added
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'asset_chronicle'
        AND column_name = 'generation_run_id'
    ) THEN
        RAISE NOTICE 'Column generation_run_id successfully added to asset_chronicle';
    ELSE
        RAISE EXCEPTION 'Failed to add generation_run_id column';
    END IF;
END $$;
