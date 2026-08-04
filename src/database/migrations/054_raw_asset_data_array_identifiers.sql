-- SPDX-License-Identifier: AGPL-3.0-or-later
-- 054_raw_asset_data_array_identifiers.sql
-- Convert raw_asset_data identifier columns from scalar to text[] arrays
-- for multi-source field mapping support (issue #353)

BEGIN;

-- Convert identifier columns to text arrays
-- ip is INET, mac is MACADDR — cast through text first
ALTER TABLE raw_asset_data
    ALTER COLUMN hostname TYPE text[] USING CASE WHEN hostname IS NULL THEN NULL ELSE ARRAY[hostname] END,
    ALTER COLUMN fqdn TYPE text[] USING CASE WHEN fqdn IS NULL THEN NULL ELSE ARRAY[fqdn] END,
    ALTER COLUMN ip TYPE text[] USING CASE WHEN ip IS NULL THEN NULL ELSE ARRAY[host(ip)] END,
    ALTER COLUMN mac TYPE text[] USING CASE WHEN mac IS NULL THEN NULL ELSE ARRAY[mac::text] END,
    ALTER COLUMN sn TYPE text[] USING CASE WHEN sn IS NULL THEN NULL ELSE ARRAY[sn] END;

-- Replace btree indexes with GIN indexes (btree does not support text[])
DROP INDEX IF EXISTS idx_raw_asset_hostname;
DROP INDEX IF EXISTS idx_raw_asset_ip;
DROP INDEX IF EXISTS idx_raw_asset_mac;
DROP INDEX IF EXISTS idx_raw_asset_sn;

CREATE INDEX idx_raw_asset_hostname ON raw_asset_data USING GIN (hostname);
CREATE INDEX idx_raw_asset_ip ON raw_asset_data USING GIN (ip);
CREATE INDEX idx_raw_asset_mac ON raw_asset_data USING GIN (mac);
CREATE INDEX idx_raw_asset_sn ON raw_asset_data USING GIN (sn);

COMMIT;
