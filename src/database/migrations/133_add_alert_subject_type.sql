-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 133: alert subject types + generic subjects payload.
-- Adds alert_rules.subject_type (source of truth) and denormalized
-- alerts.subject_type + alerts.subjects jsonb; assigns subject_type to the
-- built-in rules; re-authors the three typed-list rules' queries to the standard
-- subject_id / subject_label / subject_sublabel projection. Idempotent.
BEGIN;

ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS subject_type text NOT NULL DEFAULT 'asset';
ALTER TABLE alert_rules DROP CONSTRAINT IF EXISTS alert_rules_subject_type_check;
ALTER TABLE alert_rules ADD CONSTRAINT alert_rules_subject_type_check
    CHECK (subject_type IN ('asset','integration','audit_event','pipeline_run','metric','system'));

ALTER TABLE alerts ADD COLUMN IF NOT EXISTS subject_type text NOT NULL DEFAULT 'asset';
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS subjects jsonb;

UPDATE alert_rules SET subject_type = 'integration'
    WHERE slug IN ('sync-failures','data-source-stale');
UPDATE alert_rules SET subject_type = 'audit_event'
    WHERE slug = 'auth-admin-role-granted';
UPDATE alert_rules SET subject_type = 'pipeline_run'
    WHERE slug IN ('assets-disappeared','ootb-chronicle-silence');
UPDATE alert_rules SET subject_type = 'metric'
    WHERE slug = 'posture-tier-drop-secured-to-monitored-pct';
UPDATE alert_rules SET subject_type = 'system'
    WHERE slug IN ('pipeline-stale','pipeline-last-run-failed','pipeline-stuck',
                   'system-api-error-rate','system-db-slow-queries',
                   'system-db-connection-pool','system-scheduler-stalled');

UPDATE alert_rules SET query = $q$SELECT id AS subject_id,
       name AS subject_label,
       integration_type || ' · sync failed' AS subject_sublabel
FROM integrations
WHERE last_sync_status = 'failed' AND status = 'enabled'$q$
    WHERE slug = 'sync-failures';

UPDATE alert_rules SET query = $q$SELECT id AS subject_id,
       name AS subject_label,
       integration_type || ' · not synced 24h+' AS subject_sublabel
FROM integrations
WHERE status = 'enabled'
  AND (last_sync_at IS NULL OR last_sync_at < now() - interval '24 hours')$q$
    WHERE slug = 'data-source-stale';

UPDATE alert_rules SET query = $q$SELECT a.id AS subject_id,
       a.username || ' → ' || (a.metadata ->> 'new_role') AS subject_label,
       'was ' || (a.metadata ->> 'old_role') || ' · ' || host(a.ip_address) AS subject_sublabel
FROM auth_audit_log a
WHERE a.action = 'user_updated' AND a.success
  AND a.created_at > now() - interval '24 hours'
  AND a.metadata -> 'changes' ? 'role'
  AND a.metadata ->> 'new_role' IN ('admin','super_admin')
  AND a.metadata ->> 'old_role' NOT IN ('admin','super_admin')$q$
    WHERE slug = 'auth-admin-role-granted';

COMMIT;
