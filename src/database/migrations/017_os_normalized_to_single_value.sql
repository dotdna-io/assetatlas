-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Convert os_normalized from array to single text value
-- Date: 2025-10-29
-- Reason: After implementing OS selection logic, we now select the best OS per asset
--         before aggregation, so we only need a single value, not an array

BEGIN;

-- Convert os_normalized from text[] to text in assets table
-- Take the first element of the array if it exists
ALTER TABLE assets
ALTER COLUMN os_normalized TYPE text
USING CASE
    WHEN os_normalized IS NOT NULL AND array_length(os_normalized, 1) > 0
    THEN os_normalized[1]
    ELSE NULL
END;

-- Also update assets_history table to match
ALTER TABLE assets_history
ALTER COLUMN os_normalized TYPE text
USING CASE
    WHEN os_normalized IS NOT NULL AND array_length(os_normalized, 1) > 0
    THEN os_normalized[1]
    ELSE NULL
END;

-- Add comment explaining the change
COMMENT ON COLUMN assets.os_normalized IS 'Single best OS value selected based on confidence, specificity, and recency';
COMMENT ON COLUMN assets_history.os_normalized IS 'Single best OS value selected based on confidence, specificity, and recency';

COMMIT;