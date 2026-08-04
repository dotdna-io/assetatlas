-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 034: Add ip_primary column for temporal IP tracking
-- The ip_primary field stores the most recently seen IP address for security use cases.
ALTER TABLE assets ADD COLUMN IF NOT EXISTS ip_primary INET;
COMMENT ON COLUMN assets.ip_primary IS 'Most recently observed IP address for this asset';
