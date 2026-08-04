-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 145: repoint the auth-admin-role-granted alert rule at audit_trail.
-- Part of #1294 — PR-0 of the auth_audit_log retirement (PR-A stops writes,
-- PR-B drops the table).
--
-- The #1150 arc made audit_trail the audit system of record: every auth event
-- logged through AuditService.log_auth annotates the in-flight request's
-- audit_trail row (category='auth', resource_type='authentication',
-- action='success'|'failure' — the OUTCOME), with the semantic payload in
-- details jsonb: details->>'event' carries the action name auth_audit_log.action
-- held, plus the flattened metadata keys (changes/old_role/new_role) and the
-- acting identity (username, user_id, ip_address). auth_audit_log is only a
-- deprecation-window dual-write; once PR-A lands it freezes, and a rule still
-- querying it would silently watch a frozen table forever. This is the last
-- live rule that does — migration 132 already removed the other seeded rules.
--
-- The repoint is field-for-field. subject_type stays 'audit_event' (set by
-- migration 133) and the subject_id/subject_label/subject_sublabel projection
-- is unchanged in shape — only the source table and field references move:
--   action = 'user_updated'         -> details ->> 'event' = 'user_updated'
--   success                         -> action = 'success'
--   created_at > now() - 24h        -> "timestamp" > now() - 24h (reserved word,
--                                      quoted; windowed FIRST — audit_trail is
--                                      far larger than auth_audit_log, and
--                                      migration 047's idx_audit_trail_timestamp
--                                      (timestamp DESC) serves the predicate)
--   metadata -> 'changes' ? 'role'  -> details -> 'changes' ? 'role'
--   metadata ->> 'new_role' IN ...  -> details ->> 'new_role' IN ...
--   metadata ->> 'old_role' NOT IN  -> COALESCE(details ->> 'old_role', '')
--                                      NOT IN ... — a negated predicate over an
--                                      absent jsonb key evaluates to NULL and
--                                      silently drops the row
--   username / host(ip_address)     -> details ->> 'username' /
--                                      details ->> 'ip_address' — plain text
--                                      from request.remote_addr; host() is
--                                      dropped rather than replaced with an
--                                      unguarded ::inet cast, which a single
--                                      malformed value would turn into an
--                                      aborted 5-minute evaluation loop
--                                      (migration 120 invariant), and
--                                      remote_addr never carries a netmask.
-- category='auth' AND resource_type='authentication' replace the table scope
-- (auth_audit_log held auth events only). subject_id becomes a bigint
-- (audit_trail BIGSERIAL) — safe: for audit_event rules the evaluator
-- stringifies subject_id into the subjects jsonb, never into asset_ids.
--
-- Guarded and idempotent: keyed on the immutable slug (house convention) AND
-- the seeded name AND query LIKE '%auth_audit_log%'. A customer-edited rule —
-- renamed or re-queried — no longer matches and is never blindly overwritten;
-- re-running after apply is a no-op because the LIKE fails on the new text.
-- Dual-written to src/database/schema-full.sql's alert_rules COPY block so
-- fresh installs seed the repointed query directly.
BEGIN;

UPDATE alert_rules
SET query = $q$SELECT a.id AS subject_id,
       (a.details ->> 'username') || ' → ' || (a.details ->> 'new_role') AS subject_label,
       'was ' || (a.details ->> 'old_role') || ' · ' || (a.details ->> 'ip_address') AS subject_sublabel
FROM audit_trail a
WHERE a."timestamp" > now() - interval '24 hours'
  AND a.category = 'auth'
  AND a.resource_type = 'authentication'
  AND a.action = 'success'
  AND a.details ->> 'event' = 'user_updated'
  AND a.details -> 'changes' ? 'role'
  AND a.details ->> 'new_role' IN ('admin','super_admin')
  AND COALESCE(a.details ->> 'old_role', '') NOT IN ('admin','super_admin')$q$,
    updated_at = now()
WHERE slug = 'auth-admin-role-granted'
  AND name = 'Auth — Admin/Super-Admin Role Granted'
  AND query LIKE '%auth_audit_log%';

COMMIT;
