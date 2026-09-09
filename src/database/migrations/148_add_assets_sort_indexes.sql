-- SPDX-License-Identifier: AGPL-3.0-or-later
-- #1408: composite sort indexes matching the asset list's ORDER BY shapes.
--
-- AssetRepository._build_order_by emits `ORDER BY <col> DESC NULLS LAST,
-- id ASC` for every default sort the asset list serves: last_time (bare
-- /api/v1/assets, CSV export, chat/MCP list_assets), confidence (Explorer
-- default) and last_seen (Data Source Details default). DESC NULLS LAST is
-- the inverse of PostgreSQL's btree default in both directions (the default
-- for DESC is NULLS FIRST), and no single-column index can supply the id
-- tiebreaker, so every page load planned as a Seq Scan + top-N heapsort.
-- NULLS LAST is load-bearing: thousands of rows have NULL timestamps and
-- must sort to the bottom — the ORDER BY strings are deliberately unchanged.
--
-- Each old single-column index is a strict leading-column prefix of its
-- replacement (equality/range filters on the column keep using the
-- composite), so the old indexes are dropped here. DROP INDEX IF EXISTS
-- covers both historical variants of idx_assets_last_time: fresh installs
-- carried (last_time DESC) from schema-full.sql while migration 048 created
-- (last_time) on migrated databases.
--
-- Plain CREATE INDEX takes a SHARE lock on assets: reads continue, writes
-- block for the duration of the build (seconds at ~77k rows). No
-- CONCURRENTLY — the migration runner requires BEGIN/COMMIT.

BEGIN;

CREATE INDEX IF NOT EXISTS idx_assets_last_time_id
    ON assets (last_time DESC NULLS LAST, id ASC);

CREATE INDEX IF NOT EXISTS idx_assets_confidence_id
    ON assets (confidence DESC NULLS LAST, id ASC);

CREATE INDEX IF NOT EXISTS idx_assets_last_seen_id
    ON assets (last_seen DESC NULLS LAST, id ASC);

DROP INDEX IF EXISTS idx_assets_last_time;
DROP INDEX IF EXISTS idx_assets_last_seen;
DROP INDEX IF EXISTS idx_assets_confidence;

COMMIT;
