-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Create assets_history table
-- Date: 2025-10-27
-- Description: Create history table to preserve previous asset states before each generation run
--              The assets table always contains the latest data; history preserves past snapshots

-- Start transaction
BEGIN;

-- ----------------------------------------------------------------------------
-- Create assets_history table
-- ----------------------------------------------------------------------------
-- Stores historical snapshots of the assets table
-- Each time asset generation runs, current assets are archived here
-- ----------------------------------------------------------------------------

CREATE TABLE assets_history (
    -- History-specific fields
    history_id BIGSERIAL PRIMARY KEY,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    generation_run_id UUID,                     -- Links to the generation run that replaced this data

    -- All fields from assets table (snapshot at archive time)
    id UUID NOT NULL,
    asset TEXT[],
    hostname TEXT[],
    fqdn TEXT[],
    ip TEXT[],
    mac TEXT[],
    sn TEXT[],
    category TEXT,
    subcategory TEXT,
    orig_category TEXT,
    os_normalized TEXT[],
    mac_vendor_enriched TEXT[],
    manufacturer TEXT[],
    model TEXT[],
    data_source TEXT[],
    data_source_category TEXT[],
    vendor_category TEXT[],
    confidence NUMERIC(3,2),
    credibility_score INTEGER,
    is_compliant TEXT[],
    is_risky TEXT[],
    os_eol INTEGER,
    ipam_location TEXT,
    owner TEXT,
    location TEXT,
    description TEXT,
    av TEXT,
    domain TEXT,
    user_field TEXT,
    extended_attributes JSONB,
    first_time TIMESTAMPTZ,
    last_time TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);

-- Indexes for historical queries
CREATE INDEX idx_assets_history_id ON assets_history(id);
CREATE INDEX idx_assets_history_archived ON assets_history(archived_at DESC);
CREATE INDEX idx_assets_history_generation ON assets_history(generation_run_id);
CREATE INDEX idx_assets_history_category ON assets_history(category);
CREATE INDEX idx_assets_history_hostname ON assets_history USING GIN(hostname);

-- Add comments
COMMENT ON TABLE assets_history IS 'Historical snapshots of assets table, archived before each asset generation run';
COMMENT ON COLUMN assets_history.history_id IS 'Unique identifier for this history record';
COMMENT ON COLUMN assets_history.archived_at IS 'Timestamp when this snapshot was archived';
COMMENT ON COLUMN assets_history.generation_run_id IS 'UUID of the asset generation run that replaced this data';
COMMENT ON COLUMN assets_history.id IS 'Original asset ID (may appear multiple times with different archived_at)';

-- Commit transaction
COMMIT;
