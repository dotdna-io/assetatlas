-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Rename asset_id_mapping to asset_id_mappings (plural)
-- Date: 2025-10-26
-- Description: Make table name consistent with code expectations

BEGIN;

-- Rename table to plural form
ALTER TABLE asset_id_mapping RENAME TO asset_id_mappings;

-- Update any indexes, constraints, or sequences if needed
-- (PostgreSQL automatically renames associated objects)

COMMIT;

-- Add comment
COMMENT ON TABLE asset_id_mappings IS 'Maps asset components (hostname, ip, mac, sn) to asset IDs for deduplication';
