-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 143: give `assets` a subcategory half for the pipeline's predicted
-- snapshot, and backfill both halves where a correct one can be inferred.
--
-- Clearing a manual category override is supposed to put the asset back to what
-- the pipeline predicted -- the bulk dialog's own button says "Reset {n} to
-- predicted". It did the opposite, blanking BOTH fields on every asset, for two
-- separate reasons (#1154):
--
--   * There was no subcategory_predicted column at all, so
--     ReclassifyService._reset_assets_bulk had nothing to restore from and wrote
--     `subcategory = NULL` outright.
--   * category_predicted has existed since migration 035 and was never once
--     written. CategoryOverrideRepository.apply_overrides built it as a pandas
--     column, but PostgreSQLAdapter.bulk_upsert_assets omitted it from its INSERT
--     column list, so it was dropped at persist time -- 73,748 of 73,748 rows on
--     the shared dev database had it NULL. `SET category = category_predicted`
--     therefore wrote NULL for every asset. The pipeline flag
--     enable_category_overrides is a red herring here: the value never reached
--     the database whether it was on or off.
--
-- The code half of the fix persists both columns from every run. This migration
-- creates the column that half needs and seeds what history allows.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS, and both backfills are guarded on the
-- target still being NULL. Dual-written to schema-full.sql for fresh installs.
BEGIN;

ALTER TABLE assets
  ADD COLUMN IF NOT EXISTS subcategory_predicted TEXT;

COMMENT ON COLUMN assets.subcategory_predicted IS 'Original predicted subcategory before any manual override';

-- Backfill only the rows where the live value IS the prediction.
--
-- For a never-overridden asset, category/subcategory are exactly what the
-- pipeline produced, so copying them is a faithful snapshot -- and it makes the
-- first "reset to predicted" after this deploy behave correctly instead of
-- waiting for the next pipeline run to populate the columns.
--
-- Overridden rows are deliberately left NULL. Their live value is the override,
-- and the prediction is not recoverable from anywhere: category_overrides
-- .features_snapshot holds only ML *input* features (manufacturer, model,
-- os_normalized, mac_vendor, vendor_category), and assets_history carries no
-- predicted columns. Seeding those rows from `category` would freeze the
-- override in as the "prediction" and make a later restore a no-op that looks
-- like a bug -- strictly worse than admitting the value is unknown. The
-- COALESCE in _reset_assets_bulk degrades them to "leave the value alone", and
-- the next pipeline run writes the real prediction over the NULL.
--
-- "Overridden" is decided by category_overrides, NOT by assets
-- .category_is_overridden. That flag is not in bulk_upsert_assets' column list,
-- and the pipeline rebuilds `assets` with TRUNCATE + a plain INSERT, so every
-- run resets it to its DEFAULT false -- on any deployment that has run the
-- pipeline since its last bulk reclassify (i.e. all of them) the flag is false
-- for every row and would let this backfill seed exactly the overridden rows it
-- is meant to skip. category_overrides survives the TRUNCATE (no FK, PK on
-- asset_id) and is the durable record of which assets carry an override.
--
-- COALESCE on the flag anyway because 035 added category_is_overridden nullable
-- with a DEFAULT false: rows written before it existed carry NULL, and a bare
-- `NOT category_is_overridden` evaluates to NULL there, which Postgres treats as
-- not-matched -- silently excluding exactly the oldest rows this backfill is for.
--
-- One statement, not two: both columns are guarded independently by their own
-- COALESCE, so a single pass halves the row rewrites on a table this size.
UPDATE assets a
   SET category_predicted = COALESCE(a.category_predicted, a.category),
       subcategory_predicted = COALESCE(a.subcategory_predicted, a.subcategory)
 WHERE (a.category_predicted IS NULL OR a.subcategory_predicted IS NULL)
   AND NOT COALESCE(a.category_is_overridden, false)
   AND NOT EXISTS (
       SELECT 1 FROM category_overrides co
        WHERE co.asset_id = a.id
          AND co.active
   );

COMMIT;
