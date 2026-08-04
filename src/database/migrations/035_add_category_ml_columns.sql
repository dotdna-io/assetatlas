-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 035: Add columns for ML prediction preservation
-- Preserves the original predicted category when a manual override is applied.
ALTER TABLE assets ADD COLUMN IF NOT EXISTS category_predicted TEXT;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS category_is_overridden BOOLEAN DEFAULT false;
COMMENT ON COLUMN assets.category_predicted IS 'Original predicted category before any manual override';
COMMENT ON COLUMN assets.category_is_overridden IS 'Whether category was changed by a manual override';
