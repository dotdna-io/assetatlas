-- SPDX-License-Identifier: AGPL-3.0-or-later
BEGIN;

-- Formalize field_registry in the migration system.
-- Table already exists in production (created by schema-full.sql).
-- This migration is idempotent - safe to run on existing databases.

CREATE TABLE IF NOT EXISTS field_registry (
    id SERIAL PRIMARY KEY,
    field_name VARCHAR(100) UNIQUE NOT NULL,
    field_type VARCHAR(20) NOT NULL CHECK (field_type IN ('static', 'extended')),
    db_path VARCHAR(200) NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    description TEXT NOT NULL,
    description_source VARCHAR(20) CHECK (description_source IN ('hardcoded', 'llm', 'manual')),
    data_source VARCHAR(100),
    searchable BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_field_registry_field_name
    ON field_registry USING btree (field_name);
CREATE INDEX IF NOT EXISTS idx_field_registry_field_type
    ON field_registry USING btree (field_type);
CREATE INDEX IF NOT EXISTS idx_field_registry_searchable
    ON field_registry USING btree (searchable) WHERE (searchable = true);
CREATE INDEX IF NOT EXISTS idx_field_registry_data_source
    ON field_registry USING btree (data_source);

-- Auto-update timestamp trigger
CREATE OR REPLACE FUNCTION update_field_registry_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_field_registry_updated_at ON field_registry;
CREATE TRIGGER trigger_field_registry_updated_at
    BEFORE UPDATE ON field_registry
    FOR EACH ROW
    EXECUTE FUNCTION update_field_registry_updated_at();

COMMIT;
