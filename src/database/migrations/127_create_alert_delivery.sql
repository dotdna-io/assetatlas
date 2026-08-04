-- SPDX-License-Identifier: AGPL-3.0-or-later
-- src/database/migrations/127_create_alert_delivery.sql
-- Alert delivery framework (#917): outbound channels + per-rule routing +
-- durable delivery queue/audit log. Backend enabler for #918-921 (webhook /
-- email / syslog / HEC). Delivery is enqueued during alert creation and drained
-- by the scheduler worker, so a slow channel never blocks the pipeline commit.
BEGIN;

CREATE TABLE IF NOT EXISTS alert_channels (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type        TEXT NOT NULL,
    name        TEXT NOT NULL UNIQUE,
    enabled     BOOLEAN NOT NULL DEFAULT true,
    config      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alert_rule_channels (
    rule_id      UUID NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
    channel_id   UUID NOT NULL REFERENCES alert_channels(id) ON DELETE CASCADE,
    min_severity TEXT NOT NULL DEFAULT 'low',
    PRIMARY KEY (rule_id, channel_id)
);
CREATE INDEX IF NOT EXISTS idx_alert_rule_channels_channel
    ON alert_rule_channels (channel_id);

CREATE TABLE IF NOT EXISTS alert_deliveries (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Nullable: real deliveries reference an alert; "send test" rows carry
    -- alert_id = NULL with trigger = 'test'.
    alert_id      UUID REFERENCES alerts(id) ON DELETE CASCADE,
    channel_id    UUID NOT NULL REFERENCES alert_channels(id) ON DELETE CASCADE,
    channel_type  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    attempts      INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error    TEXT,
    response_code INTEGER,
    payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
    trigger       TEXT NOT NULL DEFAULT 'created',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at       TIMESTAMPTZ
);
-- Drain claim: only pending rows that are due.
CREATE INDEX IF NOT EXISTS idx_alert_deliveries_pending
    ON alert_deliveries (next_retry_at) WHERE status = 'pending';
-- Retention prune by age.
CREATE INDEX IF NOT EXISTS idx_alert_deliveries_created_at
    ON alert_deliveries (created_at);

INSERT INTO system_settings (category, key, value, value_type, default_value, description)
VALUES
    ('alerting', 'ALERT_DELIVERY_ENABLED', 'true', 'boolean', 'true',
     'Master switch for outbound alert delivery (enqueue + drain)'),
    ('alerting', 'ALERT_DELIVERY_MAX_ATTEMPTS', '5', 'integer', '5',
     'Max send attempts per delivery before it is marked failed'),
    ('alerting', 'ALERT_DELIVERY_TIMEOUT_SECONDS', '10', 'integer', '10',
     'Per-attempt send timeout in seconds'),
    ('alerting', 'ALERT_DELIVERY_DRAIN_INTERVAL_SECONDS', '25', 'integer', '25',
     'How often the scheduler worker drains pending deliveries'),
    ('retention', 'ALERT_DELIVERIES_RETENTION_DAYS', '30', 'integer', '30',
     'Keep alert delivery/audit rows from the last N days')
ON CONFLICT (category, key) DO NOTHING;

COMMIT;
