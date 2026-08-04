-- SPDX-License-Identifier: AGPL-3.0-or-later
-- src/database/migrations/074_add_chat_tables.sql
BEGIN;

CREATE TABLE IF NOT EXISTS chat_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    model           TEXT NOT NULL,
    provider        TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

DROP TRIGGER IF EXISTS chat_sessions_updated_at ON chat_sessions;
CREATE TRIGGER chat_sessions_updated_at
    BEFORE UPDATE ON chat_sessions
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_id
    ON chat_sessions (user_id, updated_at DESC)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS chat_messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('user','assistant','tool','system')),
    content         TEXT,
    tool_calls      JSONB,
    tool_result_ref UUID,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (session_id, seq)
);

CREATE TABLE IF NOT EXISTS chat_tool_calls (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id      UUID NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    tool_name       TEXT NOT NULL,
    arguments       JSONB NOT NULL,
    result          TEXT,
    result_bytes    INTEGER,
    duration_ms     INTEGER,
    status          TEXT NOT NULL CHECK (status IN ('ok','error','denied','timeout')),
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_tool_calls_message ON chat_tool_calls (message_id);
CREATE INDEX IF NOT EXISTS idx_chat_tool_calls_name    ON chat_tool_calls (tool_name, created_at DESC);

CREATE TABLE IF NOT EXISTS chat_feedback (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id      UUID NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rating          SMALLINT NOT NULL CHECK (rating IN (-1, 1)),
    comment         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (message_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_chat_feedback_user_id ON chat_feedback (user_id);

COMMIT;
