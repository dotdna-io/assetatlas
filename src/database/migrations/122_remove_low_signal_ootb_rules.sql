-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 122: remove four low-signal system-seeded OOTB alert rules (#877).
-- Alerts referencing these rule_ids are removed by the alerts.rule_id FK
-- (ON DELETE CASCADE, migration 037). auth-admin-role-granted is intentionally
-- retained (the only critical privilege-escalation signal). DELETE is idempotent;
-- migrations 118/120 that seeded these rows are immutable and left untouched.
BEGIN;

DELETE FROM alert_rules
 WHERE source = 'system'
   AND slug IN ('auth-user-created',
                'auth-user-deleted',
                'auth-admin-login-off-hours',
                'anomaly-presence-flap');

COMMIT;
