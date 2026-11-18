-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Drop indexes nothing reads, on the tables every ingest and pipeline run
-- writes to most.
--
-- Measured on three production databases (pg_stat_user_indexes, 2026-11):
-- each index below had idx_scan = 0, or duplicates a sibling the planner
-- uses instead. No application query, alert rule, view or materialized view
-- filters on the columns they cover in the way these indexes serve.
--
--   raw_source_data
--     idx_raw_source_payload     GIN (raw_payload). Nothing filters on the
--                                payload — it is only read back by batch. The
--                                largest deployment carried 5.4 GB of it, and a
--                                GIN index is the costliest to maintain on
--                                every ingested row.
--     idx_raw_data_batch         duplicate of idx_raw_source_batch (batch_id)
--     idx_raw_data_integration   duplicate of idx_raw_source_integration
--                                (integration_id)
--
--   asset_providers_history
--     idx_providers_history_id, idx_providers_history_asset_id,
--     idx_providers_history_generation — never scanned. The table's only
--     reader filters on data_source (idx_providers_history_data_source, kept)
--     and retention selects by archived_at (idx_providers_history_archived,
--     kept); every pipeline run still paid to maintain all three on
--     ~170k archived rows.
--
-- The kept raw_source_data siblings come from the base schema, not from a
-- migration; they are (re)asserted first so no database built some other
-- way is left without a batch_id / integration_id index. On every known
-- database they already exist and IF NOT EXISTS makes this a no-op.
--
-- DROP INDEX is a catalog change with no table rewrite: it takes an
-- ACCESS EXCLUSIVE lock on each table only for the drop itself.

BEGIN;

CREATE INDEX IF NOT EXISTS idx_raw_source_batch ON raw_source_data (batch_id);
CREATE INDEX IF NOT EXISTS idx_raw_source_integration ON raw_source_data (integration_id);

DROP INDEX IF EXISTS idx_raw_source_payload;
DROP INDEX IF EXISTS idx_raw_data_batch;
DROP INDEX IF EXISTS idx_raw_data_integration;

DROP INDEX IF EXISTS idx_providers_history_id;
DROP INDEX IF EXISTS idx_providers_history_asset_id;
DROP INDEX IF EXISTS idx_providers_history_generation;

COMMIT;
