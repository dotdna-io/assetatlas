-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 083: Widen alert_rules.source CHECK; add fingerprint and labels to alerts
-- Date: 2026-04-24
-- Issue: #487 (Sub-project A)

BEGIN;

-- 1. Allow source='prometheus' on alert_rules
ALTER TABLE alert_rules DROP CONSTRAINT IF EXISTS alert_rules_source_check;
ALTER TABLE alert_rules
    ADD CONSTRAINT alert_rules_source_check
    CHECK (source IN ('system', 'user', 'prometheus'));

COMMENT ON COLUMN alert_rules.source IS
    'system | user | prometheus (added 2026-04-24). For prometheus rules, query column stores PromQL.';

-- 2. Fingerprint + labels on alerts for Alertmanager-based dedup
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS fingerprint TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS labels JSONB;

COMMENT ON COLUMN alerts.fingerprint IS
    'Alertmanager-provided stable hash of the alert''s labels. NULL for non-Prometheus alerts.';
COMMENT ON COLUMN alerts.labels IS
    'Prometheus labels JSONB (e.g. instance, service, job). NULL for non-Prometheus alerts.';

-- 3. Partial index for fast dedup lookup on active Prometheus alerts
CREATE INDEX IF NOT EXISTS idx_alerts_fingerprint_active
    ON alerts(rule_id, fingerprint)
    WHERE status = 'active';

COMMIT;
