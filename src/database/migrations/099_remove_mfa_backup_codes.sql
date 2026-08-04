-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 099: Remove the user_mfa.backup_codes column.
--
-- Backup codes are removed entirely (issue #609). Recovery for users who lose
-- TOTP access is covered by:
--   - #613 — forgot-password flow with TOTP self-unlock
--   - #614 — admin "Reset MFA" action
--
-- Idempotent. Safe to re-run.

BEGIN;

ALTER TABLE user_mfa DROP COLUMN IF EXISTS backup_codes;

-- Verification: fail loudly if the column somehow still exists.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'user_mfa'
          AND column_name = 'backup_codes'
    ) THEN
        RAISE EXCEPTION 'user_mfa.backup_codes was not dropped';
    END IF;
END $$;

COMMIT;
