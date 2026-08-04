-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Report-Based Workflow
-- Created: 2025-11-22
-- Description: Add default report support and transition from dashboard-based to report-based workflow

-- ============================================================================
-- 1. Add is_default column to reports table
-- ============================================================================

-- Add is_default column (defaults to false for existing reports)
ALTER TABLE reports ADD COLUMN IF NOT EXISTS is_default BOOLEAN DEFAULT FALSE;

-- Create unique partial index to ensure only one default report
CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_unique_default
ON reports(is_default)
WHERE is_default = TRUE;

-- Add comment
COMMENT ON COLUMN reports.is_default IS 'Indicates if this is the default report (only one allowed)';

-- ============================================================================
-- 2. Create the default report
-- ============================================================================

-- Insert default report (using a specific UUID for consistency)
INSERT INTO reports (
    id,
    title,
    description,
    charts,
    is_default,
    created_at,
    updated_at
)
VALUES (
    '00000000-0000-0000-0000-000000000001',  -- Special UUID for default report
    'Default Report',
    'This is the default report shown when you open the dashboard. You can edit it, but not delete it.',
    '[]'::jsonb,  -- Start with empty charts array
    TRUE,
    NOW(),
    NOW()
)
ON CONFLICT (id) DO NOTHING;  -- Skip if already exists (migration re-run)

-- ============================================================================
-- 3. Add index for faster default report lookup
-- ============================================================================

-- Create index on is_default for quick lookups
CREATE INDEX IF NOT EXISTS idx_reports_is_default ON reports(is_default) WHERE is_default = TRUE;

-- ============================================================================
-- 4. Migration Complete
-- ============================================================================

-- Verify migration
DO $$
DECLARE
    default_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO default_count FROM reports WHERE is_default = TRUE;

    IF default_count = 0 THEN
        RAISE EXCEPTION 'Migration failed: No default report found';
    ELSIF default_count > 1 THEN
        RAISE EXCEPTION 'Migration failed: Multiple default reports found';
    ELSE
        RAISE NOTICE 'Migration successful: Default report created with ID 00000000-0000-0000-0000-000000000001';
    END IF;
END $$;
