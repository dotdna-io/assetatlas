-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 086: Seed auth settings category
-- Adds the global authentication/identity settings used by the auth overhaul
-- (epic #491). These are read by the auth blueprint and (in later PRs) the
-- MFA challenge and SSO providers. Idempotent via ON CONFLICT DO NOTHING.
-- Follows the seeding pattern of migrations 042, 052, 053, 082.

BEGIN;

INSERT INTO system_settings (category, key, value, value_type, default_value, description, validation_rule, is_sensitive, requires_restart)
VALUES
    ('auth', 'password_reset_rate_limit_per_ip_per_hour', '5', 'integer', '5',
     'Maximum number of password-reset request submissions accepted per source IP per rolling hour. Protects against email enumeration and spam.',
     '{"min": 1, "max": 1000}', FALSE, FALSE),

    ('auth', 'require_mfa_for_admins', 'false', 'boolean', 'false',
     'When true, admin and super_admin accounts must complete an MFA challenge to log in. Has no effect until MFA is configured.',
     NULL, FALSE, FALSE),

    ('auth', 'invite_token_ttl_hours', '168', 'integer', '168',
     'Lifetime of admin-issued user-onboarding invite tokens, in hours. Default 168 = 7 days.',
     '{"min": 1, "max": 720}', FALSE, FALSE),

    ('auth', 'reset_token_ttl_hours', '1', 'integer', '1',
     'Lifetime of self-service password-reset tokens, in hours.',
     '{"min": 1, "max": 24}', FALSE, FALSE)

ON CONFLICT (category, key) DO NOTHING;

-- Verification
SELECT COUNT(*) AS auth_settings_count
FROM system_settings
WHERE category = 'auth';

COMMIT;
