-- SPDX-License-Identifier: AGPL-3.0-or-later
-- src/database/migrations/126_create_alert_events.sql
-- Per-run alert firing history (#906): backend enabler for #904 (latest-run
-- highlight) and #905 (firing-history timechart). One snapshot row per pipeline
-- evaluation of an alert, keyed on pipeline_runs.id (the run id the UI knows).
BEGIN;

CREATE TABLE IF NOT EXISTS alert_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_id        UUID NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    rule_id         UUID NOT NULL,
    pipeline_run_id UUID NOT NULL,
    status          TEXT NOT NULL,
    severity        TEXT NOT NULL,
    asset_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_alert_events_alert_id
    ON alert_events (alert_id, created_at);
CREATE INDEX IF NOT EXISTS idx_alert_events_pipeline_run
    ON alert_events (pipeline_run_id);

-- Cheap "does this alert's latest evaluation belong to the latest run?" pointer (#904).
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS last_pipeline_run_id UUID;

-- Retention knobs (consumed by DataRetentionService; mirrors 052/053).
INSERT INTO system_settings (category, key, value, value_type, default_value, description)
VALUES
    ('retention', 'ALERT_EVENTS_RETENTION_DAYS', '90', 'integer', '90',
     'Keep alert firing-history rows from the last N days'),
    ('retention', 'ALERT_EVENTS_RETENTION_COUNT', '30', 'integer', '30',
     'Keep alert firing-history for the latest N pipeline runs (regardless of age)')
ON CONFLICT (category, key) DO NOTHING;

COMMIT;
