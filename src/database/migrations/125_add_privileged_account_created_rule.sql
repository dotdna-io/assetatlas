-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 125: seed auth-privileged-account-created alert rule (#883).
--
-- #877 removed auth-user-created (migration 122) as low-signal, and the sharpened
-- auth-admin-role-granted (migration 123) keys on role *change* (user_updated), so
-- a privileged account *created* directly fires no alert. That is the primary
-- account-creation vector in SSO deployments: an IdP group-claim can provision a
-- first-login user straight into admin/super_admin, which #826/#882 record as a
-- user_created audit row carrying metadata.role — but nothing consumed them.
--
-- This seeds a narrow, high-signal replacement: fire only when a user_created event
-- within 24h carries metadata.role IN ('admin','super_admin'). It covers both
-- SSO auto-provisioning (metadata.role from #882) and manual admin creation
-- (metadata.role added to auth.py's create-user path in this change), so every
-- creation vector is caught while an ordinary (non-privileged) create stays silent.
--
-- Mirrors auth-admin-role-granted's settings (security / critical / sql /
-- evaluation_cadence='interval' / silence_duration=1440), so the 5-minute scheduler
-- evaluates it and a resolved alert cannot re-fire within the 24h window.
--
-- NULL-safe (migration 120 invariant): a creation with no role in metadata yields
-- metadata ->> 'role' = NULL, and NULL IN (...) is simply false — it never matches
-- and never errors, so a single bad row cannot abort the evaluation loop. The query
-- is SELECT-only, per-event (returns the real auth_audit_log.id), and follows the
-- seeded-rule column shape (the seven canonical asset columns NULL except id and
-- last_time), surfacing the assigned role, provisioning source, username, and IP.
--
-- Idempotent: ON CONFLICT (slug) DO NOTHING. Dual-written to
-- src/database/schema-full.sql's alert_rules COPY block (byte-identical query text)
-- so fresh installs — which bootstrap from schema-full.sql and mark migrations
-- already-applied — get this rule too.

BEGIN;

INSERT INTO alert_rules
    (name, slug, type, severity, description, query,
     target_filters, compliance_criteria, threshold, silence_duration,
     enabled, evaluation_type, evaluation_cadence, source)
VALUES
    (
        'Auth — Privileged Account Created',
        'auth-privileged-account-created',
        'security',
        'critical',
        'A user account was created with a privileged role (admin or super_admin) within the last 24 hours (user_created carrying metadata.role). Covers an IdP group-claim provisioning a first-login SSO user straight into a privileged role and a manual admin creating a privileged account; the sharpened auth-admin-role-granted rule matches only role changes (user_updated), not creations. NULL-safe: a creation with no role in metadata simply never matches.',
        $q$SELECT a.id, NULL AS hostname, NULL AS category, NULL AS subcategory,
       NULL AS ip, NULL AS os_normalized, a.created_at AS last_time,
       a.username, a.metadata ->> 'role' AS granted_role,
       a.metadata ->> 'source' AS provision_source, host(a.ip_address) AS source_ip
FROM auth_audit_log a
WHERE a.action = 'user_created'
  AND a.success
  AND a.created_at > now() - interval '24 hours'
  AND a.metadata ->> 'role' IN ('admin', 'super_admin')$q$,
        NULL,
        NULL,
        '{"min":1}'::jsonb,
        1440,
        true,
        'sql',
        'interval',
        'system'
    )
ON CONFLICT (slug) DO NOTHING;

COMMIT;
