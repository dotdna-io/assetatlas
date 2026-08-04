-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 102: Allow multiple MFA methods per user (#608 follow-up).
--
-- The user_mfa table was singleton (PRIMARY KEY on user_id, one secret column).
-- This migration restructures it so a user can enroll multiple TOTP
-- authenticators (phone + tablet, primary + backup, etc.):
--
--   1. Add `id UUID` as the new primary key.
--   2. Add `label TEXT` so each enrollment has a user-visible name.
--   3. Add `type TEXT` with a CHECK constraint to gate forward-compatibility
--      with hardware tokens (only 'totp' is allowed today).
--   4. Move the PRIMARY KEY from user_id to id; existing rows get a fresh
--      gen_random_uuid() value via DEFAULT, preserving their data.
--   5. Add an index on user_id since it's now used for lookups but is no
--      longer a PK.
--
-- Recovery semantics from #614 (admin "Reset MFA") are unchanged: that
-- endpoint deletes every row for the user, regardless of count.
--
-- Idempotent. Safe to re-run.

BEGIN;

-- 1. New columns. ADD COLUMN IF NOT EXISTS keeps the migration idempotent;
-- DEFAULT clauses ensure existing rows get sane values without a separate
-- backfill pass.
ALTER TABLE user_mfa
    ADD COLUMN IF NOT EXISTS id UUID NOT NULL DEFAULT gen_random_uuid();

ALTER TABLE user_mfa
    ADD COLUMN IF NOT EXISTS label TEXT NOT NULL DEFAULT 'Authenticator app';

ALTER TABLE user_mfa
    ADD COLUMN IF NOT EXISTS type TEXT NOT NULL DEFAULT 'totp';

-- 2. Move the PRIMARY KEY. Only swap it if it's still on user_id — re-runs
-- after a successful first run see the PK already on id and do nothing.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_attribute a
            ON a.attnum = ANY(c.conkey)
           AND a.attrelid = c.conrelid
        WHERE c.conname = 'user_mfa_pkey'
          AND c.contype = 'p'
          AND a.attname = 'user_id'
    ) THEN
        ALTER TABLE user_mfa DROP CONSTRAINT user_mfa_pkey;
        ALTER TABLE user_mfa ADD CONSTRAINT user_mfa_pkey PRIMARY KEY (id);
    END IF;
END $$;

-- 3. Index on user_id for the per-user listing queries.
CREATE INDEX IF NOT EXISTS idx_user_mfa_user_id ON user_mfa(user_id);

-- 4. CHECK constraint on type. Today we only accept 'totp'; adding a new
-- method type later means relaxing this check in another migration.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'user_mfa_type_check' AND conrelid = 'user_mfa'::regclass
    ) THEN
        ALTER TABLE user_mfa
            ADD CONSTRAINT user_mfa_type_check CHECK (type IN ('totp'));
    END IF;
END $$;

COMMENT ON COLUMN user_mfa.id IS
    'Per-method primary key. Stable across the lifetime of the enrollment.';
COMMENT ON COLUMN user_mfa.label IS
    'User-supplied name for the enrolled method (e.g. "Phone", "Tablet").';
COMMENT ON COLUMN user_mfa.type IS
    'Method kind. Only "totp" is supported today; reserved for future hardware tokens.';

-- Verification: fail loudly if any structural piece is missing after the migration.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'user_mfa'
          AND column_name IN ('id', 'label', 'type')
        GROUP BY table_name HAVING COUNT(*) = 3
    ) THEN
        RAISE EXCEPTION 'user_mfa is missing one of id/label/type after migration';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_attribute a
            ON a.attnum = ANY(c.conkey)
           AND a.attrelid = c.conrelid
        WHERE c.conname = 'user_mfa_pkey'
          AND c.contype = 'p'
          AND a.attname = 'id'
    ) THEN
        RAISE EXCEPTION 'user_mfa_pkey was not moved to the id column';
    END IF;
END $$;

COMMIT;
