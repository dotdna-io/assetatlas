-- SPDX-License-Identifier: AGPL-3.0-or-later
BEGIN;

ALTER TABLE integrations
ADD COLUMN IF NOT EXISTS source_method VARCHAR DEFAULT 'direct';

COMMENT ON COLUMN integrations.source_method IS
  'How data is collected: direct = technology API, or a transport connector type (splunk, log_analytics, etc.)';

COMMIT;
