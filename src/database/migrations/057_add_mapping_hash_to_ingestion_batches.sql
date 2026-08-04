-- SPDX-License-Identifier: AGPL-3.0-or-later
BEGIN;

ALTER TABLE ingestion_batches ADD COLUMN IF NOT EXISTS mapping_hash VARCHAR(16);

COMMENT ON COLUMN ingestion_batches.mapping_hash IS 'SHA-256 hash (first 16 chars) of field_mapping config at time of mapping';

COMMIT;
