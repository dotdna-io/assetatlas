-- SPDX-License-Identifier: AGPL-3.0-or-later
-- src/database/migrations/077_add_chat_tool_call_artifact.sql
--
-- Adds an optional structured payload to chat_tool_calls so the chat
-- blueprint can persist chart/table/etc. artifacts produced by a tool
-- alongside the LLM-facing text result. The frontend renders these
-- inline below the assistant's message (v1: donut chart for
-- aggregate_assets). Shape is a generic envelope:
--
--     { "type": "donut", "title": "...", "series": [...], "meta": {...} }
--
-- Discriminated by ``type`` so future chart kinds (bar, line,
-- stacked_bar) drop in without a schema change.
--
-- Idempotent: safe to re-apply.

BEGIN;

ALTER TABLE chat_tool_calls
    ADD COLUMN IF NOT EXISTS artifact JSONB;

COMMIT;
