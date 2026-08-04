-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Consolidated Chronicle Schema
-- Purpose: Add columns and indexes for consolidated event tracking
-- Date: 2025-10-27
--
-- Changes:
-- 1. Add consolidated event columns (changed_attributes, attribute_changes, full_snapshot)
-- 2. Add audit trail columns (operation_id, causation, related_asset_id)
-- 3. Change primary key from (time, asset_id) to BIGSERIAL id
-- 4. Add GIN index for efficient array queries
-- 5. Add indexes for common query patterns

-- =========================================================================
-- STEP 1: Add new columns for consolidated events
-- =========================================================================

-- Array of attribute names that changed in this event
ALTER TABLE asset_chronicle
ADD COLUMN IF NOT EXISTS changed_attributes TEXT[];

-- JSONB map of attribute changes {attribute_name: {old: [...], new: [...], added: [...], removed: [...]}}
ALTER TABLE asset_chronicle
ADD COLUMN IF NOT EXISTS attribute_changes JSONB;

-- Full asset snapshot at event time (for creation, re-add, periodic snapshots)
ALTER TABLE asset_chronicle
ADD COLUMN IF NOT EXISTS full_snapshot JSONB;

-- Comments for new columns
COMMENT ON COLUMN asset_chronicle.changed_attributes IS
'Array of attribute names that changed in this event. Enables efficient queries like "find all events where hostname changed"';

COMMENT ON COLUMN asset_chronicle.attribute_changes IS
'JSONB map of detailed changes: {attr: {old: [...], new: [...], added: [...], removed: [...]}}. Provides granular change details.';

COMMENT ON COLUMN asset_chronicle.full_snapshot IS
'Complete asset state at event time. Used for asset_created, asset_readded, and periodic snapshots. Enables point-in-time reconstruction without replaying all events.';

-- =========================================================================
-- STEP 2: Add audit trail columns
-- =========================================================================

-- UUID linking related events in a single operation
ALTER TABLE asset_chronicle
ADD COLUMN IF NOT EXISTS operation_id UUID;

-- Why this event occurred (e.g., 'scheduled_scan', 'manual_update', 'merge_operation')
ALTER TABLE asset_chronicle
ADD COLUMN IF NOT EXISTS causation TEXT;

-- Related asset (for merge operations)
ALTER TABLE asset_chronicle
ADD COLUMN IF NOT EXISTS related_asset_id UUID;

-- Comments for audit columns
COMMENT ON COLUMN asset_chronicle.operation_id IS
'Links all events that occurred as part of a single logical operation (e.g., merge operation affecting multiple assets)';

COMMENT ON COLUMN asset_chronicle.causation IS
'Why this event occurred: scheduled_scan, manual_update, merge_operation, data_import, etc. Enables root cause analysis.';

COMMENT ON COLUMN asset_chronicle.related_asset_id IS
'For operations involving multiple assets (like merges), references the related asset. Enables tracking asset relationships.';

-- =========================================================================
-- STEP 3: Change primary key from (time, asset_id) to (time, id)
-- =========================================================================

-- Add new BIGSERIAL id column first
ALTER TABLE asset_chronicle
ADD COLUMN IF NOT EXISTS id BIGSERIAL;

-- Drop the existing primary key constraint
-- NOTE: This is a potentially expensive operation on large tables
DO $$
BEGIN
    -- Check if the constraint exists before trying to drop it
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'asset_chronicle_pkey'
    ) THEN
        ALTER TABLE asset_chronicle DROP CONSTRAINT asset_chronicle_pkey;
        RAISE NOTICE 'Dropped existing primary key constraint (time, asset_id)';
    END IF;
END $$;

-- Create composite primary key (time, id)
-- TimescaleDB requires partitioning column (time) to be part of PK
ALTER TABLE asset_chronicle
ADD PRIMARY KEY (time, id);

DO $$
BEGIN
    RAISE NOTICE 'Added new composite primary key (time, id)';
END $$;

-- =========================================================================
-- STEP 4: Add unique constraint on (time, asset_id)
-- =========================================================================

-- Ensure we still maintain uniqueness of (time, asset_id)
-- This prevents duplicate events at the same microsecond
CREATE UNIQUE INDEX IF NOT EXISTS idx_chronicle_time_asset_unique
ON asset_chronicle(time, asset_id);

COMMENT ON INDEX idx_chronicle_time_asset_unique IS
'Ensures no duplicate events for the same asset at the same timestamp. Previously was the primary key.';

-- =========================================================================
-- STEP 5: Create indexes for efficient querying
-- =========================================================================

-- GIN index for efficient array queries on changed_attributes
-- Enables fast queries like: WHERE 'hostname' = ANY(changed_attributes)
CREATE INDEX IF NOT EXISTS idx_chronicle_changed_attributes
ON asset_chronicle USING GIN (changed_attributes);

