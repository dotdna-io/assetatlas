-- SPDX-License-Identifier: AGPL-3.0-or-later
-- ============================================================================
-- AssetAtlas Database Schema
-- PostgreSQL + TimescaleDB
-- ============================================================================
-- Description: Complete database schema for AssetAtlas standalone application
-- Version: 1.0
-- Created: 2025-10-26
-- ============================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ============================================================================
-- SECTION 1: DATA INGESTION PIPELINE
-- ============================================================================
-- Flow: Source API → raw_source_data → raw_asset_data → Asset Generation
-- Retention: 7 days for both staging tables
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1.1 Integration Configuration
-- ----------------------------------------------------------------------------
-- Stores metadata and configuration for each data source integration
-- Examples: Jamf, vCenter, CrowdStrike, ServiceNow, etc.
-- ----------------------------------------------------------------------------

CREATE TABLE integrations (
    -- Primary key
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Integration identity
    name TEXT NOT NULL UNIQUE,              -- e.g., "jamf_prod", "vcenter_dc1"
    integration_type TEXT NOT NULL,         -- e.g., "jamf", "vcenter", "crowdstrike"

    -- Classification
    category TEXT NOT NULL,                 -- e.g., "endpoint security", "virtualization"
    vendor_category TEXT,                   -- For ML category predictions

    -- Status
    status TEXT NOT NULL DEFAULT 'enabled'
        CHECK (status IN ('enabled', 'disabled', 'error')),

    -- Configuration (stored as JSONB for flexibility)
    config JSONB NOT NULL,                  -- API endpoint, credentials, polling interval
    field_mapping JSONB NOT NULL,           -- Maps source fields → AssetAtlas fields

    -- Sync tracking
    last_sync_at TIMESTAMPTZ,
    last_sync_status TEXT,                  -- 'success', 'failed', 'partial'
    last_error TEXT,
    sync_count INTEGER DEFAULT 0,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_integrations_status ON integrations(status);
CREATE INDEX idx_integrations_type ON integrations(integration_type);

-- Example field_mapping JSONB structure:
COMMENT ON COLUMN integrations.field_mapping IS
'Maps source API fields to AssetAtlas standard fields using JSONPath-like syntax.
Example: {"hostname": "computer.general.name", "sn": "computer.general.serial_number"}';


-- ----------------------------------------------------------------------------
-- 1.2 Ingestion Batch Tracking
-- ----------------------------------------------------------------------------
-- Tracks each connector run/sync operation
-- Links all records from a single ingestion together
-- ----------------------------------------------------------------------------

CREATE TABLE ingestion_batches (
    -- Primary key
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    integration_id UUID NOT NULL REFERENCES integrations(id),

    -- Status
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'mapping', 'completed', 'failed')),

    -- Record counts
    raw_records_count INTEGER DEFAULT 0,       -- Records pulled from API
    mapped_records_count INTEGER DEFAULT 0,     -- Successfully mapped records
    failed_records_count INTEGER DEFAULT 0,     -- Failed mapping attempts

    -- Timing
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    mapping_started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,

    -- Error tracking
    error_message TEXT,

    CONSTRAINT fk_batch_integration
        FOREIGN KEY (integration_id) REFERENCES integrations(id) ON DELETE CASCADE
);

-- Indexes
CREATE INDEX idx_batches_integration ON ingestion_batches(integration_id);
CREATE INDEX idx_batches_status ON ingestion_batches(status);
CREATE INDEX idx_batches_started ON ingestion_batches(started_at DESC);


-- ----------------------------------------------------------------------------
-- 1.3 STAGE 1: Raw Source Data (Technology Native Format)
-- ----------------------------------------------------------------------------
-- Stores data EXACTLY as received from source API
-- No transformation, pure original payload
-- Retention: 7 days
-- ----------------------------------------------------------------------------

CREATE TABLE raw_source_data (
    -- Primary key
    id BIGSERIAL PRIMARY KEY,
    integration_id UUID NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,

    -- Raw data from API (completely unprocessed)
    raw_payload JSONB NOT NULL,             -- Entire API response

    -- Metadata
    batch_id UUID NOT NULL REFERENCES ingestion_batches(id) ON DELETE CASCADE,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_record_id TEXT,                  -- ID from source system (if available)

    CONSTRAINT fk_raw_source_integration
        FOREIGN KEY (integration_id) REFERENCES integrations(id) ON DELETE CASCADE
);

-- Indexes
CREATE INDEX idx_raw_source_integration ON raw_source_data(integration_id);
CREATE INDEX idx_raw_source_batch ON raw_source_data(batch_id);
CREATE INDEX idx_raw_source_ingested ON raw_source_data(ingested_at);
CREATE INDEX idx_raw_source_payload ON raw_source_data USING GIN(raw_payload);

