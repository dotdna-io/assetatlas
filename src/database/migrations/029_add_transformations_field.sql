-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Add transformations field to integrations table
-- Date: 2025-11-11
-- Description: Separates field transformations from field mappings to eliminate naming conflicts

-- Add transformations field
ALTER TABLE integrations
ADD COLUMN IF NOT EXISTS transformations JSONB DEFAULT '{}'::jsonb;

-- Add comment
COMMENT ON COLUMN integrations.transformations IS 'Field transformation definitions (separate from field mappings to avoid naming conflicts)';

-- Create index for transformations JSONB queries
CREATE INDEX IF NOT EXISTS idx_integrations_transformations
ON integrations USING GIN (transformations);

-- Migrate existing transformations from field_mapping to transformations column
-- This will extract any transformation objects (with type='transform') and move them
UPDATE integrations
SET transformations = (
    SELECT jsonb_object_agg(key, value)
    FROM jsonb_each(field_mapping)
    WHERE value->>'type' = 'transform'
),
field_mapping = (
    SELECT jsonb_object_agg(key, value)
    FROM jsonb_each(field_mapping)
    WHERE value->>'type' IS NULL OR value->>'type' != 'transform'
)
WHERE EXISTS (
    SELECT 1
    FROM jsonb_each(field_mapping)
    WHERE value->>'type' = 'transform'
);

-- Verification query
SELECT
    id,
    name,
    jsonb_pretty(transformations) as transformations_preview,
    jsonb_pretty(field_mapping) as field_mapping_preview
FROM integrations
WHERE transformations != '{}'::jsonb
LIMIT 5;
