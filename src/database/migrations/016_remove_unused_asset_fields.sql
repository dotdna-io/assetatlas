-- SPDX-License-Identifier: AGPL-3.0-or-later
-- ============================================================================
-- Migration: Remove Unused Asset Fields
-- Date: 2025-10-29
-- Description: Remove fields that are no longer needed:
--   - orig_category: ML prediction storage (ML disabled)
--   - is_compliant: Compliance tracking (not implemented)
--   - description: General description (can use extended_attributes)
--   - av: Antivirus info (can use extended_attributes)
--   - domain: Domain membership (can use extended_attributes)
-- ============================================================================

-- Start transaction
BEGIN;

-- ----------------------------------------------------------------------------
-- Remove columns from assets table
-- ----------------------------------------------------------------------------
ALTER TABLE assets
    DROP COLUMN IF EXISTS orig_category,
    DROP COLUMN IF EXISTS is_compliant,
    DROP COLUMN IF EXISTS description,
    DROP COLUMN IF EXISTS av,
    DROP COLUMN IF EXISTS domain;

-- ----------------------------------------------------------------------------
-- Remove columns from assets_history table
-- ----------------------------------------------------------------------------
ALTER TABLE assets_history
    DROP COLUMN IF EXISTS orig_category,
    DROP COLUMN IF EXISTS is_compliant,
    DROP COLUMN IF EXISTS description,
    DROP COLUMN IF EXISTS av,
    DROP COLUMN IF EXISTS domain;

-- ----------------------------------------------------------------------------
-- Log migration
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    RAISE NOTICE 'Migration 016_remove_unused_asset_fields completed successfully';
    RAISE NOTICE 'Removed fields: orig_category, is_compliant, description, av, domain';
    RAISE NOTICE 'Data can still be stored in extended_attributes JSONB if needed';
END $$;

COMMIT;