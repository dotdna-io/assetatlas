-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 103: Allow analysts to add custom subcategories from the
-- Reclassify dialog. Catalog-curated subcategories continue to live in
-- data/categories.json (read at process start). User-added entries are
-- stored here and merged into the dropdown by ReclassifyService.
--
-- Idempotent: safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS custom_subcategories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category    TEXT NOT NULL,
    name        TEXT NOT NULL,
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Case-insensitive uniqueness within a category. The dedup check at the
-- service layer also strips against catalog entries so the catalog row
-- always wins.
CREATE UNIQUE INDEX IF NOT EXISTS custom_subcategories_category_name_lower_idx
    ON custom_subcategories (category, lower(name));

CREATE INDEX IF NOT EXISTS custom_subcategories_category_idx
    ON custom_subcategories (category);

COMMIT;
