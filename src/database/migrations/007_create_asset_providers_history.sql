-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Create asset_providers_history table
-- Date: 2025-10-27
-- Description: Create history table to preserve previous asset_providers states before each generation run
--              The asset_providers table always contains the latest data; history preserves past snapshots

-- Start transaction
BEGIN;

-- ----------------------------------------------------------------------------
-- Create asset_providers_history table
-- ----------------------------------------------------------------------------
-- Stores historical snapshots of the asset_providers table
-- Each time asset generation runs, current providers are archived here
-- ----------------------------------------------------------------------------

CREATE TABLE asset_providers_history (
    -- History-specific fields
    history_id BIGSERIAL PRIMARY KEY,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    generation_run_id UUID,                     -- Links to the generation run that replaced this data

    -- All fields from asset_providers table (snapshot at archive time)
    id BIGINT NOT NULL,
    asset_id UUID NOT NULL,
    data_source TEXT NOT NULL,
    data_source_category TEXT,
    hostname TEXT,
    fqdn TEXT,
    ip INET,
    mac MACADDR,
    sn TEXT,
    os TEXT,
    manufacturer TEXT,
    model TEXT,
    vendor_category TEXT,
    data_source_fields JSONB,
    updated_at TIMESTAMPTZ
);

-- Indexes for historical queries
CREATE INDEX idx_providers_history_id ON asset_providers_history(id);
CREATE INDEX idx_providers_history_asset_id ON asset_providers_history(asset_id);
CREATE INDEX idx_providers_history_archived ON asset_providers_history(archived_at DESC);
CREATE INDEX idx_providers_history_generation ON asset_providers_history(generation_run_id);
CREATE INDEX idx_providers_history_data_source ON asset_providers_history(data_source);

-- Add comments
COMMENT ON TABLE asset_providers_history IS 'Historical snapshots of asset_providers table, archived before each asset generation run';
COMMENT ON COLUMN asset_providers_history.history_id IS 'Unique identifier for this history record';
COMMENT ON COLUMN asset_providers_history.archived_at IS 'Timestamp when this snapshot was archived';
COMMENT ON COLUMN asset_providers_history.generation_run_id IS 'UUID of the asset generation run that replaced this data';
COMMENT ON COLUMN asset_providers_history.id IS 'Original provider record ID';
COMMENT ON COLUMN asset_providers_history.asset_id IS 'Asset this provider record belonged to';

-- Commit transaction
COMMIT;
