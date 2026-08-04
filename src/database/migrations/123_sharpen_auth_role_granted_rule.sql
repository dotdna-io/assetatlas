-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 123: sharpen auth-admin-role-granted to an exact escalation (#823).
--
-- Migration 120 seeded this critical rule to *approximate* "escalated to
-- privileged": the user_updated audit row recorded only THAT the role changed
-- (metadata.changes = ['role']), not the old/new values, so the rule JOINed the
-- target user's CURRENT role from the users table and fired whenever that user
-- now held admin/super_admin. That mis-fires (an already-admin user edited for an
-- unrelated field, or a role toggled up-then-down, still reads as "privileged
-- now") and can't tell a genuine grant from a no-op.
--
-- #823 adds old_role/new_role to the user_updated audit metadata, so we can now
-- match the exact transition directly off the audit row and drop the users-table
-- join. Escalation = the target ends privileged AND was not privileged before:
--   new_role IN ('admin','super_admin') AND old_role NOT IN ('admin','super_admin').
-- Rows written before #823 lack these keys, so metadata ->> 'new_role' is NULL
-- and the IN/NOT IN predicates are simply false — they never match and never
-- error, keeping the 5-minute evaluation loop NULL-safe (migration 120 invariant).
--
-- Idempotent: a plain UPDATE keyed on the immutable slug; re-running is a no-op.
-- Migration 120 (which seeded the old shape) is immutable and left untouched.
-- Dual-written to src/database/schema-full.sql's alert_rules COPY block so fresh
-- installs — which bootstrap from schema-full.sql and mark migrations applied —
-- get the sharpened rule too.
BEGIN;

UPDATE alert_rules
SET query = $q$SELECT a.id, NULL AS hostname, NULL AS category, NULL AS subcategory,
       NULL AS ip, NULL AS os_normalized, a.created_at AS last_time,
       a.username, a.metadata ->> 'new_role' AS new_role,
       a.metadata ->> 'old_role' AS old_role, host(a.ip_address) AS source_ip
FROM auth_audit_log a
WHERE a.action = 'user_updated'
  AND a.success
  AND a.created_at > now() - interval '24 hours'
  AND a.metadata -> 'changes' ? 'role'
  AND a.metadata ->> 'new_role' IN ('admin', 'super_admin')
  AND a.metadata ->> 'old_role' NOT IN ('admin', 'super_admin')$q$,
    description = 'A user''s role was changed to admin or super_admin from a non-privileged role (user_updated carrying a role change) within the last 24 hours — an exact privilege escalation. Matches the old_role/new_role recorded in the audit metadata, so it fires only on a true non-privileged to privileged transition; no users-table join or current-role approximation.',
    updated_at = now()
WHERE slug = 'auth-admin-role-granted'
  AND source = 'system';

COMMIT;
