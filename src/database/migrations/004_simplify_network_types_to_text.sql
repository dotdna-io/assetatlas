-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Simplify inet[] and macaddr[] to text[] for easier handling
-- Date: 2025-10-26
-- Description: Convert inet[] and macaddr[] arrays to text[] to avoid complex type casting

BEGIN;

-- Convert inet[] to text[]
ALTER TABLE assets
    ALTER COLUMN ip TYPE text[] USING ip::text[];

-- Convert macaddr[] to text[]
ALTER TABLE assets
    ALTER COLUMN mac TYPE text[] USING mac::text[];

COMMIT;

-- Update comments
COMMENT ON COLUMN assets.ip IS 'Array of IP addresses (as text) from multiple sources';
COMMENT ON COLUMN assets.mac IS 'Array of MAC addresses (as text) from multiple sources';
