-- SPDX-License-Identifier: AGPL-3.0-or-later
-- src/database/migrations/131_create_merge_denylist.sql
-- Identifier denylist: identifiers an admin has marked as untrusted for asset
-- identity. The asset-generation pipeline reads this every run and refuses to use
-- a listed identifier to merge records — both when building identity from fresh
-- data (Stage 2 asset field) and when re-loading historical mappings (Stage 3).
-- Keyed on the identifier (stable across runs) in the pipeline's canonical
-- component form: 'sn:<lower>' for serials, lowercase colon-form for MACs, bare
-- lowercase for hostnames/fqdns. This is the durable fix for pipeline over-merges
-- (reused hostname, shared/virtual MAC) that deleting mappings cannot achieve.
BEGIN;

CREATE TABLE IF NOT EXISTS merge_denylist (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    identifier_type  text NOT NULL,
    identifier_value text NOT NULL,   -- canonical component form, e.g. 'sn:abc123', 'bc:24:11:22:33:44', 'web01'
    raw_value        text NOT NULL,   -- what the admin typed, for display
    reason           text,
    created_by       text NOT NULL,
    enabled          boolean NOT NULL DEFAULT true,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT merge_denylist_type_check
        CHECK (identifier_type IN ('hostname', 'fqdn', 'mac', 'serial')),
    CONSTRAINT merge_denylist_unique UNIQUE (identifier_type, identifier_value)
);

CREATE INDEX IF NOT EXISTS idx_merge_denylist_enabled
    ON merge_denylist (enabled) WHERE enabled;

COMMIT;
