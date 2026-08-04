-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 085: Add token_type to password_reset_tokens
-- Adds a token_type discriminator so the same table can back both
-- 'reset' (existing flow) and 'invite' (PR 3 onboarding flow).
-- Idempotent: uses IF NOT EXISTS for column/index, pg_constraint guard for the CHECK.
-- Backfills existing rows to 'reset' via the column DEFAULT.

BEGIN;

ALTER TABLE password_reset_tokens
    ADD COLUMN IF NOT EXISTS token_type TEXT NOT NULL DEFAULT 'reset';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'password_reset_tokens_token_type_check'
    ) THEN
        ALTER TABLE password_reset_tokens
            ADD CONSTRAINT password_reset_tokens_token_type_check
            CHECK (token_type IN ('reset', 'invite'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_user_id_type
    ON password_reset_tokens(user_id, token_type);

COMMENT ON COLUMN password_reset_tokens.token_type IS
'Discriminator for the token flow: ''reset'' (self-service password reset, 1h TTL) or ''invite'' (admin-issued user onboarding, 7d TTL).';

-- Verification
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'password_reset_tokens'
  AND column_name = 'token_type';

COMMIT;
