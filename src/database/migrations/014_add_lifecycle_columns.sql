-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Add Lifecycle Tracking Columns
-- Purpose: Enable asset lifecycle management and change tracking
-- Date: 2025-10-31
-- Phase: Phase 1 - Change Tracking Enhancement
--
-- Changes:
-- 1. Add lifecycle metadata columns to assets table
-- 2. Add same columns to assets_history table (for consistency)
-- 3. Create indexes for efficient lifecycle queries
-- 4. Backfill first_seen from history (optional, for existing assets)

-- =========================================================================
-- STEP 1: Add lifecycle columns to assets table
-- =========================================================================

-- When was this asset first discovered in any generation run?
ALTER TABLE assets
ADD COLUMN IF NOT EXISTS first_seen TIMESTAMPTZ;

-- When was this asset last seen in a generation run?
ALTER TABLE assets
ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ;

-- Which generation run last updated this asset?
ALTER TABLE assets
ADD COLUMN IF NOT EXISTS last_generation_run_id UUID;

-- Add column comments
COMMENT ON COLUMN assets.first_seen IS
'Timestamp when this asset was first discovered in any generation run. Preserved across runs for lifecycle tracking.';

COMMENT ON COLUMN assets.last_seen IS
'Timestamp when this asset was last seen in a generation run. Updated every run the asset appears. Used for inactivity detection.';

COMMENT ON COLUMN assets.last_generation_run_id IS
'UUID of the generation run that last updated this asset. Links to generation_run_id in pipeline.';

-- =========================================================================
-- STEP 2: Add lifecycle columns to assets_history table
-- =========================================================================

-- Add same columns to history for consistency
ALTER TABLE assets_history
ADD COLUMN IF NOT EXISTS first_seen TIMESTAMPTZ;

ALTER TABLE assets_history
ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ;

ALTER TABLE assets_history
ADD COLUMN IF NOT EXISTS last_generation_run_id UUID;

-- Add column comments
COMMENT ON COLUMN assets_history.first_seen IS
'Historical snapshot: When this asset was first discovered (as of this archive time).';

COMMENT ON COLUMN assets_history.last_seen IS
'Historical snapshot: When this asset was last seen (as of this archive time).';

COMMENT ON COLUMN assets_history.last_generation_run_id IS
'Historical snapshot: Which generation run created this snapshot.';

-- =========================================================================
-- STEP 3: Create indexes for efficient lifecycle queries
-- =========================================================================

-- Index for "assets not seen in N days" queries
CREATE INDEX IF NOT EXISTS idx_assets_last_seen
ON assets(last_seen DESC);

-- Index for "newly discovered assets" queries
CREATE INDEX IF NOT EXISTS idx_assets_first_seen
ON assets(first_seen);

-- Index for generation run correlation
CREATE INDEX IF NOT EXISTS idx_assets_last_run
ON assets(last_generation_run_id);

-- Index on history for first_seen backfill queries
CREATE INDEX IF NOT EXISTS idx_assets_history_first_seen
ON assets_history(id, first_seen);

COMMENT ON INDEX idx_assets_last_seen IS
'Supports queries like "show assets not seen in 30 days" for compliance reporting.';

COMMENT ON INDEX idx_assets_first_seen IS
'Supports queries like "show newly discovered assets" for change tracking.';

COMMENT ON INDEX idx_assets_last_run IS
'Links assets to their last generation run for audit trail and debugging.';

-- =========================================================================
-- STEP 4: Optional backfill for existing assets
-- =========================================================================

-- For existing assets, estimate first_seen from history
-- Note: This is optional and may take time on large datasets
-- Can be run as a background job if needed

DO $$
DECLARE
    assets_to_backfill INTEGER;
    history_records INTEGER;