-- Partition by ingestion time for efficient cleanup
-- Note: Implement partitioning in separate migration when data volume grows


-- ----------------------------------------------------------------------------
-- 1.4 STAGE 2: Normalized Raw Asset Data (AssetAtlas Framework)
-- ----------------------------------------------------------------------------
-- Stores mapped/normalized data in AssetAtlas standard format
-- This is what the asset generation pipeline processes
-- Retention: 7 days
-- ----------------------------------------------------------------------------

CREATE TABLE raw_asset_data (
    -- Primary key
    id BIGSERIAL PRIMARY KEY,
    integration_id UUID NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,
    raw_source_id BIGINT REFERENCES raw_source_data(id) ON DELETE SET NULL,

    -- AssetAtlas Standard Fields
    hostname text[],
    fqdn text[],
    ip text[],
    mac text[],
    sn text[],
    os TEXT,
    manufacturer TEXT,
    model TEXT,
    vendor_category TEXT,

    -- Non-standard fields (source-specific)
    extended_fields JSONB,                  -- Fields that don't fit standard schema

    -- Metadata
    batch_id UUID NOT NULL REFERENCES ingestion_batches(id) ON DELETE CASCADE,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    mapped_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_raw_asset_integration
        FOREIGN KEY (integration_id) REFERENCES integrations(id) ON DELETE CASCADE
);

-- Indexes for asset generation queries
CREATE INDEX idx_raw_asset_integration ON raw_asset_data(integration_id);
CREATE INDEX idx_raw_asset_batch ON raw_asset_data(batch_id);
CREATE INDEX idx_raw_asset_ingested ON raw_asset_data(ingested_at);
CREATE INDEX idx_raw_asset_hostname ON raw_asset_data USING GIN (hostname);
CREATE INDEX idx_raw_asset_ip ON raw_asset_data USING GIN (ip);
CREATE INDEX idx_raw_asset_mac ON raw_asset_data USING GIN (mac);
CREATE INDEX idx_raw_asset_sn ON raw_asset_data USING GIN (sn);
CREATE INDEX idx_raw_asset_extended ON raw_asset_data USING GIN(extended_fields);


-- ----------------------------------------------------------------------------
-- 1.5 Mapping Error Tracking
-- ----------------------------------------------------------------------------
-- Captures records that failed during mapping transformation
-- Useful for debugging and fixing field_mapping configurations
-- ----------------------------------------------------------------------------

