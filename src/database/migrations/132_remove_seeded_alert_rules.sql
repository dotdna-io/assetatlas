-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 132: remove ten system-seeded OOTB alert rules on request.
-- Drops the auth/audit interval rules (failed-login bursts, refresh-token reuse,
-- MFA disabled, security-settings changed, privileged-account created), the two
-- OS end-of-life rules, the update-available rule, and the grace-expiry preview.
--
-- Alerts referencing these rule_ids are removed by the alerts.rule_id FK
-- (ON DELETE CASCADE, migration 037), which in turn cascades alert_events and
-- alert_deliveries. DELETE is idempotent; the migrations that seeded these rows
-- (119/120/125) are immutable and left untouched.
--
-- assetatlas-update-available: UpdateCheckService.tick() no-ops when the rule is
-- absent (get_rule_by_slug -> None), so removing it cleanly disables the update
-- alert; the About-tab update badge uses a separate endpoint and is unaffected.
-- auth-admin-role-granted is intentionally RETAINED (privilege-escalation signal).
BEGIN;

DELETE FROM alert_rules
 WHERE source = 'system'
   AND slug IN ('auth-privileged-account-created',
                'auth-refresh-token-reuse',
                'auth-failed-login-burst-ip',
                'auth-failed-login-burst-user',
                'auth-settings-changed',
                'auth-mfa-disabled',
                'ootb-os-eol-transition',
                'ootb-os-eol-forecast-60d',
                'assetatlas-update-available',
                'ootb-grace-expiry-preview');

COMMIT;
