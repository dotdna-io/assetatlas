-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 036: Create pipeline_runs table for tracking pipeline executions
-- Supports manual triggers and scheduled runs with stage-level progress tracking.

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    triggered_by VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    current_stage VARCHAR(50),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    duration_seconds NUMERIC(10,2),
    stats JSONB,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status ON pipeline_runs(status);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_created ON pipeline_runs(created_at DESC);
