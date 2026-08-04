-- SPDX-License-Identifier: AGPL-3.0-or-later
BEGIN;

-- ============================================================================
-- Audit Trail Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS audit_trail (
    id               BIGSERIAL PRIMARY KEY,
    timestamp        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id          UUID,
    username         VARCHAR(255),
    method           VARCHAR(10) NOT NULL,
    path             VARCHAR(500) NOT NULL,
    status_code      INTEGER NOT NULL,
    ip_address       VARCHAR(45),
    user_agent       TEXT,
    request_body     JSONB,
    response_summary VARCHAR(500),
    duration_ms      INTEGER,
    request_id       VARCHAR(64)
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_audit_trail_timestamp
    ON audit_trail(timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_audit_trail_user_id
    ON audit_trail(user_id);

CREATE INDEX IF NOT EXISTS idx_audit_trail_method
    ON audit_trail(method);

CREATE INDEX IF NOT EXISTS idx_audit_trail_path
    ON audit_trail(path text_pattern_ops);

CREATE INDEX IF NOT EXISTS idx_audit_trail_status_code
    ON audit_trail(status_code);

-- ============================================================================
-- Data Retention Settings
-- ============================================================================

INSERT INTO system_settings (category, key, value, value_type, default_value, description, validation_rule, requires_restart)
VALUES
    ('retention', 'AUDIT_TRAIL_RETENTION_DAYS', '90', 'integer', '90',
     'Number of days to retain audit trail records',
     '{"min": 7, "max": 730}', FALSE),

    ('retention', 'AUDIT_TRAIL_LOG_READS', 'false', 'boolean', 'false',
     'Whether to log GET/read requests in the audit trail',
     NULL, FALSE),

    ('retention', 'RAW_SOURCE_DATA_RETENTION_DAYS', '90', 'integer', '90',
     'Number of days to retain raw source data from integrations',
     '{"min": 7, "max": 730}', FALSE),

    ('retention', 'ASSET_CHRONICLE_RETENTION_DAYS', '90', 'integer', '90',
     'Number of days to retain asset chronicle (history) records',
     '{"min": 7, "max": 730}', FALSE),

    ('retention', 'EXCLUDED_RECORDS_RETENTION_DAYS', '90', 'integer', '90',
     'Number of days to retain excluded asset records',
     '{"min": 7, "max": 730}', FALSE),

    ('retention', 'BATCH_DATA_RETENTION_DAYS', '90', 'integer', '90',
     'Number of days to retain batch processing data',
     '{"min": 7, "max": 730}', FALSE),

    ('retention', 'AUTH_AUDIT_LOG_RETENTION_DAYS', '90', 'integer', '90',
     'Number of days to retain authentication audit log entries',
     '{"min": 7, "max": 730}', FALSE)

ON CONFLICT (category, key) DO NOTHING;

COMMIT;
