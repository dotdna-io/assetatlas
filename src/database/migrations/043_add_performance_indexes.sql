-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Add missing performance indexes for dashboard and pipeline queries
-- Date: 2026-02-21
-- Purpose: Address identified index gaps causing full table scans on dashboard load
--          and slow chronicle CTE queries

BEGIN;

-- FK index on asset_providers.integration_id
-- Missing: causes full table scan in get_integration_stats() 3-table JOIN
CREATE INDEX IF NOT EXISTS idx_asset_providers_integration_id
    ON asset_providers (integration_id);

-- Category index on assets table
-- Missing: used in GROUP BY for category breakdown on every dashboard load
CREATE INDEX IF NOT EXISTS idx_assets_category
    ON assets (category);

-- Confidence index on assets table
-- Missing: used in confidence distribution grouping
CREATE INDEX IF NOT EXISTS idx_assets_confidence
    ON assets (confidence);

-- First seen index for "assets added today" query
CREATE INDEX IF NOT EXISTS idx_assets_first_seen
    ON assets (first_seen);

-- Composite index on asset_chronicle for latest-run CTE optimization
-- The dashboard queries: ORDER BY time DESC WHERE generation_run_id IS NOT NULL LIMIT 1
CREATE INDEX IF NOT EXISTS idx_chronicle_gen_run_time
    ON asset_chronicle (generation_run_id, time DESC)
    WHERE generation_run_id IS NOT NULL;

-- Started_at index on pipeline_runs for time-range queries
-- Only had created_at DESC, missing started_at
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started_at
    ON pipeline_runs (started_at DESC);

COMMIT;
