-- SPDX-License-Identifier: AGPL-3.0-or-later
BEGIN;

ALTER TABLE raw_asset_data ADD COLUMN IF NOT EXISTS first_time TIMESTAMPTZ;
ALTER TABLE raw_asset_data ADD COLUMN IF NOT EXISTS last_time TIMESTAMPTZ;

COMMENT ON COLUMN raw_asset_data.first_time IS 'Earliest observation timestamp from source';
COMMENT ON COLUMN raw_asset_data.last_time IS 'Most recent observation timestamp from source';

COMMIT;
