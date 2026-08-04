-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Replace btree indexes on text[] columns with GIN indexes
-- Date: 2026-03-15
-- Description: btree indexes on array columns fail when values exceed 2704 bytes.
--   GIN indexes are designed for array containment queries (@>, &&) and have no size limit.
--   Affected columns: hostname, ip, mac, sn (all text[] since migration 004).

BEGIN;

DROP INDEX IF EXISTS idx_assets_hostname;
DROP INDEX IF EXISTS idx_assets_ip;
DROP INDEX IF EXISTS idx_assets_mac;
DROP INDEX IF EXISTS idx_assets_sn;

CREATE INDEX idx_assets_hostname ON assets USING GIN (hostname);
CREATE INDEX idx_assets_ip ON assets USING GIN (ip);
CREATE INDEX idx_assets_mac ON assets USING GIN (mac);
CREATE INDEX idx_assets_sn ON assets USING GIN (sn);

COMMIT;
