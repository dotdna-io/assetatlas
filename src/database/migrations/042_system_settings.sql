-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: System Settings for Dynamic Configuration
-- Version: 007
-- Description: Add tables for storing system settings with audit trail
-- Date: 2025-01-15

BEGIN;

-- ============================================================================
-- System Settings Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS system_settings (
    id SERIAL PRIMARY KEY,
    category VARCHAR(50) NOT NULL,  -- 'database', 'cache', 'performance', 'general', 'retention'
    key VARCHAR(100) NOT NULL,      -- 'DB_SLOW_QUERY_THRESHOLD_MS', etc.
    value TEXT NOT NULL,             -- Setting value (stored as string, parsed by type)
    value_type VARCHAR(20) NOT NULL, -- 'integer', 'boolean', 'string', 'json'
    default_value TEXT NOT NULL,     -- Default value if setting is reset
    description TEXT,                -- Human-readable description
    validation_rule TEXT,            -- JSON validation rule: {"min": 1, "max": 10000}
    is_sensitive BOOLEAN DEFAULT FALSE, -- Hide value in UI (for credentials)
    requires_restart BOOLEAN DEFAULT FALSE, -- Whether change requires app restart
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_by VARCHAR(255),         -- User ID who last updated

    CONSTRAINT unique_setting UNIQUE (category, key)
);

-- Create indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_settings_category ON system_settings(category);
CREATE INDEX IF NOT EXISTS idx_settings_updated_at ON system_settings(updated_at DESC);

-- ============================================================================
-- Settings Audit Log Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS settings_audit_log (
    id SERIAL PRIMARY KEY,
    setting_id INTEGER NOT NULL REFERENCES system_settings(id) ON DELETE CASCADE,
    category VARCHAR(50) NOT NULL,
    key VARCHAR(100) NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_by VARCHAR(255) NOT NULL,  -- User ID
    changed_by_username VARCHAR(255),  -- Username for readability
    change_reason TEXT,                 -- Why was this changed
    ip_address VARCHAR(45),            -- IP of user making change
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Create indexes for audit queries
CREATE INDEX IF NOT EXISTS idx_settings_audit_setting ON settings_audit_log(setting_id);
CREATE INDEX IF NOT EXISTS idx_settings_audit_created ON settings_audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_settings_audit_user ON settings_audit_log(changed_by);

-- ============================================================================
-- Insert Default Settings
-- ============================================================================

-- Database Query Logging Settings
INSERT INTO system_settings (category, key, value, value_type, default_value, description, validation_rule, requires_restart)
VALUES
    ('database', 'DB_SLOW_QUERY_THRESHOLD_MS', '100', 'integer', '100',
     'Threshold in milliseconds for logging slow queries',
     '{"min": 1, "max": 10000}', FALSE),

    ('database', 'DB_ENABLE_QUERY_LOGGING', 'true', 'boolean', 'true',
     'Enable database query logging',
     NULL, FALSE),

    ('database', 'DB_LOG_ALL_QUERIES', 'false', 'boolean', 'false',
     'Log ALL queries (very verbose, use with caution)',
     NULL, FALSE)

ON CONFLICT (category, key) DO NOTHING;

-- Cache Logging Settings
INSERT INTO system_settings (category, key, value, value_type, default_value, description, validation_rule, requires_restart)
VALUES
    ('cache', 'CACHE_ENABLE_LOGGING', 'true', 'boolean', 'true',
     'Enable cache operation logging',
     NULL, FALSE),

    ('cache', 'CACHE_LOG_HITS', 'false', 'boolean', 'false',
     'Log cache hits (verbose, useful for debugging)',
     NULL, FALSE)

ON CONFLICT (category, key) DO NOTHING;

-- Performance Tracking Settings
INSERT INTO system_settings (category, key, value, value_type, default_value, description, validation_rule, requires_restart)
VALUES
    ('performance', 'PERF_ENABLE_TRACKING', 'true', 'boolean', 'true',
     'Enable performance tracking and logging',
     NULL, FALSE),

    ('performance', 'PERF_ENABLE_BUDGET_WARNINGS', 'true', 'boolean', 'true',
     'Enable warnings when endpoints exceed performance budgets',
     NULL, FALSE),

    ('performance', 'PERF_DEFAULT_BUDGET_MS', '1000', 'integer', '1000',
     'Default performance budget in milliseconds',
     '{"min": 100, "max": 10000}', FALSE),

    ('performance', 'PERF_ENDPOINT_BUDGETS', '{}', 'json', '{}',
     'Per-endpoint performance budgets (JSON)',
     NULL, FALSE)

ON CONFLICT (category, key) DO NOTHING;

-- General Logging Settings
INSERT INTO system_settings (category, key, value, value_type, default_value, description, validation_rule, requires_restart)
VALUES
    ('general', 'LOG_LEVEL', 'INFO', 'string', 'INFO',
     'Application log level',
     '{"enum": ["DEBUG", "INFO", "WARNING", "ERROR"]}', TRUE),

    ('general', 'LOG_REQUEST_BODY_ON_ERROR', 'true', 'boolean', 'true',
     'Log request body when errors occur',
     NULL, FALSE)

ON CONFLICT (category, key) DO NOTHING;

-- Log Retention Settings
INSERT INTO system_settings (category, key, value, value_type, default_value, description, validation_rule, requires_restart)
VALUES
    ('retention', 'LOG_RETENTION_DAYS', '30', 'integer', '30',
     'Application log retention period in days',
     '{"min": 7, "max": 365}', FALSE),

    ('retention', 'LOG_AUTO_CLEANUP_ENABLED', 'true', 'boolean', 'true',
     'Enable automatic log cleanup',
     NULL, FALSE),

    ('retention', 'LOG_ROTATION_SIZE_MB', '100', 'integer', '100',
     'Log file rotation size in megabytes',
     '{"min": 10, "max": 1000}', TRUE),

    ('retention', 'AUDIT_LOG_RETENTION_DAYS', '365', 'integer', '365',
     'Audit log retention period in days (minimum 90 for compliance)',
     '{"min": 90, "max": 730}', FALSE)

ON CONFLICT (category, key) DO NOTHING;

-- ============================================================================
-- Trigger: Update updated_at timestamp
-- ============================================================================

CREATE OR REPLACE FUNCTION update_settings_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS settings_updated_at_trigger ON system_settings;
CREATE TRIGGER settings_updated_at_trigger
    BEFORE UPDATE ON system_settings
    FOR EACH ROW
    EXECUTE FUNCTION update_settings_timestamp();

-- ============================================================================
-- Verification
-- ============================================================================

-- Verify tables created
SELECT 'system_settings table created' as status
WHERE EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'system_settings');

SELECT 'settings_audit_log table created' as status
WHERE EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'settings_audit_log');

-- Count default settings
SELECT COUNT(*) as default_settings_count FROM system_settings;

COMMIT;
