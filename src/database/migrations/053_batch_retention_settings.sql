-- SPDX-License-Identifier: AGPL-3.0-or-later
BEGIN;

-- Add ingestion batch retention settings.
-- Policy: keep batches from last N days OR latest M per integration (whichever preserves more).

INSERT INTO system_settings (category, key, value, value_type, default_value, description)
VALUES
    ('retention', 'BATCH_RETENTION_DAYS', '7', 'integer', '7',
     'Ingestion batch retention — keep batches from the last N days'),
    ('retention', 'BATCH_RETENTION_COUNT', '5', 'integer', '5',
     'Ingestion batch retention — keep latest N batches per integration (regardless of age)')
ON CONFLICT (category, key) DO NOTHING;

COMMIT;