CREATE TABLE mapping_errors (
    -- Primary key
    id BIGSERIAL PRIMARY KEY,
    batch_id UUID NOT NULL REFERENCES ingestion_batches(id) ON DELETE CASCADE,
    raw_source_id BIGINT REFERENCES raw_source_data(id) ON DELETE SET NULL,

    -- Error details
    error_type TEXT NOT NULL,               -- 'mapping_failed', 'validation_failed', 'parse_error'
    error_message TEXT NOT NULL,
    field_name TEXT,                        -- Which field failed to map

    -- Problematic data (for debugging)
    raw_data JSONB,                         -- Copy of the raw payload that failed

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_mapping_errors_batch ON mapping_errors(batch_id);
CREATE INDEX idx_mapping_errors_type ON mapping_errors(error_type);


-- ============================================================================
-- SECTION 2: ASSET GENERATION PIPELINE OUTPUT
-- ============================================================================
-- Final processed assets and related data
-- These tables are the core output of the asset generation algorithm
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 2.1 Asset ID Mapping (UnionFind Persistence)
-- ----------------------------------------------------------------------------
-- Critical table: Maps asset components to persistent asset IDs
-- Loss of this data would break asset identity continuity
-- ⚠️  NEVER DELETE - This is permanent storage
-- ----------------------------------------------------------------------------

CREATE TABLE asset_id_mapping (
    -- Primary key (the component being mapped)
    component TEXT PRIMARY KEY,             -- e.g., "hostname", "sn:ABC123", "10.1.2.3"

    -- The asset it belongs to
    asset_id UUID NOT NULL,                 -- References assets(id) but no FK to avoid circular dependency

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_mapping_asset_id ON asset_id_mapping(asset_id);
CREATE INDEX idx_mapping_updated ON asset_id_mapping(updated_at DESC);


-- ----------------------------------------------------------------------------
-- 2.2 Current Assets (Main Asset Inventory)
-- ----------------------------------------------------------------------------
-- The current state of all discovered assets
-- This table is updated on each asset generation run
-- ============================================================================

CREATE TABLE assets (
    -- Primary key
    id UUID PRIMARY KEY,

    -- Asset composite field (for reference)
    asset TEXT,                             -- "hostname|fqdn|sn:SERIAL|ip|mac"

    -- Identity fields
    hostname TEXT,
    fqdn TEXT,
    ip INET,
    mac MACADDR,
    sn TEXT,

    -- Classification
    category TEXT,
    subcategory TEXT,

    -- Technical details
    os_normalized TEXT,                     -- Single best OS value selected based on confidence
    os TEXT[],                              -- Array of all normalized OS values from all sources
    os_orig TEXT[],                         -- Original OS values before normalization
    mac_vendor_enriched TEXT,
    manufacturer TEXT,
    model TEXT,

    -- Data source tracking (arrays for multiple sources)
    data_source TEXT[],                     -- Array of source IDs
    data_source_category TEXT[],            -- Array of source categories
    vendor_category TEXT,

    -- Scoring
    confidence NUMERIC(3,2),                -- 0.00 to 1.00
    credibility_score INTEGER,              -- 0, 1, 2 based on confidence

    -- Additional metadata
    ipam_location TEXT,
    owner TEXT,
    location TEXT,
    user_field TEXT,                        -- Renamed from 'user' to avoid reserved word

    -- Dynamic/optional fields (non-schema fields organized by data source)
    -- Structure: {"DataSourceName": {"field1": "value1", "field2": "value2"}}
    -- Example: {"Armis": {"asset_id": "40612", "riskLevel": "60"}, "Intune": {"isEncrypted_raw_b": "true"}}
    extended_attributes JSONB,

    -- Timestamps
    first_time TIMESTAMPTZ,                 -- First time this asset was seen
    last_time TIMESTAMPTZ,                  -- Last time this asset was seen
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX idx_assets_category ON assets(category);
CREATE INDEX idx_assets_hostname ON assets USING GIN (hostname);
CREATE INDEX idx_assets_ip ON assets USING GIN (ip);
CREATE INDEX idx_assets_mac ON assets USING GIN (mac);
CREATE INDEX idx_assets_sn ON assets USING GIN (sn);
CREATE INDEX idx_assets_last_time ON assets(last_time DESC);
CREATE INDEX idx_assets_confidence ON assets(confidence);
CREATE INDEX idx_assets_extended ON assets USING GIN(extended_attributes);


-- ----------------------------------------------------------------------------
-- 2.3 Asset Providers (Granular Source Attribution)
-- ----------------------------------------------------------------------------
-- Detailed record of which data source contributed what data for each asset
-- Shows the raw records before aggregation
-- Updated on each asset generation run
-- ----------------------------------------------------------------------------

CREATE TABLE asset_providers (
    -- Primary key
    id BIGSERIAL PRIMARY KEY,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,

    -- Source information
    data_source TEXT NOT NULL,              -- Integration ID
    data_source_category TEXT,

    -- Asset data as provided by this source
    hostname TEXT,
    fqdn TEXT,
    ip INET,
    mac MACADDR,
    sn TEXT,
    os TEXT,
    manufacturer TEXT,
    model TEXT,
    vendor_category TEXT,

    -- Source-specific fields
    data_source_fields JSONB,               -- Extended fields from this source

    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_providers_asset_id ON asset_providers(asset_id);
CREATE INDEX idx_providers_data_source ON asset_providers(data_source);


-- ----------------------------------------------------------------------------
-- 2.4 Asset Chronicle (Time-Series Change Tracking)
-- ----------------------------------------------------------------------------
-- TimescaleDB hypertable for tracking all asset changes over time
-- Append-only, compressed storage for historical analysis
-- ============================================================================

CREATE TABLE asset_chronicle (
    -- Compound primary key (time + asset_id)
    time TIMESTAMPTZ NOT NULL,
    asset_id UUID NOT NULL,

    -- Event information
    event_type TEXT NOT NULL,               -- 'new_asset', 'removed', 'attribute_updated', 're-added'
    attribute TEXT,                         -- Which attribute changed (null for new/removed)

    -- Change details (stored as JSONB for flexibility)
    old_value JSONB,
    new_value JSONB,
    values_added JSONB,
    values_removed JSONB,

    PRIMARY KEY (time, asset_id)
);

-- Convert to TimescaleDB hypertable with 12-hour chunks. if_not_exists makes
-- this safe to re-run during schema regeneration / partial restores.
SELECT create_hypertable('asset_chronicle', 'time',
    chunk_time_interval => INTERVAL '12 hours',
    if_not_exists => TRUE);

-- Enable compression (compress data older than 12 hours)
ALTER TABLE asset_chronicle SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'asset_id',
    timescaledb.compress_orderby = 'time DESC'
);

-- Add compression policy
SELECT add_compression_policy('asset_chronicle', INTERVAL '12 hours');

-- Indexes
CREATE INDEX idx_chronicle_asset ON asset_chronicle(asset_id, time DESC);
CREATE INDEX idx_chronicle_event_type ON asset_chronicle(event_type, time DESC);


-- ----------------------------------------------------------------------------
-- 2.5 Chronicle Continuous Aggregate (Pre-computed Summary)
-- ----------------------------------------------------------------------------
-- TimescaleDB continuous aggregate for fast hourly event queries
-- Automatically maintained by TimescaleDB
-- ----------------------------------------------------------------------------

CREATE MATERIALIZED VIEW asset_chronicle_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS hour,
    asset_id,
    event_type,
    COUNT(*) as event_count
FROM asset_chronicle
GROUP BY hour, asset_id, event_type;

-- Refresh policy (automatically update the view)
SELECT add_continuous_aggregate_policy('asset_chronicle_hourly',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');


-- ============================================================================
-- SECTION 3: REFERENCE DATA
-- ============================================================================
-- Supporting tables for compliance, tagging, and enrichment
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 3.1 Compliance Rules
-- ----------------------------------------------------------------------------

CREATE TABLE compliance_rules (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,

    -- Rule conditions (stored as JSONB)
    condition_operators JSONB NOT NULL,     -- e.g., {"tag": "any", "location": "all"}
    conditions JSONB NOT NULL,              -- e.g., {"tag": ["prod"], "location": ["us-east"]}

    -- Metadata
    description TEXT,
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_compliance_category ON compliance_rules(category);


-- ----------------------------------------------------------------------------
-- 3.2 Asset Tags
-- ----------------------------------------------------------------------------

CREATE TABLE asset_tags (
    asset_id UUID REFERENCES assets(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,

    -- Metadata
    created_by TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    PRIMARY KEY (asset_id, tag)
);

CREATE INDEX idx_tags_tag ON asset_tags(tag);


-- ----------------------------------------------------------------------------
-- 3.3 IPAM Network Ranges
-- ----------------------------------------------------------------------------

CREATE TABLE ipam_networks (
    id SERIAL PRIMARY KEY,
    network CIDR NOT NULL UNIQUE,
    location TEXT NOT NULL,

    -- Metadata
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_ipam_network ON ipam_networks USING GIST(network inet_ops);


-- ----------------------------------------------------------------------------
-- 3.4 MAC Vendor Ranges
-- ----------------------------------------------------------------------------

CREATE TABLE mac_vendor_ranges (
    id SERIAL PRIMARY KEY,
    mac_prefix TEXT NOT NULL,               -- First 6 hex digits
    vendor TEXT NOT NULL,

    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_mac_vendor_prefix ON mac_vendor_ranges(mac_prefix);


-- ----------------------------------------------------------------------------
-- 3.6 Category Overrides
-- ----------------------------------------------------------------------------
-- Manual category assignments that override ML predictions

CREATE TABLE category_overrides (
    asset_id UUID PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
    category TEXT NOT NULL,

    -- Audit trail
    overridden_by TEXT,
    overridden_at TIMESTAMPTZ DEFAULT NOW(),
    reason TEXT
);


-- ============================================================================
-- SECTION 4: UTILITY FUNCTIONS
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 4.1 Data Retention/Cleanup Functions
-- ----------------------------------------------------------------------------

-- Cleanup raw source data (7-day retention)
CREATE OR REPLACE FUNCTION cleanup_raw_source_data()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM raw_source_data
    WHERE ingested_at < NOW() - INTERVAL '7 days';

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Cleanup raw asset data (7-day retention)
CREATE OR REPLACE FUNCTION cleanup_raw_asset_data()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM raw_asset_data
    WHERE ingested_at < NOW() - INTERVAL '7 days';

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Cleanup completed batches (30-day retention)
CREATE OR REPLACE FUNCTION cleanup_old_batches()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM ingestion_batches
    WHERE completed_at < NOW() - INTERVAL '30 days'
    AND status = 'completed';

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;


-- ----------------------------------------------------------------------------
-- 4.2 Trigger: Update timestamps on row modification
-- ----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply trigger to relevant tables
CREATE TRIGGER update_integrations_updated_at BEFORE UPDATE ON integrations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_assets_updated_at BEFORE UPDATE ON assets
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_asset_id_mapping_updated_at BEFORE UPDATE ON asset_id_mapping
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();


-- ============================================================================
-- SECTION 5: INITIAL DATA / SEED DATA
-- ============================================================================
-- Add any default reference data here
-- ============================================================================

-- Example: Default compliance rule (customize as needed)
-- INSERT INTO compliance_rules (name, category, condition_operators, conditions, description)
-- VALUES (
--     'Production Server Compliance',
--     'server',
--     '{"tag": "any"}',
--     '{"tag": ["production"]}',
--     'All production servers must have specific tags'
-- );


-- ============================================================================
-- END OF SCHEMA
-- ============================================================================
-- Notes:
-- - Schedule cleanup functions via pg_cron or external scheduler
-- - Consider partitioning raw_* tables when data volume grows
-- - Monitor TimescaleDB compression policies
-- - Backup asset_id_mapping table frequently (critical for continuity)
-- ============================================================================
