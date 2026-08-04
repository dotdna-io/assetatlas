-- SPDX-License-Identifier: AGPL-3.0-or-later
-- src/database/migrations/076_chat_messages_created_at_index.sql
--
-- Admin usage + audit queries (Phase 12) filter chat_messages by
-- created_at without a user_id or session_id predicate. The existing
-- idx_chat_messages_session (session_id, seq) can't serve these scans,
-- so we add a plain B-tree on created_at.

BEGIN;

CREATE INDEX IF NOT EXISTS idx_chat_messages_created_at
    ON chat_messages (created_at);

COMMIT;
