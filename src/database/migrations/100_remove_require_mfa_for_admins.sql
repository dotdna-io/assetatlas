-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 100: Remove the auth.require_mfa_for_admins setting row.
--
-- Issue #610 makes MFA enforcement unconditional for every local user.
-- The boolean setting is no longer read by the application; deleting
-- the row keeps the system_settings table consistent with code.
--
-- Idempotent. Safe to re-run.

BEGIN;

DELETE FROM system_settings
WHERE category = 'auth' AND key = 'require_mfa_for_admins';

-- Verification: fail loudly if the row somehow still exists.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM system_settings
        WHERE category = 'auth' AND key = 'require_mfa_for_admins'
    ) THEN
        RAISE EXCEPTION 'auth.require_mfa_for_admins setting was not removed';
    END IF;
END $$;

COMMIT;
