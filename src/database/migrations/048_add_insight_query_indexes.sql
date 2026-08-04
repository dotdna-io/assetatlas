-- SPDX-License-Identifier: AGPL-3.0-or-later
BEGIN;

-- Index for insight queries that filter asset_providers by data_source
CREATE INDEX IF NOT EXISTS idx_asset_providers_data_source
    ON asset_providers (data_source);

-- Index for stale-asset and timeline queries that filter on assets.last_time
CREATE INDEX IF NOT EXISTS idx_assets_last_time
    ON assets (last_time);

COMMIT;
