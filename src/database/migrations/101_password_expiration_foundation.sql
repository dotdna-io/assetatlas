-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 101: Password expiration foundation (#611).
--
-- Adds two pieces:
--   1. users.password_changed_at — TIMESTAMPTZ tracking when the bcrypt
--      hash was last updated. Backfills existing rows to NOW() so every
--      user starts a fresh window (the alternative — backfill to created_at
--      or updated_at — would force an immediate forced-change for users
--      whose password is already old, which is unfair without prior notice).
--   2. system_settings row auth.password_expiration_days — integer days
--      (0 = disabled, default 180). Read by login flow to set
--      must_change_password=true when the local user's password exceeds
--      the window. NIST 800-63B explicitly recommends NOT mandating
--      periodic rotation; 0 honors that.
--
-- Idempotent. Safe to re-run.

BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

INSERT INTO system_settings (
    category, key, value, value_type, default_value,
    description, validation_rule, is_sensitive, requires_restart
) VALUES (
    'auth', 'password_expiration_days', '180', 'integer', '180',
    'Number of days a local-account password remains valid before the user is required to change it on next login. 0 = never expires (recommended per NIST 800-63B). Has no effect on LDAP or OIDC users.',
    '{"min": 0, "max": 3650}', FALSE, FALSE
)
ON CONFLICT (category, key) DO NOTHING;

-- Verification: fail loudly if either piece didn't land.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'users'
          AND column_name = 'password_changed_at'
    ) THEN
        RAISE EXCEPTION 'users.password_changed_at column was not created';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM system_settings
        WHERE category = 'auth' AND key = 'password_expiration_days'
    ) THEN
        RAISE EXCEPTION 'auth.password_expiration_days setting was not seeded';
    END IF;
END $$;

COMMIT;
