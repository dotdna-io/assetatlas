-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Add missing indexes on FK columns with cascade actions
-- Purpose: Fix FK cascade performance issues during delete operations
-- Without indexes on FK columns, PostgreSQL does full table scans during cascade

-- Add index on excluded_records.raw_record_id
-- FK to raw_asset_data with ON DELETE SET NULL
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_excluded_records_raw_record_id
    ON excluded_records(raw_record_id);

-- Add index on mapping_errors.raw_source_id
-- FK to raw_source_data with ON DELETE SET NULL
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_mapping_errors_raw_source_id
    ON mapping_errors(raw_source_id);

-- Add index on raw_asset_data.raw_source_id
-- FK to raw_source_data with ON DELETE SET NULL
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_raw_asset_data_raw_source_id
    ON raw_asset_data(raw_source_id);

-- Add index on system_dashboard_preferences.last_modified_by
-- FK to users with ON DELETE SET NULL
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dashboard_prefs_last_modified_by
    ON system_dashboard_preferences(last_modified_by);

-- Reason: When deleting parent records (integrations, raw_source_data, users),
-- PostgreSQL must update/check referencing rows. Without indexes, this causes
-- statement_timeout errors (60s default) on large tables.
