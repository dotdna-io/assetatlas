-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: pg_trgm GIN index for Explorer keyword search
-- Date: 2026-06-17
-- Purpose: The Explorer free-text search (AssetRepository.list_assets) matched
--   every searchable field with `unnest(<col>) ILIKE '%term%'` across ~15
--   columns plus `extended_attributes::text ILIKE`. No index can serve that,
--   so every search ran two full sequential scans (COUNT + data). On large
--   deployments (e.g. tens of thousands of assets) search was unusably slow or timed out.
--
--   The query now matches ONE consolidated text expression. Index that exact
--   expression with a pg_trgm GIN index so `<expr> ILIKE '%term%'` (and the
--   matching COUNT) use the index instead of a sequential scan.
--
-- IMPORTANT: the indexed expression below MUST stay structurally identical to
--   _ASSET_SEARCH_EXPR in
--   src/modules/assets/repositories/asset_repository.py (same columns, order
--   and separators) or the planner will not use the index. It is also mirrored
--   in schema-full.sql for fresh installs.
--
--   array_to_string() is only marked STABLE (its anyarray signature is
--   conservative), so it cannot appear directly in an index expression. The
--   asset_search_join() wrapper below is genuinely IMMUTABLE for text[] and is
--   what makes the expression indexable.
--
-- Operational notes:
--   * Building the index scans the table once and takes a SHARE lock (writes
--     blocked, reads allowed) for the duration. Apply during a maintenance
--     window on large databases; it may take a while and consume disk.
--   * pg_trgm is a trusted extension (PostgreSQL 13+), so the application role
--     can create it. If CREATE EXTENSION is denied, have a superuser run
--     `CREATE EXTENSION pg_trgm;` once, then re-run this migration.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- IMMUTABLE wrapper around array_to_string(arr, ' ') so the search expression
-- can be indexed. Safe to mark IMMUTABLE: joining a text[] with a constant
-- separator is fully deterministic.
CREATE OR REPLACE FUNCTION asset_search_join(text[])
    RETURNS text
    LANGUAGE sql
    IMMUTABLE
    PARALLEL SAFE
    AS $$ SELECT array_to_string($1, ' ') $$;

CREATE INDEX IF NOT EXISTS idx_assets_search_trgm
    ON assets USING gin (
        (
            coalesce(asset_search_join(hostname), '') || ' ' ||
            coalesce(asset_search_join(ip), '') || ' ' ||
            coalesce(asset_search_join(mac), '') || ' ' ||
            coalesce(asset_search_join(sn), '') || ' ' ||
            coalesce(asset_search_join(fqdn), '') || ' ' ||
            coalesce(asset_search_join(manufacturer), '') || ' ' ||
            coalesce(asset_search_join(model), '') || ' ' ||
            coalesce(asset_search_join(data_source), '') || ' ' ||
            coalesce(asset_search_join(owner), '') || ' ' ||
            coalesce(asset_search_join(location), '') || ' ' ||
            coalesce(asset_search_join(user_field), '') || ' ' ||
            coalesce(category, '') || ' ' ||
            coalesce(subcategory, '') || ' ' ||
            coalesce(os_normalized, '') || ' ' ||
            coalesce(extended_attributes::text, '')
        ) gin_trgm_ops
    );

COMMIT;
