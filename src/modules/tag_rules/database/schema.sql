-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Tag Rules System Database Schema
-- Version: 1.1
-- Created: 2025-10-31
-- Updated: 2025-11-03
--
-- This schema creates 4 tables for the tag rules system:
-- 1. tag_rules - Rule definitions
-- 2. asset_tags_v2 - Asset-tag associations (hard deletion, complete replacement strategy)
-- 3. rule_execution_history - Execution audit trail
-- 4. detection_alerts - Compliance alerts
--
-- CHANGELOG:
-- [2025-11-03] Removed removed_at column from asset_tags_v2 (hard deletion strategy)
-- [2025-11-03] Simplified unique constraint (no WHERE clause needed)
-- [2025-10-31] Initial schema creation

-- =============================================================================
-- Table 1: tag_rules
-- =============================================================================
-- Stores all tag rule configurations (classification and compliance rules)

CREATE TABLE IF NOT EXISTS tag_rules (
    -- Primary identification
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    type VARCHAR(20) NOT NULL CHECK (type IN ('compliance', 'classification')),
    enabled BOOLEAN DEFAULT true,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_modified TIMESTAMPTZ DEFAULT NOW(),
    last_executed_at TIMESTAMPTZ,

    -- Execution statistics
    execution_count INTEGER DEFAULT 0,
    last_execution_duration_ms INTEGER,
    last_execution_assets_matched INTEGER DEFAULT 0,
    last_execution_assets_passed INTEGER,  -- For compliance rules
    last_execution_assets_failed INTEGER,  -- For compliance rules

    -- Rule configuration (stored as JSONB for flexibility)
    target_filters JSONB NOT NULL,  -- Array of {field, operator, value}
    tags JSONB NOT NULL,  -- {pass, fail} for compliance OR {label, color} for classification
    compliance_criteria JSONB,  -- {logic: "all"|"any", conditions: [...]} for compliance rules
    detection_config JSONB,  -- {enabled, condition, threshold, severity} for alerts

    -- Validation constraints
    CONSTRAINT valid_compliance_rule CHECK (
        type != 'compliance' OR (compliance_criteria IS NOT NULL AND tags->'pass' IS NOT NULL AND tags->'fail' IS NOT NULL)
    ),
    CONSTRAINT valid_classification_rule CHECK (
        type != 'classification' OR (tags->'label' IS NOT NULL AND tags->'color' IS NOT NULL)
    ),
    CONSTRAINT valid_target_filters CHECK (
        jsonb_typeof(target_filters) = 'array'
    )
);

