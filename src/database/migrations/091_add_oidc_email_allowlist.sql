-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 091: OIDC email-domain allowlist + safer auto_create_users default
--
-- Adds the new oidc.allowed_email_domains setting (comma-separated list,
-- default empty = all domains allowed) and ensures oidc.auto_create_users
-- exists on installs that pre-date migration 089.
--
-- IMPORTANT: this migration is intentionally non-destructive. ON CONFLICT
-- DO NOTHING preserves any value an operator (or migration 089) already set
-- for oidc.auto_create_users. The new safer default ('false') is applied
-- ONLY on fresh installs, where schema-full.sql seeds the row before the
-- migrations run (see src/database/schema-full.sql).
--
-- Idempotent. Safe to re-run.

BEGIN;

-- New setting: comma-separated allowlist of email domains permitted to
-- auto-provision a local account via OIDC. Empty string = no restriction.
INSERT INTO system_settings (category, key, value, value_type, default_value, description, validation_rule, is_sensitive, requires_restart)
VALUES
    ('oidc', 'allowed_email_domains', '', 'string', '',
     'Comma-separated list of email domains permitted to auto-provision a local account via OIDC (e.g., "example.com,corp.io"). Empty = all domains allowed. Only consulted when oidc.auto_create_users = true.',
     NULL, FALSE, FALSE)
ON CONFLICT (category, key) DO NOTHING;

-- Defensive re-seed of auto_create_users for any install that somehow
-- skipped migration 089. The default value here matches the new safer
-- default in schema-full.sql; existing rows are preserved.
INSERT INTO system_settings (category, key, value, value_type, default_value, description, validation_rule, is_sensitive, requires_restart)
VALUES
    ('oidc', 'auto_create_users', 'false', 'boolean', 'false',
     'Provision local user records on first successful SSO sign-in. Disable to require admins to pre-create users by email.',
     NULL, FALSE, FALSE)
ON CONFLICT (category, key) DO NOTHING;

SELECT COUNT(*) AS oidc_settings_count
FROM system_settings
WHERE category = 'oidc';

COMMIT;
