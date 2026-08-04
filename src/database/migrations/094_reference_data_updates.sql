-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 094: reference_data_updates history table
--
-- Tracks every reference-data bundle apply (uploaded or fetched from upstream).
-- Idempotent via CREATE TABLE IF NOT EXISTS.

BEGIN;

CREATE TABLE IF NOT EXISTS reference_data_updates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bundle_version  TEXT NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('upload', 'github')),
    initiated_by    TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    status          TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')) DEFAULT 'running',
    error_message   TEXT,
    datasets        JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_reference_data_updates_started
    ON reference_data_updates(started_at DESC);

COMMENT ON TABLE reference_data_updates IS
    'History of reference-data bundle applies (mac_vendor, os_eol). One row per apply attempt.';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables WHERE table_name = 'reference_data_updates'
    ) THEN
        RAISE EXCEPTION 'Migration 094 failed: reference_data_updates table not created';
    END IF;
END $$;

COMMIT;
