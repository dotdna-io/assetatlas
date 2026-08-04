-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Drop invalid_assets table
-- Created: 2025-10-29
-- Purpose: Remove invalid_assets table as it's been replaced by excluded_records tracking
-- Reason: Excluded records are now filtered at Stage 4.7 and saved to excluded_records only.
--         The invalid_assets table was creating polluted aggregated data and providing no value.

-- Drop the invalid_assets table
DROP TABLE IF EXISTS invalid_assets CASCADE;

-- Add comment to migration history
COMMENT ON SCHEMA public IS 'Migration 015: Dropped invalid_assets table (2025-10-29). Excluded records now tracked only in excluded_records table.';
