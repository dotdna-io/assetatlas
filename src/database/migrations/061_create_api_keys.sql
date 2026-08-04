-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 061: Create api_keys table for external API authentication
BEGIN;

CREATE TABLE IF NOT EXISTS api_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            VARCHAR(100) NOT NULL,
    key_prefix      VARCHAR(8) NOT NULL,
    key_hash        VARCHAR(128) NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    last_used_at    TIMESTAMPTZ,
    last_used_ip    INET,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Unique index on hash for fast lookup during authentication
CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys (key_hash);

-- Index for listing keys by user
CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys (user_id);

-- Composite index for cleanup queries (find expired/inactive keys)
CREATE INDEX IF NOT EXISTS idx_api_keys_active_expires ON api_keys (is_active, expires_at);

COMMENT ON TABLE api_keys IS 'API keys for external system authentication against /api/v1/ endpoints';
COMMENT ON COLUMN api_keys.key_prefix IS 'First 8 chars of the key (e.g. aa_a1b2c) for UI identification';
COMMENT ON COLUMN api_keys.key_hash IS 'SHA-256 hash of the full API key — plaintext is never stored';

COMMIT;