-- Indexes for tag_rules
CREATE INDEX IF NOT EXISTS idx_tag_rules_type ON tag_rules(type);
CREATE INDEX IF NOT EXISTS idx_tag_rules_enabled ON tag_rules(enabled) WHERE enabled = true;
CREATE INDEX IF NOT EXISTS idx_tag_rules_last_executed ON tag_rules(last_executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_tag_rules_target_filters ON tag_rules USING GIN (target_filters);
CREATE INDEX IF NOT EXISTS idx_tag_rules_created ON tag_rules(created_at DESC);

-- Comments
COMMENT ON TABLE tag_rules IS 'Tag rule definitions for automatic asset tagging and compliance checking';
COMMENT ON COLUMN tag_rules.type IS 'Rule type: "compliance" (pass/fail evaluation) or "classification" (simple labeling)';
COMMENT ON COLUMN tag_rules.target_filters IS 'Array of filter conditions to match assets: [{field, operator, value}, ...]';
COMMENT ON COLUMN tag_rules.tags IS 'Tag configuration: {pass, fail} for compliance OR {label, color} for classification';
COMMENT ON COLUMN tag_rules.compliance_criteria IS 'Compliance evaluation rules: {logic: "all"|"any", conditions: [{field, operator, value}, ...]}';
COMMENT ON COLUMN tag_rules.detection_config IS 'Alert configuration: {enabled, condition, threshold, severity}';


-- =============================================================================
-- Table 2: asset_tags_v2
-- =============================================================================
-- Tracks which tags are applied to which assets and by which rules
--
-- LIFECYCLE STRATEGY: Complete Replacement (Hard Deletion)
-- - When a rule executes, all existing tags for that rule+tag_name are DELETED
-- - New tags are then INSERTED for matching assets
-- - No soft deletion tracking (no removed_at column)
-- - Simpler and more efficient than soft-delete/reactivation approach

CREATE TABLE IF NOT EXISTS asset_tags_v2 (
    -- Primary identification
    id BIGSERIAL PRIMARY KEY,
    asset_id UUID NOT NULL,  -- FK to assets.id
    tag_name VARCHAR(255) NOT NULL,
    tag_rule_id VARCHAR(64) NOT NULL,  -- FK to tag_rules.id

    -- Lifecycle tracking
    applied_at TIMESTAMPTZ DEFAULT NOW(),

    -- Metadata
    tag_type VARCHAR(20) NOT NULL CHECK (tag_type IN ('pass', 'fail', 'classification')),
    execution_id BIGINT,  -- FK to rule_execution_history.id (optional)

    -- Foreign key constraints
    CONSTRAINT fk_asset_tags_v2_asset
        FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE,
    CONSTRAINT fk_asset_tags_v2_rule
        FOREIGN KEY (tag_rule_id) REFERENCES tag_rules(id) ON DELETE CASCADE
);

-- Indexes for asset_tags_v2
CREATE INDEX IF NOT EXISTS idx_asset_tags_v2_asset ON asset_tags_v2(asset_id);
CREATE INDEX IF NOT EXISTS idx_asset_tags_v2_rule ON asset_tags_v2(tag_rule_id);
CREATE INDEX IF NOT EXISTS idx_asset_tags_v2_tag_name ON asset_tags_v2(tag_name);
CREATE INDEX IF NOT EXISTS idx_asset_tags_v2_applied ON asset_tags_v2(applied_at DESC);
CREATE INDEX IF NOT EXISTS idx_asset_tags_v2_execution ON asset_tags_v2(execution_id)
    WHERE execution_id IS NOT NULL;

-- Unique constraint: Ensure only one tag per asset/name/rule combination
CREATE UNIQUE INDEX IF NOT EXISTS idx_asset_tags_v2_unique_active
    ON asset_tags_v2(asset_id, tag_name, tag_rule_id);

-- Comments
COMMENT ON TABLE asset_tags_v2 IS 'Asset tag associations with hard deletion lifecycle (complete replacement strategy)';
COMMENT ON COLUMN asset_tags_v2.tag_type IS 'Type of tag: "pass", "fail" (for compliance), or "classification"';
COMMENT ON COLUMN asset_tags_v2.execution_id IS 'Link to rule execution that created this tag';


-- =============================================================================
-- Table 3: rule_execution_history
-- =============================================================================
-- Tracks every rule execution for auditing and analytics

CREATE TABLE IF NOT EXISTS rule_execution_history (
    -- Primary identification
    id BIGSERIAL PRIMARY KEY,
    rule_id VARCHAR(64) NOT NULL,  -- FK to tag_rules.id

    -- Execution metadata
    executed_at TIMESTAMPTZ DEFAULT NOW(),
    execution_time_ms INTEGER,
    status VARCHAR(20) NOT NULL CHECK (status IN ('success', 'failed', 'partial')),
    error_message TEXT,

    -- Execution results
    assets_matched INTEGER DEFAULT 0,
    assets_passed INTEGER,  -- For compliance rules only
    assets_failed INTEGER,  -- For compliance rules only
    tags_applied INTEGER DEFAULT 0,
    tags_removed INTEGER DEFAULT 0,

    -- Execution context
    triggered_by VARCHAR(50) CHECK (triggered_by IN ('scheduled', 'manual', 'api', 'asset_generation_pipeline')),
    triggered_by_user VARCHAR(100),
    generation_run_id UUID,  -- Link to asset generation run if triggered by pipeline

    -- Foreign key constraints
    CONSTRAINT fk_execution_history_rule
        FOREIGN KEY (rule_id) REFERENCES tag_rules(id) ON DELETE CASCADE
);

-- Indexes for rule_execution_history
CREATE INDEX IF NOT EXISTS idx_execution_history_rule ON rule_execution_history(rule_id, executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_execution_history_date ON rule_execution_history(executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_execution_history_status ON rule_execution_history(status);
CREATE INDEX IF NOT EXISTS idx_execution_history_generation ON rule_execution_history(generation_run_id)
    WHERE generation_run_id IS NOT NULL;

-- Comments
COMMENT ON TABLE rule_execution_history IS 'Audit trail of all tag rule executions';
COMMENT ON COLUMN rule_execution_history.triggered_by IS 'Execution trigger: "scheduled", "manual", "api", or "asset_generation_pipeline"';
COMMENT ON COLUMN rule_execution_history.generation_run_id IS 'Links to asset generation run UUID when triggered by pipeline';


-- =============================================================================
-- Table 4: detection_alerts
-- =============================================================================
-- Stores alerts triggered by compliance detection rules

CREATE TABLE IF NOT EXISTS detection_alerts (
    -- Primary identification
    id BIGSERIAL PRIMARY KEY,
    rule_id VARCHAR(64) NOT NULL,  -- FK to tag_rules.id
    execution_id BIGINT,  -- FK to rule_execution_history.id (optional)

    -- Alert lifecycle
    triggered_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by VARCHAR(100),

    -- Alert details
    condition VARCHAR(100) NOT NULL,  -- "Pass rate below threshold", etc.
    threshold_value NUMERIC(10, 2),  -- The configured threshold
    actual_value NUMERIC(10, 2),  -- The actual value that triggered alert
    severity VARCHAR(20) NOT NULL CHECK (severity IN ('Low', 'Medium', 'High', 'Critical')),

    -- Alert status
    status VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active', 'resolved', 'suppressed')),

    -- Additional context
    message TEXT,
    metadata JSONB,

    -- Foreign key constraints
    CONSTRAINT fk_detection_alerts_rule
        FOREIGN KEY (rule_id) REFERENCES tag_rules(id) ON DELETE CASCADE,
    CONSTRAINT fk_detection_alerts_execution
        FOREIGN KEY (execution_id) REFERENCES rule_execution_history(id) ON DELETE SET NULL
);

-- Indexes for detection_alerts
CREATE INDEX IF NOT EXISTS idx_detection_alerts_rule ON detection_alerts(rule_id);
CREATE INDEX IF NOT EXISTS idx_detection_alerts_status ON detection_alerts(status) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_detection_alerts_severity ON detection_alerts(severity);
CREATE INDEX IF NOT EXISTS idx_detection_alerts_triggered ON detection_alerts(triggered_at DESC);
CREATE INDEX IF NOT EXISTS idx_detection_alerts_execution ON detection_alerts(execution_id)
    WHERE execution_id IS NOT NULL;

-- Comments
COMMENT ON TABLE detection_alerts IS 'Compliance alerts triggered when thresholds are breached';
COMMENT ON COLUMN detection_alerts.condition IS 'Alert condition: "Pass rate below threshold", "Fail rate above threshold", etc.';
COMMENT ON COLUMN detection_alerts.status IS 'Alert status: "active", "resolved", or "suppressed"';


-- =============================================================================
-- Functions and Triggers
-- =============================================================================

-- Function to update last_modified timestamp on tag_rules
CREATE OR REPLACE FUNCTION update_tag_rule_modified()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_modified = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to automatically update last_modified on tag_rules
DROP TRIGGER IF EXISTS trigger_update_tag_rule_modified ON tag_rules;
CREATE TRIGGER trigger_update_tag_rule_modified
    BEFORE UPDATE ON tag_rules
    FOR EACH ROW
    EXECUTE FUNCTION update_tag_rule_modified();


-- Function to update rule statistics after execution
CREATE OR REPLACE FUNCTION update_rule_execution_stats()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE tag_rules
    SET
        last_executed_at = NEW.executed_at,
        execution_count = execution_count + 1,
        last_execution_duration_ms = NEW.execution_time_ms,
        last_execution_assets_matched = NEW.assets_matched,
        last_execution_assets_passed = NEW.assets_passed,
        last_execution_assets_failed = NEW.assets_failed
    WHERE id = NEW.rule_id;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to automatically update rule stats after execution
DROP TRIGGER IF EXISTS trigger_update_rule_stats ON rule_execution_history;
CREATE TRIGGER trigger_update_rule_stats
    AFTER INSERT ON rule_execution_history
    FOR EACH ROW
    EXECUTE FUNCTION update_rule_execution_stats();


-- =============================================================================
-- Sample Data (for development/testing)
-- =============================================================================

-- Sample classification rule
INSERT INTO tag_rules (id, name, description, type, target_filters, tags, enabled)
VALUES (
    'rule_sample_windows_servers',
    'Windows Server Assets',
    'Tags all Windows Server assets for easy filtering',
    'classification',
    '[{"field": "OS Type", "operator": "contains", "value": "Windows Server"}]'::jsonb,
    '{"label": "Windows-Server", "color": "#0078d4"}'::jsonb,
    false  -- Disabled by default, enable after testing
)
ON CONFLICT (id) DO NOTHING;

-- Sample compliance rule
INSERT INTO tag_rules (id, name, description, type, target_filters, tags, compliance_criteria, detection_config, enabled)
VALUES (
    'rule_sample_av_compliance',
    'Antivirus Protection Check',
    'Checks if Windows Servers have antivirus software installed',
    'compliance',
    '[{"field": "OS Type", "operator": "contains", "value": "Windows Server"}]'::jsonb,
    '{"pass": "AV-Protected", "fail": "AV-Missing"}'::jsonb,
    '{"logic": "any", "conditions": [{"field": "Software Installed", "operator": "contains", "value": "Defender"}, {"field": "Software Installed", "operator": "contains", "value": "SentinelOne"}]}'::jsonb,
    '{"enabled": true, "condition": "Pass rate below threshold", "threshold": 90, "severity": "High"}'::jsonb,
    false  -- Disabled by default
)
ON CONFLICT (id) DO NOTHING;


-- =============================================================================
-- Schema Verification Queries
-- =============================================================================

-- Verify all tables exist
SELECT
    table_name,
    (SELECT COUNT(*) FROM information_schema.columns WHERE table_name = t.table_name) as column_count
FROM information_schema.tables t
WHERE table_schema = 'public'
AND table_name IN ('tag_rules', 'asset_tags_v2', 'rule_execution_history', 'detection_alerts')
ORDER BY table_name;

-- Verify indexes
SELECT
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
AND tablename IN ('tag_rules', 'asset_tags_v2', 'rule_execution_history', 'detection_alerts')
ORDER BY tablename, indexname;

-- Verify foreign keys
SELECT
    tc.table_name,
    kcu.column_name,
    ccu.table_name AS foreign_table_name,
    ccu.column_name AS foreign_column_name
FROM information_schema.table_constraints AS tc
JOIN information_schema.key_column_usage AS kcu
    ON tc.constraint_name = kcu.constraint_name
    AND tc.table_schema = kcu.table_schema
JOIN information_schema.constraint_column_usage AS ccu
    ON ccu.constraint_name = tc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
AND tc.table_name IN ('tag_rules', 'asset_tags_v2', 'rule_execution_history', 'detection_alerts')
ORDER BY tc.table_name, kcu.column_name;
