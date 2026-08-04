-- SPDX-License-Identifier: AGPL-3.0-or-later
-- 124_add_preserve_fields_to_integrations.sql
-- Add preserve_fields JSONB column to integrations table.
-- Authoritative source-of-truth for which source columns are marked
-- "Data Source Specific" (DSS / preserve) in the integration wizard (#809).
--
-- DSS fields remain encoded in field_mapping as {field: field} self-mappings
-- (the asset-generation pipeline reads field_mapping and is unchanged);
-- preserve_fields lets the wizard's edit-mode load classify a DSS field as
-- "Data Source Specific" instead of a framework mapping, even when the field
-- is named exactly like a canonical framework field (os, user, owner, ...).
--
-- No backfill: legacy rows default to [] and the wizard falls back to the
-- existing framework-name heuristic for them.
--
-- Schema: array of source field names
-- ["os", "custom_tag"]

BEGIN;

ALTER TABLE integrations
    ADD COLUMN IF NOT EXISTS preserve_fields JSONB DEFAULT '[]'::jsonb;

COMMENT ON COLUMN integrations.preserve_fields IS 'Array of source field names marked Data Source Specific (DSS/preserve) in the wizard; authoritative for DSS-vs-mapping classification. DSS fields also stay as {field:field} self-mappings in field_mapping (#809).';

COMMIT;
