-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Add temporal tracking to asset_id_mappings
-- Purpose: Add first_seen and last_seen timestamps for 30-day component expiration
-- Created: 2025-10-27

BEGIN;

-- Add temporal tracking columns
ALTER TABLE asset_id_mappings
ADD COLUMN first_seen TIMESTAMP WITH TIME ZONE,
ADD COLUMN last_seen TIMESTAMP WITH TIME ZONE;

-- Populate initial values for existing records (both timestamps = now)
UPDATE asset_id_mappings
SET first_seen = NOW(),
    last_seen = NOW()
WHERE first_seen IS NULL;

-- Make columns NOT NULL now that they have values
ALTER TABLE asset_id_mappings
ALTER COLUMN first_seen SET NOT NULL,
ALTER COLUMN last_seen SET NOT NULL;

-- Set default values for new records
ALTER TABLE asset_id_mappings
ALTER COLUMN first_seen SET DEFAULT NOW(),
ALTER COLUMN last_seen SET DEFAULT NOW();

-- Create index on last_seen for efficient expiration queries
CREATE INDEX idx_asset_id_mappings_last_seen ON asset_id_mappings(last_seen);

-- Add comment explaining the expiration policy
COMMENT ON COLUMN asset_id_mappings.first_seen IS 'Timestamp when component was first seen in the system';
COMMENT ON COLUMN asset_id_mappings.last_seen IS 'Timestamp when component was last seen. Components expire after 30 days of inactivity.';

COMMIT;
