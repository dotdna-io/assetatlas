-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Create invalid_assets table for tracking assets with no valid identifiers
-- Created: 2025-10-27
-- Purpose: Track raw asset records that could not be assigned valid IDs during asset generation

CREATE TABLE IF NOT EXISTS invalid_assets (
    id BIGSERIAL PRIMARY KEY,
    generation_run_id UUID NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Raw asset data that failed ID assignment
    hostname TEXT,
    fqdn TEXT,
    ip INET,
    mac MACADDR,
    sn TEXT,
    os TEXT,
    manufacturer TEXT,
    model TEXT,
    vendor_category TEXT,
    data_source TEXT,

    -- Reason for invalidity
    failure_reason TEXT NOT NULL,

    -- Store full raw data as JSONB for investigation
    raw_data JSONB,

    -- Additional context
    notes TEXT
);

-- Indexes for efficient querying
CREATE INDEX idx_invalid_assets_generation_run ON invalid_assets(generation_run_id);
CREATE INDEX idx_invalid_assets_recorded_at ON invalid_assets(recorded_at DESC);
CREATE INDEX idx_invalid_assets_data_source ON invalid_assets(data_source);
CREATE INDEX idx_invalid_assets_failure_reason ON invalid_assets(failure_reason);

-- Add comment
COMMENT ON TABLE invalid_assets IS 'Tracks raw asset records that failed to receive valid IDs during asset generation. Used for data quality monitoring and debugging.';
