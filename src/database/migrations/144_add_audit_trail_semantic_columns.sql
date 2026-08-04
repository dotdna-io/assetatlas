-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 144: give `audit_trail` the semantic columns a handler can attach
-- to the row the audit middleware already writes (#1150).
--
-- audit_trail is the single system of record for audit events, but the row the
-- after_request middleware writes today is purely HTTP-shaped: method, path,
-- status, bodies. Anything semantic -- WHICH resource changed, WHAT was done to
-- it, WHY -- has to be reverse-engineered from the path and the (sanitized)
-- request body, when it is recoverable at all.
--
-- These five columns let a handler annotate the in-flight request
-- (src/api/utils/audit_context.annotate_audit) and have the middleware merge
-- the annotation into the same INSERT -- one row per event, never a second
-- write:
--
--   * category      -- coarse bucket for filtering (default 'admin').
--   * resource_type -- the kind of thing acted on ('integration', 'asset', ...).
--   * resource_id   -- identifier of the specific resource, when there is one.
--                      TEXT because ids arrive as uuids, slugs and ints alike.
--   * action        -- the verb ('create', 'update', 'delete', ...).
--   * details       -- structured context, run through the same _sanitize_body
--                      redaction as request_body and capped at the same 64 KB.
--
-- All five are nullable TEXT/jsonb with no backfill: rows written before this
-- migration -- and requests nothing annotates -- keep them NULL by design.
-- TEXT, not varchar(n): the middleware insert is fire-and-forget inside a
-- try/except, so a length limit here would silently drop the whole audit row
-- on overflow -- a data-loss bug, not hygiene.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS throughout.
-- Dual-written to schema-full.sql for fresh installs.
BEGIN;

ALTER TABLE audit_trail
  ADD COLUMN IF NOT EXISTS category TEXT,
  ADD COLUMN IF NOT EXISTS resource_type TEXT,
  ADD COLUMN IF NOT EXISTS resource_id TEXT,
  ADD COLUMN IF NOT EXISTS action TEXT,
  ADD COLUMN IF NOT EXISTS details jsonb;

COMMENT ON COLUMN audit_trail.category IS 'Coarse classification of the audited action, supplied by annotate_audit (default ''admin''). NULL for rows with no semantic annotation.';
COMMENT ON COLUMN audit_trail.resource_type IS 'Kind of resource the request acted on (e.g. integration, asset), supplied by annotate_audit. NULL when nothing annotated the request.';
COMMENT ON COLUMN audit_trail.resource_id IS 'Identifier of the specific resource acted on (uuid, slug, int-as-text), supplied by annotate_audit. NULL when not applicable or nothing annotated.';
COMMENT ON COLUMN audit_trail.action IS 'Verb describing what was done to the resource (e.g. create, update, delete), supplied by annotate_audit. NULL when nothing annotated the request.';
COMMENT ON COLUMN audit_trail.details IS 'Structured annotation context as JSONB, sanitized with the same recursive redaction as request_body and capped at 64 KB. NULL when the annotation carried no details.';

COMMIT;
