-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 120: auth/audit security alert rule pack (#584 §17, phase 4)
--
-- Seeds nine SQL rules over auth_audit_log / settings_audit_log on the
--   'interval' cadence added in Phase 3 (migration 119): the scheduler worker
--   evaluates them every 5 minutes, out of band from the pipeline. Failed-login
--   bursts can't wait for tomorrow's pipeline run, so none of these are
--   'pipeline' cadence. This is seeds only — the evaluation engine is unchanged.
--
-- Shape: auth alerts carry no asset semantics, so each query mirrors migration
--   119's ootb-chronicle-silence — the seven canonical asset columns are NULL
--   (hostname/category/subcategory/ip/os_normalized) except `id` and last_time,
--   with the auth-specific fields (username, source_ip, ...) projected as extra
--   columns for readability. The evaluator persists only `id` + match count, so
--   `id` must be a uuid (alerts.asset_ids is uuid[]): per-event rules return the
--   real auth_audit_log.id; aggregate rules (GROUP BY username/ip/settings row)
--   synthesise a stable md5('<slug>:'||key)::uuid so a sustained condition keeps
--   the same identity and updates ONE active alert across ticks (no duplicates).
--   Every query is SELECT-only, NULL-safe, and never casts an unguarded value,
--   so a single bad row can never abort the 5-minute evaluation loop.
--
-- Idempotent: ON CONFLICT (slug) DO NOTHING. Every seed row is dual-written to
--   src/database/schema-full.sql's alert_rules COPY block (byte-identical query
--   text) so fresh installs — which bootstrap from schema-full.sql and mark
--   migrations as already-applied — get the same rules as upgrades.

BEGIN;

INSERT INTO alert_rules
    (name, slug, type, severity, description, query,
     target_filters, compliance_criteria, threshold, silence_duration,
     enabled, evaluation_type, evaluation_cadence, source)
