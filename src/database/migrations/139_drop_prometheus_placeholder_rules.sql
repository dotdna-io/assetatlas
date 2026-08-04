-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 139: drop the four system-* Prometheus placeholder rows so the
-- boot-time sync can own those slugs.
--
-- monitoring/alerts/*.yml ships eleven Prometheus alerts, but only seven ever
-- reached the alerts table. Two guards agree on the same column:
--
--   PrometheusWebhookService._lookup_rule_id
--     WHERE slug = %(slug)s AND source = 'prometheus'
--   sync_prometheus_rules
--     ON CONFLICT (slug) DO UPDATE ... WHERE alert_rules.source = 'prometheus'
--
-- Migration 119 seeded these four slugs as source='system', enabled=false,
-- empty-query placeholders. The sync's INSERT therefore conflicted, its DO UPDATE
-- was filtered out by the source guard, no prometheus-owned row was ever written,
-- and every alert that fired was dropped on arrival with
-- "No alert_rules row for alertname=... — skipping". system-scheduler-stalled is
-- critical, so a dead scheduler raised nothing in the UI.
--
-- The other seven work only because 119 never seeded placeholders for them.
-- Deleting these restores that consistency: with the monitoring overlay running,
-- sync_prometheus_rules recreates all eleven as source='prometheus' at API boot.
-- Without the overlay none of the eleven are listed, which is the honest state —
-- nothing evaluates them.
--
-- Alerts referencing these rule_ids would cascade via the alerts.rule_id FK
-- (ON DELETE CASCADE, migration 037). There are none to lose: the webhook only
-- resolves source='prometheus' rows, and the pipeline evaluator excludes both
-- disabled rules and source='prometheus' (src/modules/alerts/repository.py).
-- DELETE is idempotent; migrations 119 and 133 are immutable and left untouched.
BEGIN;

DELETE FROM alert_rules
 WHERE source = 'system'
   AND slug IN ('system-api-error-rate',
                'system-db-slow-queries',
                'system-db-connection-pool',
                'system-scheduler-stalled');

COMMIT;