COMMENT ON INDEX idx_chronicle_changed_attributes IS
'GIN index for array queries. Enables fast lookups like "find all events where hostname changed"';

-- Index for operation_id (audit trail queries)
CREATE INDEX IF NOT EXISTS idx_chronicle_operation_id
ON asset_chronicle(operation_id)
WHERE operation_id IS NOT NULL;

COMMENT ON INDEX idx_chronicle_operation_id IS
'Partial index for operation_id. Enables fast queries for all events in a logical operation.';

-- Index for related_asset_id (merge tracking)
CREATE INDEX IF NOT EXISTS idx_chronicle_related_asset
ON asset_chronicle(related_asset_id)
WHERE related_asset_id IS NOT NULL;

COMMENT ON INDEX idx_chronicle_related_asset IS
'Partial index for related_asset_id. Enables fast queries for merge-related events.';

-- Composite index for common time-range + asset queries
CREATE INDEX IF NOT EXISTS idx_chronicle_time_asset
ON asset_chronicle(time DESC, asset_id);

COMMENT ON INDEX idx_chronicle_time_asset IS
'Optimizes time-range queries for specific assets. Supports efficient event history retrieval.';

-- Index for event_type filtering (for reporting)
CREATE INDEX IF NOT EXISTS idx_chronicle_event_type
ON asset_chronicle(event_type);

COMMENT ON INDEX idx_chronicle_event_type IS
'Enables efficient filtering by event type in reports.';

-- =========================================================================
-- STEP 6: Verification and statistics
-- =========================================================================

-- Verify all columns exist
DO $$
DECLARE
    missing_columns TEXT[] := ARRAY[]::TEXT[];
    expected_columns TEXT[] := ARRAY[
        'changed_attributes',
        'attribute_changes',
        'full_snapshot',
        'operation_id',
        'causation',
        'related_asset_id',
        'id'
    ];
    col TEXT;
BEGIN
    FOREACH col IN ARRAY expected_columns
    LOOP
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'asset_chronicle'
            AND column_name = col
        ) THEN
            missing_columns := array_append(missing_columns, col);
        END IF;
    END LOOP;

    IF array_length(missing_columns, 1) > 0 THEN
        RAISE EXCEPTION 'Missing columns: %', array_to_string(missing_columns, ', ');
    ELSE
        RAISE NOTICE '✓ All new columns successfully added to asset_chronicle';
    END IF;
END $$;

-- Verify indexes exist
DO $$
DECLARE
    missing_indexes TEXT[] := ARRAY[]::TEXT[];
    expected_indexes TEXT[] := ARRAY[
        'idx_chronicle_changed_attributes',
        'idx_chronicle_operation_id',
        'idx_chronicle_related_asset',
        'idx_chronicle_time_asset',
        'idx_chronicle_event_type',
        'idx_chronicle_time_asset_unique'
    ];
    idx TEXT;
BEGIN
    FOREACH idx IN ARRAY expected_indexes
    LOOP
        IF NOT EXISTS (
            SELECT 1
            FROM pg_indexes
            WHERE indexname = idx
        ) THEN
            missing_indexes := array_append(missing_indexes, idx);
        END IF;
    END LOOP;

    IF array_length(missing_indexes, 1) > 0 THEN
        RAISE EXCEPTION 'Missing indexes: %', array_to_string(missing_indexes, ', ');
    ELSE
        RAISE NOTICE '✓ All new indexes successfully created';
    END IF;
END $$;

-- Display table statistics
DO $$
DECLARE
    row_count BIGINT;
    table_size TEXT;
BEGIN
    SELECT COUNT(*) INTO row_count FROM asset_chronicle;
    SELECT pg_size_pretty(pg_total_relation_size('asset_chronicle')) INTO table_size;

    RAISE NOTICE '====================================';
    RAISE NOTICE 'Migration 012 completed successfully';
    RAISE NOTICE '====================================';
    RAISE NOTICE 'Total chronicle events: %', row_count;
    RAISE NOTICE 'Table size: %', table_size;
    RAISE NOTICE '';
    RAISE NOTICE 'Schema changes:';
    RAISE NOTICE '  - Added 7 new columns (changed_attributes, attribute_changes, full_snapshot, operation_id, causation, related_asset_id, id)';
    RAISE NOTICE '  - Changed PK from (time, asset_id) to BIGSERIAL id';
    RAISE NOTICE '  - Added 6 new indexes for efficient querying';
    RAISE NOTICE '';
    RAISE NOTICE 'Next steps:';
    RAISE NOTICE '  1. Update chronicle_service.py to generate consolidated events';
    RAISE NOTICE '  2. Test event generation with new schema';
    RAISE NOTICE '  3. Verify reporting queries work as expected';
END $$;
