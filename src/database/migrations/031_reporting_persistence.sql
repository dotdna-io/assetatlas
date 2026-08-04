-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Reporting Dashboard Persistence (System-Wide Reports)
-- Version: 031
-- Description: Add tables for storing system-wide reports with audit trail
-- Date: 2025-11-20

-- ============================================================================
-- Reports Table (System-Wide)
-- ============================================================================

CREATE TABLE IF NOT EXISTS reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(255) NOT NULL,
    description TEXT,
    charts JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,  -- Audit: who created
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_by UUID REFERENCES users(id) ON DELETE SET NULL,  -- Audit: who last updated

    -- Constraints
    CONSTRAINT reports_title_not_empty CHECK (title <> '')
);

-- Create indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_reports_created_at ON reports(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reports_updated_at ON reports(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_reports_title ON reports(title);
CREATE INDEX IF NOT EXISTS idx_reports_created_by ON reports(created_by);
CREATE INDEX IF NOT EXISTS idx_reports_updated_by ON reports(updated_by);

-- Full-text search support for charts JSONB (GIN index for JSONB operations)
CREATE INDEX IF NOT EXISTS idx_reports_charts_gin ON reports USING gin(charts);

COMMENT ON TABLE reports IS 'System-wide reports with chart configurations (shared across all users)';
COMMENT ON COLUMN reports.charts IS 'JSONB array of chart configurations (layout, query config, visual settings)';
COMMENT ON COLUMN reports.created_by IS 'User who created the report (audit trail)';
COMMENT ON COLUMN reports.updated_by IS 'User who last updated the report (audit trail)';

-- ============================================================================
-- System Dashboard Preferences (System-Wide)
-- ============================================================================

-- One shared working dashboard that all users see (no real-time sync, refresh to see changes)
CREATE TABLE IF NOT EXISTS system_dashboard_preferences (
    id INTEGER PRIMARY KEY DEFAULT 1,  -- Only one row
    working_dashboard JSONB NOT NULL DEFAULT '{"charts": []}'::jsonb,
    last_modified TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    last_modified_by UUID REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT single_row_check CHECK (id = 1)
);

CREATE INDEX IF NOT EXISTS idx_system_prefs_dashboard_gin ON system_dashboard_preferences USING gin(working_dashboard);

COMMENT ON TABLE system_dashboard_preferences IS 'System-wide working dashboard (shared by all users, no real-time sync)';

-- ============================================================================
-- Audit Log Table (Optional - for detailed change tracking)
-- ============================================================================

CREATE TABLE IF NOT EXISTS reports_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id UUID REFERENCES reports(id) ON DELETE CASCADE,
    action VARCHAR(50) NOT NULL,  -- 'created', 'updated', 'deleted'
    changed_by UUID REFERENCES users(id) ON DELETE SET NULL,
    changed_by_username VARCHAR(255),
    changed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    changes JSONB,  -- What changed (for updates)

    CONSTRAINT valid_action CHECK (action IN ('created', 'updated', 'deleted'))
);

CREATE INDEX IF NOT EXISTS idx_reports_audit_report_id ON reports_audit_log(report_id);
CREATE INDEX IF NOT EXISTS idx_reports_audit_changed_at ON reports_audit_log(changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_reports_audit_changed_by ON reports_audit_log(changed_by);

COMMENT ON TABLE reports_audit_log IS 'Audit trail for all report changes (who, when, what)';

-- ============================================================================
-- Triggers: Auto-update timestamps
-- ============================================================================

-- Trigger function for reports.updated_at
CREATE OR REPLACE FUNCTION update_reports_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER reports_updated_at_trigger
    BEFORE UPDATE ON reports
    FOR EACH ROW
    EXECUTE FUNCTION update_reports_timestamp();

-- Trigger function for system_dashboard_preferences.last_modified
CREATE OR REPLACE FUNCTION update_system_prefs_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_modified = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER system_prefs_last_modified_trigger
    BEFORE UPDATE ON system_dashboard_preferences
    FOR EACH ROW
    EXECUTE FUNCTION update_system_prefs_timestamp();

-- Initialize the single row for system preferences
INSERT INTO system_dashboard_preferences (id, working_dashboard)
VALUES (1, '{"charts": []}'::jsonb)
ON CONFLICT (id) DO NOTHING;

-- ============================================================================
-- Verification
-- ============================================================================

-- Verify tables created
SELECT 'reports table created' as status
WHERE EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'reports');

SELECT 'system_dashboard_preferences table created' as status
WHERE EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'system_dashboard_preferences');

SELECT 'reports_audit_log table created' as status
WHERE EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'reports_audit_log');

-- Display table structures for verification
SELECT
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_name = 'reports'
ORDER BY ordinal_position;
