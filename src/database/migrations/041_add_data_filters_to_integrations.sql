-- SPDX-License-Identifier: AGPL-3.0-or-later
-- 041_add_data_filters_to_integrations.sql
-- Add data_filters JSONB column to integrations table.
-- Stores filter rules applied during data ingestion to exclude
-- source records that don't match criteria (e.g., accountEnabled == true).
--
-- Schema: array of filter objects
-- [{"field": "accountEnabled", "operator": "equals", "value": true}]

BEGIN;

ALTER TABLE integrations
    ADD COLUMN IF NOT EXISTS data_filters JSONB DEFAULT '[]'::jsonb;

COMMENT ON COLUMN integrations.data_filters IS 'Array of filter rules applied during ingestion. Each rule: {field, operator, value}';

COMMIT;