BEGIN
    -- Count how many assets need backfill
    SELECT COUNT(*) INTO assets_to_backfill
    FROM assets
    WHERE first_seen IS NULL;

    -- Count available history records
    SELECT COUNT(DISTINCT id) INTO history_records
    FROM assets_history
    WHERE first_seen IS NOT NULL OR archived_at IS NOT NULL;

    RAISE NOTICE '====================================';
    RAISE NOTICE 'Lifecycle Columns Backfill Analysis';
    RAISE NOTICE '====================================';
    RAISE NOTICE 'Assets needing first_seen backfill: %', assets_to_backfill;
    RAISE NOTICE 'Unique assets in history: %', history_records;
    RAISE NOTICE '';

    IF assets_to_backfill > 0 AND history_records > 0 THEN
        RAISE NOTICE 'Backfilling first_seen from history...';
        RAISE NOTICE 'This estimates first_seen as earliest archived_at for each asset.';
        RAISE NOTICE '';

        -- Backfill first_seen from earliest appearance in history
        -- Use archived_at as proxy if first_seen not available in history
        UPDATE assets a
        SET first_seen = (
            SELECT COALESCE(MIN(ah.first_seen), MIN(ah.archived_at))
            FROM assets_history ah
            WHERE ah.id = a.id
        )
        WHERE a.first_seen IS NULL;

        RAISE NOTICE '✓ Backfilled first_seen for % assets', assets_to_backfill;
    ELSE
        RAISE NOTICE 'No backfill needed or no history available';
    END IF;

    -- Note: last_seen and last_generation_run_id will be populated
    -- by the next pipeline run, so we don't backfill them
    RAISE NOTICE '';
    RAISE NOTICE 'Note: last_seen and last_generation_run_id will be populated';
    RAISE NOTICE 'automatically by the next asset generation pipeline run.';
END $$;

-- =========================================================================
-- STEP 5: Verification and statistics
-- =========================================================================

DO $$
DECLARE
    missing_columns TEXT[] := ARRAY[]::TEXT[];
    expected_columns TEXT[] := ARRAY[
        'first_seen',
        'last_seen',
        'last_generation_run_id'
    ];
    col TEXT;
    assets_count INTEGER;
    assets_with_first_seen INTEGER;
    assets_history_count INTEGER;
BEGIN
    -- Verify all columns exist in assets table
    FOREACH col IN ARRAY expected_columns
    LOOP
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'assets'
            AND column_name = col
        ) THEN
            missing_columns := array_append(missing_columns, 'assets.' || col);
        END IF;
    END LOOP;

    -- Verify all columns exist in assets_history table
    FOREACH col IN ARRAY expected_columns
    LOOP
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'assets_history'
            AND column_name = col
        ) THEN
            missing_columns := array_append(missing_columns, 'assets_history.' || col);
        END IF;
    END LOOP;

    IF array_length(missing_columns, 1) > 0 THEN
        RAISE EXCEPTION 'Missing columns: %', array_to_string(missing_columns, ', ');
    END IF;

    -- Gather statistics
    SELECT COUNT(*) INTO assets_count FROM assets;
    SELECT COUNT(*) FILTER (WHERE first_seen IS NOT NULL) INTO assets_with_first_seen FROM assets;
    SELECT COUNT(*) INTO assets_history_count FROM assets_history;

    RAISE NOTICE '====================================';
    RAISE NOTICE 'Migration 014 completed successfully';
    RAISE NOTICE '====================================';
    RAISE NOTICE '';
    RAISE NOTICE 'Schema Changes:';
    RAISE NOTICE '  ✓ Added 3 columns to assets table';
    RAISE NOTICE '  ✓ Added 3 columns to assets_history table';
    RAISE NOTICE '  ✓ Created 4 indexes for lifecycle queries';
    RAISE NOTICE '';
    RAISE NOTICE 'Current State:';
    RAISE NOTICE '  Total assets: %', assets_count;
    RAISE NOTICE '  Assets with first_seen: %', assets_with_first_seen;
    RAISE NOTICE '  Total history records: %', assets_history_count;
    RAISE NOTICE '';
    RAISE NOTICE 'Next Steps:';
    RAISE NOTICE '  1. Update asset_repository.py with get_first_seen_dates()';
    RAISE NOTICE '  2. Enhance Stage 9 to populate lifecycle columns';
    RAISE NOTICE '  3. Enable track_changes=True in run script';
    RAISE NOTICE '  4. Run asset generation pipeline to test';
    RAISE NOTICE '';
    RAISE NOTICE 'Phase 1 Complete: Change tracking foundation ready!';
    RAISE NOTICE '====================================';
END $$;
