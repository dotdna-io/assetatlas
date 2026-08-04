-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: 052_history_retention_settings
-- Description: Add retention settings for assets_history and asset_providers_history tables
-- Date: 2026-03-20

BEGIN;

INSERT INTO system_settings (category, key, value, value_type, default_value, description)
VALUES
    ('retention', 'ASSETS_HISTORY_RETENTION_DAYS', '90', 'integer', '90',
     'Keep asset history snapshots from the last N days'),
    ('retention', 'ASSETS_HISTORY_RETENTION_COUNT', '5', 'integer', '5',
     'Keep latest N asset history snapshots (regardless of age)'),
    ('retention', 'ASSET_PROVIDERS_HISTORY_RETENTION_DAYS', '90', 'integer', '90',
     'Keep asset providers history snapshots from the last N days'),
    ('retention', 'ASSET_PROVIDERS_HISTORY_RETENTION_COUNT', '5', 'integer', '5',
     'Keep latest N asset providers history snapshots (regardless of age)')
ON CONFLICT (category, key) DO NOTHING;

COMMIT;
