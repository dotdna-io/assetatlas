-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Create excluded_records table
-- Purpose: Track all records excluded from asset generation with reasons and execution metadata
-- Date: 2025-10-28

-- Drop table if exists (for development)
DROP TABLE IF EXISTS excluded_records CASCADE;

-- Create excluded_records table
CREATE TABLE excluded_records (
    id BIGSERIAL PRIMARY KEY,

    -- Execution metadata
    generation_run_id UUID NOT NULL,
    excluded_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    -- Source data reference
    raw_record_id BIGINT,  -- Foreign key to raw_asset_data.id if available
    integration_id UUID,
    raw_source_id BIGINT,
    data_source TEXT,
    batch_id UUID,

    -- Original record data
    hostname TEXT,
    fqdn TEXT,
    ip INET,
    mac MACADDR,
    sn TEXT,
    os TEXT,
    manufacturer TEXT,
    model TEXT,
    vendor_category TEXT,

    -- Exclusion metadata
    exclusion_reason TEXT NOT NULL,  -- Why was it excluded
    exclusion_category TEXT NOT NULL,  -- Category: hostname_quality, ip_quality, mac_quality, insufficient_data, etc.
    has_hostname BOOLEAN,
    has_ip BOOLEAN,
    has_mac BOOLEAN,
    has_sn BOOLEAN,

    -- Additional context (JSON for flexibility)
    extended_fields JSONB,
    exclusion_details JSONB,  -- Detailed breakdown of exclusion reasons

    -- Indexes for common queries
    CONSTRAINT fk_raw_record FOREIGN KEY (raw_record_id) REFERENCES raw_asset_data(id) ON DELETE SET NULL
);

-- Indexes for efficient querying
CREATE INDEX idx_excluded_records_generation_run ON excluded_records(generation_run_id);
CREATE INDEX idx_excluded_records_excluded_at ON excluded_records(excluded_at);
CREATE INDEX idx_excluded_records_exclusion_category ON excluded_records(exclusion_category);
CREATE INDEX idx_excluded_records_exclusion_reason ON excluded_records(exclusion_reason);
CREATE INDEX idx_excluded_records_data_source ON excluded_records(data_source);
CREATE INDEX idx_excluded_records_integration_id ON excluded_records(integration_id);
CREATE INDEX idx_excluded_records_batch_id ON excluded_records(batch_id);
CREATE INDEX idx_excluded_records_hostname ON excluded_records(hostname) WHERE hostname IS NOT NULL;
CREATE INDEX idx_excluded_records_ip ON excluded_records(ip) WHERE ip IS NOT NULL;
CREATE INDEX idx_excluded_records_mac ON excluded_records(mac) WHERE mac IS NOT NULL;
CREATE INDEX idx_excluded_records_sn ON excluded_records(sn) WHERE sn IS NOT NULL;

-- GIN index for JSONB fields
CREATE INDEX idx_excluded_records_extended_fields ON excluded_records USING GIN(extended_fields);
CREATE INDEX idx_excluded_records_exclusion_details ON excluded_records USING GIN(exclusion_details);

-- Comments
COMMENT ON TABLE excluded_records IS 'Records excluded from asset generation with reasons and execution metadata';
COMMENT ON COLUMN excluded_records.generation_run_id IS 'UUID of the asset generation execution';
COMMENT ON COLUMN excluded_records.exclusion_reason IS 'Human-readable reason for exclusion';
COMMENT ON COLUMN excluded_records.exclusion_category IS 'Category of exclusion (hostname_quality, ip_quality, etc.)';
COMMENT ON COLUMN excluded_records.exclusion_details IS 'Detailed JSON breakdown of specific quality issues';
