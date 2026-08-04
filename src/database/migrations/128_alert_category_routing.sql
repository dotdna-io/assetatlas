-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 128: per-category alert delivery routing (#918)
-- Replaces #917's per-rule routing table (never released/used) with a per-CATEGORY one:
-- delivery channels are assigned to a rule category, not an individual rule.
BEGIN;

DROP TABLE IF EXISTS alert_rule_channels;

CREATE TABLE IF NOT EXISTS alert_category_channels (
    category      TEXT NOT NULL CHECK (category IN
                    ('security', 'compliance', 'operational', 'data_quality', 'custom')),
    channel_id    UUID NOT NULL REFERENCES alert_channels(id) ON DELETE CASCADE,
    min_severity  TEXT NOT NULL DEFAULT 'low'
                    CHECK (min_severity IN ('low', 'medium', 'high', 'critical')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (category, channel_id)
);

CREATE INDEX IF NOT EXISTS idx_alert_category_channels_channel
    ON alert_category_channels(channel_id);

COMMIT;
