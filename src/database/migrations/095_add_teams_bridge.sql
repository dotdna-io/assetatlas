-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 095: Microsoft Teams bot bridge tables
--
-- Adds two tables backing the Teams bridge:
--   - teams_account_links: maps Entra (aad_object_id, tenant_id) → AssetAtlas user
--   - teams_conversation_sessions: maps Teams 1:1 conversation_id → chat_sessions.id
--
-- Both reference users.id with ON DELETE CASCADE so user deletion cleans up.
-- teams_conversation_sessions also references chat_sessions.id so deleting
-- a session removes its Teams binding.
--
-- Idempotent. Safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS teams_account_links (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aad_object_id   TEXT NOT NULL,
    tenant_id       TEXT NOT NULL,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    linked_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    linked_via_ip   INET
);

CREATE UNIQUE INDEX IF NOT EXISTS teams_account_links_aad_uniq
    ON teams_account_links (aad_object_id, tenant_id);
CREATE INDEX IF NOT EXISTS teams_account_links_user_idx
    ON teams_account_links (user_id);

CREATE TABLE IF NOT EXISTS teams_conversation_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id TEXT NOT NULL UNIQUE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id      UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS teams_conversation_sessions_user_idx
    ON teams_conversation_sessions (user_id);

CREATE TABLE IF NOT EXISTS teams_link_used_jtis (
    jti         TEXT PRIMARY KEY,
    consumed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Tokens expire in 5 minutes, so a JTI older than that can never replay
-- a real token. There is no scheduled cleanup in v1; operators trim with:
--   DELETE FROM teams_link_used_jtis
--    WHERE consumed_at < now() - interval '24 hours';
-- The consumed_at index supports that scan.
CREATE INDEX IF NOT EXISTS teams_link_used_jtis_consumed_idx
    ON teams_link_used_jtis (consumed_at);

SELECT 'teams_account_links' AS table_name, COUNT(*) AS row_count
FROM teams_account_links
UNION ALL
SELECT 'teams_conversation_sessions', COUNT(*)
FROM teams_conversation_sessions
UNION ALL
SELECT 'teams_link_used_jtis', COUNT(*)
FROM teams_link_used_jtis;

COMMIT;
