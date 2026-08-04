-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Add data storage tables for integration execution
-- Date: 2025-11-04
-- Description: Creates batches and raw_source_data tables for storing integration execution results

-- =============================================================================
-- Batches Table
-- =============================================================================

CREATE TABLE IF NOT EXISTS batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    integration_id UUID NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    trigger_type VARCHAR(50) NOT NULL DEFAULT 'manual',
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE,
    record_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    error_details JSONB,
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Indexes for batches
CREATE INDEX IF NOT EXISTS idx_batches_integration ON batches(integration_id);
CREATE INDEX IF NOT EXISTS idx_batches_status ON batches(status);
CREATE INDEX IF NOT EXISTS idx_batches_started_at ON batches(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_batches_integration_status ON batches(integration_id, status);

-- Comments for batches
COMMENT ON TABLE batches IS 'Tracks integration execution runs with metadata and results';
COMMENT ON COLUMN batches.status IS 'Batch status: pending, running, completed, failed, partial';
COMMENT ON COLUMN batches.trigger_type IS 'How batch was triggered: scheduled, manual, api';
COMMENT ON COLUMN batches.record_count IS 'Number of records collected in this batch';
COMMENT ON COLUMN batches.error_message IS 'Error message if batch failed';
COMMENT ON COLUMN batches.error_details IS 'Detailed error information as JSON';
COMMENT ON COLUMN batches.metadata IS 'Additional batch metadata (field_mode, etc)';

-- =============================================================================
-- Raw Source Data Table
-- =============================================================================

CREATE TABLE IF NOT EXISTS raw_source_data (
    id BIGSERIAL PRIMARY KEY,
    batch_id UUID NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    integration_id UUID NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,
    source_record_id VARCHAR(255),
    data JSONB NOT NULL,
    collected_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    processed BOOLEAN NOT NULL DEFAULT FALSE,
    processed_at TIMESTAMP WITH TIME ZONE,
    processing_error TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Indexes for raw_source_data
CREATE INDEX IF NOT EXISTS idx_raw_data_batch ON raw_source_data(batch_id);
CREATE INDEX IF NOT EXISTS idx_raw_data_integration ON raw_source_data(integration_id);
CREATE INDEX IF NOT EXISTS idx_raw_data_processed ON raw_source_data(processed) WHERE NOT processed;
CREATE INDEX IF NOT EXISTS idx_raw_data_source_id ON raw_source_data(integration_id, source_record_id);
CREATE INDEX IF NOT EXISTS idx_raw_data_collected_at ON raw_source_data(collected_at DESC);

-- GIN index for JSONB queries
CREATE INDEX IF NOT EXISTS idx_raw_data_json ON raw_source_data USING gin(data);

-- Comments for raw_source_data
COMMENT ON TABLE raw_source_data IS 'Stores raw JSON records from integration sources';
COMMENT ON COLUMN raw_source_data.source_record_id IS 'Original record ID from source system';
COMMENT ON COLUMN raw_source_data.data IS 'Raw record data as JSON';
COMMENT ON COLUMN raw_source_data.processed IS 'Whether record has been transformed to assets';
COMMENT ON COLUMN raw_source_data.processing_error IS 'Error message if processing failed';

-- =============================================================================
-- Batch Logs Table (Optional - for detailed logging)
-- =============================================================================

CREATE TABLE IF NOT EXISTS batch_logs (
    id BIGSERIAL PRIMARY KEY,
    batch_id UUID NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    log_level VARCHAR(20) NOT NULL,
    message TEXT NOT NULL,
    details JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Indexes for batch_logs
CREATE INDEX IF NOT EXISTS idx_batch_logs_batch ON batch_logs(batch_id);
CREATE INDEX IF NOT EXISTS idx_batch_logs_level ON batch_logs(log_level);
CREATE INDEX IF NOT EXISTS idx_batch_logs_created_at ON batch_logs(created_at DESC);

-- Comments for batch_logs
COMMENT ON TABLE batch_logs IS 'Detailed execution logs for batches';
COMMENT ON COLUMN batch_logs.log_level IS 'Log level: INFO, WARNING, ERROR';
COMMENT ON COLUMN batch_logs.message IS 'Log message';
COMMENT ON COLUMN batch_logs.details IS 'Additional log details as JSON';

-- =============================================================================
-- Verification Queries
-- =============================================================================

-- Verify tables created
SELECT
    tablename,
    schemaname
FROM pg_tables
WHERE schemaname = 'public'
    AND tablename IN ('batches', 'raw_source_data', 'batch_logs')
ORDER BY tablename;

-- Verify indexes created
SELECT
    indexname,
    tablename
FROM pg_indexes
WHERE schemaname = 'public'
    AND tablename IN ('batches', 'raw_source_data', 'batch_logs')
ORDER BY tablename, indexname;

-- Count check (should be 0 for new tables)
SELECT
    'batches' as table_name,
    COUNT(*) as row_count
FROM batches
UNION ALL
SELECT
    'raw_source_data' as table_name,
    COUNT(*) as row_count
FROM raw_source_data
UNION ALL
SELECT
    'batch_logs' as table_name,
    COUNT(*) as row_count
FROM batch_logs;
