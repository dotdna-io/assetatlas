-- SPDX-License-Identifier: AGPL-3.0-or-later
-- src/database/migrations/075_chat_sessions_updated_at_index.sql
--
-- Retention ("DELETE FROM chat_sessions WHERE updated_at < ...") runs without
-- a user_id predicate and therefore can't use the existing partial index
-- idx_chat_sessions_user_id (user_id, updated_at DESC) WHERE deleted_at IS NULL.
-- Without a leading updated_at index the cleanup degrades to a full scan once
-- the table grows. Add a plain B-tree on updated_at.

BEGIN;

CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated_at
    ON chat_sessions (updated_at);

COMMIT;
