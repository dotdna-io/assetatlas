-- SPDX-License-Identifier: AGPL-3.0-or-later
-- #455: wipe legacy JSONB on FieldId cutover.
-- Spec: docs/superpowers/specs/2026-04-20-field-id-restructure-design.md
-- Order: history/audit tables first (FK referrers), then owning tables.

BEGIN;

TRUNCATE TABLE posture_sub_rule_history, posture_sub_rules CASCADE;
TRUNCATE TABLE reports_audit_log, reports CASCADE;
TRUNCATE TABLE system_dashboard_preferences CASCADE;
TRUNCATE TABLE tag_rules CASCADE;

COMMIT;
