-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Add job execution history for robust scheduler monitoring
-- Date: 2025-11-13
-- Purpose: Track all scheduler job executions for audit, debugging, and monitoring

-- Create job execution history table
CREATE TABLE IF NOT EXISTS job_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Job identification
    job_id VARCHAR(255) NOT NULL,  -- Integration ID or other job identifier
    job_name VARCHAR(255),          -- Human-readable job name
    execution_id UUID NOT NULL,      -- Unique ID for this execution attempt

    -- Execution timing
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    completed_at TIMESTAMP WITH TIME ZONE,
    duration_seconds NUMERIC(10,2),

    -- Execution status
    status VARCHAR(20) NOT NULL CHECK (status IN ('running', 'success', 'failed', 'timeout', 'skipped')),

    -- Results
    records_processed INTEGER,
    error_message TEXT,
    message TEXT,  -- General execution message

    -- Retry information
    attempt_number INTEGER DEFAULT 1,
    max_attempts INTEGER DEFAULT 3,

    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Indexes for efficient querying
CREATE INDEX idx_job_executions_job_id ON job_executions(job_id);
CREATE INDEX idx_job_executions_execution_id ON job_executions(execution_id);
CREATE INDEX idx_job_executions_started_at ON job_executions(started_at DESC);
CREATE INDEX idx_job_executions_status ON job_executions(status);
CREATE INDEX idx_job_executions_job_id_started_at ON job_executions(job_id, started_at DESC);

-- Create a view for recent job executions (last 7 days)
CREATE OR REPLACE VIEW recent_job_executions AS
SELECT
    je.*,
    CASE
        WHEN status = 'running' AND
             EXTRACT(EPOCH FROM (NOW() - started_at)) > 3600
        THEN 'stuck'
        ELSE status
    END as effective_status
FROM job_executions je
WHERE started_at > NOW() - INTERVAL '7 days'
ORDER BY started_at DESC;

-- Create a materialized view for job statistics (refresh hourly)
CREATE MATERIALIZED VIEW IF NOT EXISTS job_execution_stats AS
SELECT
    job_id,
    job_name,
    COUNT(*) as total_executions,
    COUNT(*) FILTER (WHERE status = 'success') as successful_executions,
    COUNT(*) FILTER (WHERE status = 'failed') as failed_executions,
    COUNT(*) FILTER (WHERE status = 'timeout') as timeout_executions,
    COUNT(*) FILTER (WHERE status = 'skipped') as skipped_executions,
    AVG(duration_seconds) FILTER (WHERE status = 'success') as avg_duration_seconds,
    MIN(duration_seconds) FILTER (WHERE status = 'success') as min_duration_seconds,
    MAX(duration_seconds) FILTER (WHERE status = 'success') as max_duration_seconds,
    SUM(records_processed) FILTER (WHERE status = 'success') as total_records_processed,
    MAX(started_at) as last_execution_at,
    MAX(started_at) FILTER (WHERE status = 'success') as last_success_at,
    MAX(started_at) FILTER (WHERE status = 'failed') as last_failure_at,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE status = 'success') / NULLIF(COUNT(*), 0),
        2
    ) as success_rate
FROM job_executions
WHERE started_at > NOW() - INTERVAL '30 days'
GROUP BY job_id, job_name;

-- Create index on materialized view
CREATE UNIQUE INDEX idx_job_execution_stats_job_id ON job_execution_stats(job_id);

-- Function to clean up old job execution records (keep 90 days)
CREATE OR REPLACE FUNCTION cleanup_old_job_executions()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM job_executions
    WHERE started_at < NOW() - INTERVAL '90 days'
    AND status != 'running';  -- Don't delete running jobs even if old (might be stuck)

    GET DIAGNOSTICS deleted_count = ROW_COUNT;

    -- Log the cleanup
    RAISE NOTICE 'Deleted % old job execution records', deleted_count;

    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Function to get job health status
CREATE OR REPLACE FUNCTION get_job_health_status(p_job_id VARCHAR)
RETURNS TABLE (
    health_status VARCHAR,
    recent_failures INTEGER,
    success_rate NUMERIC,
    avg_duration NUMERIC,
    last_success TIMESTAMP WITH TIME ZONE,
    last_failure TIMESTAMP WITH TIME ZONE,
    is_stuck BOOLEAN
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        CASE
            WHEN recent_failures > 5 THEN 'critical'
            WHEN recent_failures > 2 THEN 'warning'
            WHEN last_exec_status = 'running' AND
                 EXTRACT(EPOCH FROM (NOW() - last_exec_started)) > 3600 THEN 'stuck'
            ELSE 'healthy'
        END::VARCHAR as health_status,
        COALESCE(recent_failures, 0)::INTEGER,
        COALESCE(recent_success_rate, 0)::NUMERIC,
        COALESCE(recent_avg_duration, 0)::NUMERIC,
        recent_last_success,
        recent_last_failure,
        (last_exec_status = 'running' AND
         EXTRACT(EPOCH FROM (NOW() - last_exec_started)) > 3600)::BOOLEAN as is_stuck
    FROM (
        SELECT
            COUNT(*) FILTER (WHERE status = 'failed' AND started_at > NOW() - INTERVAL '24 hours') as recent_failures,
            ROUND(
                100.0 * COUNT(*) FILTER (WHERE status = 'success') / NULLIF(COUNT(*), 0),
                2
            ) as recent_success_rate,
            AVG(duration_seconds) FILTER (WHERE status = 'success') as recent_avg_duration,
            MAX(started_at) FILTER (WHERE status = 'success') as recent_last_success,
            MAX(started_at) FILTER (WHERE status = 'failed') as recent_last_failure,
            (SELECT status FROM job_executions WHERE job_id = p_job_id ORDER BY started_at DESC LIMIT 1) as last_exec_status,
            (SELECT started_at FROM job_executions WHERE job_id = p_job_id ORDER BY started_at DESC LIMIT 1) as last_exec_started
        FROM job_executions
        WHERE job_id = p_job_id
        AND started_at > NOW() - INTERVAL '7 days'
    ) stats;
END;
$$ LANGUAGE plpgsql;

-- Create a scheduled job to refresh the materialized view (requires pg_cron extension)
-- Uncomment if pg_cron is available:
-- SELECT cron.schedule('refresh-job-stats', '0 * * * *', 'REFRESH MATERIALIZED VIEW CONCURRENTLY job_execution_stats;');
-- SELECT cron.schedule('cleanup-job-history', '0 2 * * *', 'SELECT cleanup_old_job_executions();');

-- Grant permissions (adjust as needed for your user)
GRANT SELECT, INSERT, UPDATE ON job_executions TO assetatlas;
GRANT SELECT ON recent_job_executions TO assetatlas;
GRANT SELECT ON job_execution_stats TO assetatlas;

-- Add comment
COMMENT ON TABLE job_executions IS 'Audit trail and history of all scheduler job executions';
COMMENT ON VIEW recent_job_executions IS 'Recent job executions (last 7 days) with stuck detection';
COMMENT ON MATERIALIZED VIEW job_execution_stats IS 'Aggregated job statistics for monitoring (refresh hourly)';