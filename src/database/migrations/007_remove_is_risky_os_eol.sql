-- SPDX-License-Identifier: AGPL-3.0-or-later
-- ============================================================================
-- Migration: Remove is_risky and os_eol fields
-- Date: 2025-10-28
-- Description: Remove unused is_risky and os_eol columns and os_eol_list table
-- ============================================================================

-- Drop columns from assets table
ALTER TABLE assets DROP COLUMN IF EXISTS is_risky;
ALTER TABLE assets DROP COLUMN IF EXISTS os_eol;

-- Drop columns from assets_history table
ALTER TABLE assets_history DROP COLUMN IF EXISTS is_risky;
ALTER TABLE assets_history DROP COLUMN IF EXISTS os_eol;

-- Drop os_eol_list table entirely
DROP TABLE IF EXISTS os_eol_list;

-- Success message
DO $$
BEGIN
    RAISE NOTICE 'Migration completed: Removed is_risky, os_eol columns and os_eol_list table';
END
$$;
