-- SPDX-License-Identifier: AGPL-3.0-or-later
BEGIN;

-- Enrichment datasets (one per uploaded CSV)
CREATE TABLE IF NOT EXISTS enrichment_datasets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    match_field TEXT NOT NULL,
    match_type TEXT NOT NULL DEFAULT 'exact',
    target_field TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    row_count INTEGER NOT NULL DEFAULT 0,
    matched_count INTEGER DEFAULT NULL,
    uploaded_by TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enrichment rows (individual CSV rows)
CREATE TABLE IF NOT EXISTS enrichment_rows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id UUID NOT NULL REFERENCES enrichment_datasets(id) ON DELETE CASCADE,
    match_value TEXT NOT NULL,
    enrich_value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_enrichment_rows_dataset ON enrichment_rows(dataset_id);
CREATE INDEX IF NOT EXISTS idx_enrichment_rows_match ON enrichment_rows(match_value);
CREATE INDEX IF NOT EXISTS idx_enrichment_rows_match_lower ON enrichment_rows(dataset_id, LOWER(match_value));

COMMIT;
