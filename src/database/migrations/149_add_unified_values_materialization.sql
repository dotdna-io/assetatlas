-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 149: materialized storage for Unified Field values.
--
-- A `virtual:<slug>` field is today resolved to its full merge expression by
-- FieldResolver._emit_sql() on every filter, sort, chart, tag rule, posture
-- rule and Explorer typeahead — on a real deployment a 1,700-character
-- COALESCE over 31 extended_attributes JSONB extractions, re-evaluated per row
-- per occurrence. Every unified function is pure and row-local, and the
-- pipeline rebuilds `assets` from scratch on every run, so the value can be
-- computed once per row during the rebuild instead. This migration adds the
-- storage; refresh_unified_values() (src/modules/fields/unified/materialize.py)
-- is the only writer, called by the two materialize pipeline stages. The read
-- flip (the resolver emitting the materialized reference for fields whose
-- stamp is set) is a separate change — until it lands, these columns change no
-- query result anywhere.
--
-- assets.unified_values is deliberately NOT added to bulk_upsert_assets'
-- column list: the rebuild (TRUNCATE + plain INSERT) resets it to NULL and the
-- materialize stage refills it inside the same transaction, so the column
-- never carries stale values past a run.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS throughout; COMMENT ON COLUMN is
-- re-runnable. Dual-written to schema-full.sql for fresh installs.
BEGIN;

ALTER TABLE assets
  ADD COLUMN IF NOT EXISTS unified_values jsonb;

COMMENT ON COLUMN assets.unified_values IS 'Materialized Unified Field values keyed by slug. Written only by refresh_unified_values (the pipeline materialize stages); deliberately absent from bulk_upsert_assets, so the TRUNCATE+INSERT rebuild leaves it NULL until the materialize stage runs.';

ALTER TABLE assets_history
  ADD COLUMN IF NOT EXISTS unified_values jsonb;

COMMENT ON COLUMN assets_history.unified_values IS 'Historical snapshot of assets.unified_values at archive time.';

ALTER TABLE unified_fields
  ADD COLUMN IF NOT EXISTS materialized_at timestamptz;

COMMENT ON COLUMN unified_fields.materialized_at IS 'Stamped by refresh_unified_values after a successful full-table refresh of this field; NULL means the field has no usable materialized values. Definition changes clear it.';

COMMIT;
