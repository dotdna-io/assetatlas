-- SPDX-License-Identifier: AGPL-3.0-or-later
-- src/database/migrations/114_create_unified_fields.sql
-- Unified Fields: user-defined virtual fields derived from existing catalog
-- fields via a merge function (coalesce/union/max/min). Resolved at query time;
-- definitions only — no raw SQL is stored.
BEGIN;

CREATE TABLE IF NOT EXISTS unified_fields (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug       TEXT NOT NULL,
    name       TEXT NOT NULL,
    function   TEXT NOT NULL CHECK (function IN ('coalesce','union','max','min')),
    sources    JSONB NOT NULL,
    data_type  TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS unified_fields_updated_at ON unified_fields;
CREATE TRIGGER unified_fields_updated_at
    BEFORE UPDATE ON unified_fields
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

CREATE UNIQUE INDEX IF NOT EXISTS uq_unified_fields_slug
    ON unified_fields (lower(slug));
CREATE UNIQUE INDEX IF NOT EXISTS uq_unified_fields_name
    ON unified_fields (lower(name));

-- Safe try-cast helpers for JSONB-text number/date sources under coalesce/max/min.
-- numeric parsing is locale-independent -> IMMUTABLE.
CREATE OR REPLACE FUNCTION aa_try_numeric(t text) RETURNS numeric AS $$
BEGIN RETURN t::numeric; EXCEPTION WHEN others THEN RETURN NULL; END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- text::timestamptz depends on session TimeZone/DateStyle GUCs -> STABLE
-- (we never index on it, so STABLE is correct and sufficient).
CREATE OR REPLACE FUNCTION aa_try_ts(t text) RETURNS timestamptz AS $$
BEGIN RETURN t::timestamptz; EXCEPTION WHEN others THEN RETURN NULL; END;
$$ LANGUAGE plpgsql STABLE;

COMMIT;
