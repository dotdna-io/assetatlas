-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Drop Legacy Chronicle Fields
-- Purpose: Remove backward compatibility fields and enforce consolidated schema only
-- Date: 2025-10-27
--
-- Drops deprecated fields:
-- - attribute (use changed_attributes array instead)
-- - old_value (use attribute_changes JSONB instead)
-- - new_value (use attribute_changes JSONB instead)
-- - values_added (use attribute_changes JSONB instead)
-- - values_removed (use attribute_changes JSONB instead)

-- =========================================================================
-- STEP 1: Drop legacy columns
-- =========================================================================

ALTER TABLE asset_chronicle
DROP COLUMN IF EXISTS attribute;

ALTER TABLE asset_chronicle
DROP COLUMN IF EXISTS old_value;

ALTER TABLE asset_chronicle
DROP COLUMN IF EXISTS new_value;

ALTER TABLE asset_chronicle
DROP COLUMN IF EXISTS values_added;

ALTER TABLE asset_chronicle
DROP COLUMN IF EXISTS values_removed;

-- =========================================================================
-- STEP 2: Verification
-- =========================================================================

-- Verify legacy columns are dropped
DO $$
DECLARE
    legacy_columns TEXT[] := ARRAY['attribute', 'old_value', 'new_value', 'values_added', 'values_removed'];
    remaining_columns TEXT[] := ARRAY[]::TEXT[];
    col TEXT;
BEGIN
    FOREACH col IN ARRAY legacy_columns
    LOOP
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'asset_chronicle'
            AND column_name = col
        ) THEN
            remaining_columns := array_append(remaining_columns, col);
        END IF;
    END LOOP;

    IF array_length(remaining_columns, 1) > 0 THEN
        RAISE EXCEPTION 'Failed to drop legacy columns: %', array_to_string(remaining_columns, ', ');
    ELSE
        RAISE NOTICE '✓ Successfully dropped all legacy columns';
    END IF;
END $$;

-- Verify current schema
DO $$
DECLARE
    expected_columns TEXT[] := ARRAY[
        'id',
        'time',
        'asset_id',
        'event_type',
        'generation_run_id',
        'changed_attributes',
        'attribute_changes',
        'full_snapshot',
        'operation_id',
        'causation',
        'related_asset_id'
    ];
    missing_columns TEXT[] := ARRAY[]::TEXT[];
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
        RAISE EXCEPTION 'Missing expected columns: %', array_to_string(missing_columns, ', ');
    ELSE
        RAISE NOTICE '✓ All expected columns present';
    END IF;
END $$;

-- Display final schema
DO $$
DECLARE
    row_count BIGINT;
    table_size TEXT;
BEGIN
    SELECT COUNT(*) INTO row_count FROM asset_chronicle;
    SELECT pg_size_pretty(pg_total_relation_size('asset_chronicle')) INTO table_size;

    RAISE NOTICE '====================================';
    RAISE NOTICE 'Migration 013 completed successfully';
    RAISE NOTICE '====================================';
    RAISE NOTICE 'Total chronicle events: %', row_count;
    RAISE NOTICE 'Table size: %', table_size;
    RAISE NOTICE '';
    RAISE NOTICE 'Schema changes:';
    RAISE NOTICE '  - Dropped 5 legacy columns (attribute, old_value, new_value, values_added, values_removed)';
    RAISE NOTICE '  - Enforcing consolidated schema only';
    RAISE NOTICE '';
    RAISE NOTICE 'Final schema columns:';
    RAISE NOTICE '  - Core: id, time, asset_id, event_type';
    RAISE NOTICE '  - Consolidated: changed_attributes, attribute_changes, full_snapshot';
    RAISE NOTICE '  - Audit: generation_run_id, operation_id, causation, related_asset_id';
END $$;