VALUES
    (
        'Auth — Failed Login Burst (per user)',
        'auth-failed-login-burst-user',
        'security',
        'high',
        'At least 10 failed interactive logins for a single username within 15 minutes — likely password guessing or credential stuffing against that account. Failed logins are the login/login_ldap/login_mfa/login_oidc actions with success=false.',
        $q$SELECT md5('auth-failed-login-burst-user:' || username)::uuid AS id,
       NULL AS hostname, NULL AS category, NULL AS subcategory,
       NULL AS ip, NULL AS os_normalized,
       max(created_at) AS last_time,
       username, count(*) AS failure_count
FROM auth_audit_log
WHERE action IN ('login', 'login_ldap', 'login_mfa', 'login_oidc')
  AND NOT success
  AND username IS NOT NULL
  AND created_at > now() - interval '15 minutes'
GROUP BY username
HAVING count(*) >= 10$q$,
        NULL,
        NULL,
        '{"min":1}'::jsonb,
        60,
        true,
        'sql',
        'interval',
        'system'
    ),
    (
        'Auth — Failed Login Burst (per source IP)',
        'auth-failed-login-burst-ip',
        'security',
        'high',
        'At least 20 failed interactive logins from a single source IP within 15 minutes — likely an automated credential attack from that address. Catches floods where the username is unknown or rotating (e.g. early-stage OIDC failures log no username).',
        $q$SELECT md5('auth-failed-login-burst-ip:' || host(ip_address))::uuid AS id,
       NULL AS hostname, NULL AS category, NULL AS subcategory,
       NULL AS ip, NULL AS os_normalized,
       max(created_at) AS last_time,
       host(ip_address) AS source_ip, count(*) AS failure_count
FROM auth_audit_log
WHERE action IN ('login', 'login_ldap', 'login_mfa', 'login_oidc')
  AND NOT success
  AND ip_address IS NOT NULL
  AND created_at > now() - interval '15 minutes'
GROUP BY ip_address
HAVING count(*) >= 20$q$,
        NULL,
        NULL,
        '{"min":1}'::jsonb,
        60,
        true,
        'sql',
        'interval',
        'system'
    ),
    (
        'Auth — Refresh Token Reuse Detected',
        'auth-refresh-token-reuse',
        'security',
        'critical',
        'A rotated refresh token was replayed (refresh_token_reuse_detected) within the last 15 minutes — a strong indicator of token theft or session hijacking. Returns the affected username (may be null for pre-auth reuse), source IP, and time.',
        $q$SELECT id, NULL AS hostname, NULL AS category, NULL AS subcategory,
       NULL AS ip, NULL AS os_normalized, created_at AS last_time,
       username, host(ip_address) AS source_ip
FROM auth_audit_log
WHERE action = 'refresh_token_reuse_detected'
  AND created_at > now() - interval '15 minutes'$q$,
        NULL,
        NULL,
        '{"min":1}'::jsonb,
        60,
        true,
        'sql',
        'interval',
        'system'
    ),
    (
        'Auth — Admin/Super-Admin Role Granted',
        'auth-admin-role-granted',
        'security',
        'critical',
        'A user''s role was changed (user_updated carrying a role change) within 24 hours and that user now holds admin or super_admin — a privilege escalation to review. The audit trail records that the role changed but not the target role, so the user''s current role is joined to approximate "escalated to privileged"; a follow-up issue tracks recording old/new role for an exact match.',
        $q$SELECT a.id, NULL AS hostname, NULL AS category, NULL AS subcategory,
       NULL AS ip, NULL AS os_normalized, a.created_at AS last_time,
       a.username, u.role AS current_role, host(a.ip_address) AS source_ip
FROM auth_audit_log a
JOIN users u ON u.id = a.user_id
WHERE a.action = 'user_updated'
  AND a.success
  AND a.created_at > now() - interval '24 hours'
  AND a.metadata -> 'changes' ? 'role'
  AND u.role IN ('admin', 'super_admin')$q$,
        NULL,
        NULL,
        '{"min":1}'::jsonb,
        1440,
        true,
        'sql',
        'interval',
        'system'
    ),
    (
        'Auth — MFA Disabled',
        'auth-mfa-disabled',
        'security',
        'medium',
        'Multi-factor authentication was removed for an account within the last 24 hours — self-service disable (mfa_disabled) or an admin resetting another user''s MFA (user_mfa_reset_by_admin), both of which delete the account''s MFA methods. Weakens that account''s protection.',
        $q$SELECT id, NULL AS hostname, NULL AS category, NULL AS subcategory,
       NULL AS ip, NULL AS os_normalized, created_at AS last_time,
       username, host(ip_address) AS source_ip
FROM auth_audit_log
WHERE action IN ('mfa_disabled', 'user_mfa_reset_by_admin')
  AND success
  AND created_at > now() - interval '24 hours'$q$,
        NULL,
        NULL,
        '{"min":1}'::jsonb,
        1440,
        true,
        'sql',
        'interval',
        'system'
    ),
    (
        'Auth — User Account Created',
        'auth-user-created',
        'security',
        'low',
        'A user account was created within the last 24 hours (user_created).',
        $q$SELECT id, NULL AS hostname, NULL AS category, NULL AS subcategory,
       NULL AS ip, NULL AS os_normalized, created_at AS last_time,
       username, host(ip_address) AS source_ip
FROM auth_audit_log
WHERE action = 'user_created'
  AND success
  AND created_at > now() - interval '24 hours'$q$,
        NULL,
        NULL,
        '{"min":1}'::jsonb,
        1440,
        true,
        'sql',
        'interval',
        'system'
    ),
    (
        'Auth — User Account Deleted',
        'auth-user-deleted',
        'security',
        'low',
        'A user account was permanently deleted within the last 24 hours (user_deleted_permanent).',
        $q$SELECT id, NULL AS hostname, NULL AS category, NULL AS subcategory,
       NULL AS ip, NULL AS os_normalized, created_at AS last_time,
       username, host(ip_address) AS source_ip
FROM auth_audit_log
WHERE action = 'user_deleted_permanent'
  AND success
  AND created_at > now() - interval '24 hours'$q$,
        NULL,
        NULL,
        '{"min":1}'::jsonb,
        1440,
        true,
        'sql',
        'interval',
        'system'
    ),
    (
        'Auth — Security Settings Changed',
        'auth-settings-changed',
        'security',
        'high',
        'A security-relevant settings category (security, ldap, email/SMTP, or oidc) was modified within the last 15 minutes. Returns the category, key, and who changed it.',
        $q$SELECT md5('auth-settings-changed:' || id::text)::uuid AS id,
       NULL AS hostname, NULL AS category, NULL AS subcategory,
       NULL AS ip, NULL AS os_normalized, created_at AS last_time,
       category AS setting_category, key AS setting_key, changed_by_username
FROM settings_audit_log
WHERE category IN ('security', 'ldap', 'email', 'oidc')
  AND created_at > now() - interval '15 minutes'$q$,
        NULL,
        NULL,
        '{"min":1}'::jsonb,
        60,
        true,
        'sql',
        'interval',
        'system'
    ),
    (
        'Auth — Admin Login Outside Business Hours',
        'auth-admin-login-off-hours',
        'security',
        'medium',
        'A successful admin or super_admin login occurred outside business hours (before 06:00 or at/after 22:00 server time) within the last 24 hours. Disabled by default — enable and tune the hour window to your organisation''s schedule.',
        $q$SELECT a.id, NULL AS hostname, NULL AS category, NULL AS subcategory,
       NULL AS ip, NULL AS os_normalized, a.created_at AS last_time,
       a.username, u.role AS current_role, host(a.ip_address) AS source_ip
FROM auth_audit_log a
JOIN users u ON u.id = a.user_id
WHERE a.action IN ('login', 'login_ldap', 'login_mfa', 'login_oidc')
  AND a.success
  AND a.created_at > now() - interval '24 hours'
  AND u.role IN ('admin', 'super_admin')
  AND (extract(hour FROM a.created_at) < 6 OR extract(hour FROM a.created_at) >= 22)$q$,
        NULL,
        NULL,
        '{"min":1}'::jsonb,
        1440,
        false,
        'sql',
        'interval',
        'system'
    )
ON CONFLICT (slug) DO NOTHING;

COMMIT;
