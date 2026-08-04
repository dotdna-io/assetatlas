-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Enhance mac_vendor_ranges table with multi-source support
-- Date: 2025-10-31
-- Purpose: Add columns to support multi-source MAC vendor data from IEEE, Wireshark, and maclookup.app

-- This migration is BACKWARD COMPATIBLE
-- Existing queries using "SELECT mac_prefix, vendor FROM mac_vendor_ranges" will continue to work unchanged

-- Add new columns (nullable for backward compatibility)
ALTER TABLE mac_vendor_ranges
    ADD COLUMN IF NOT EXISTS vendor_short TEXT,
    ADD COLUMN IF NOT EXISTS vendor_address TEXT,
    ADD COLUMN IF NOT EXISTS block_type TEXT,
    ADD COLUMN IF NOT EXISTS is_private BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS data_source TEXT,
    ADD COLUMN IF NOT EXISTS last_updated TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

-- Create indexes for new columns
CREATE INDEX IF NOT EXISTS idx_mac_vendor_source ON mac_vendor_ranges(data_source);
CREATE INDEX IF NOT EXISTS idx_mac_vendor_block_type ON mac_vendor_ranges(block_type);
CREATE INDEX IF NOT EXISTS idx_mac_vendor_updated ON mac_vendor_ranges(updated_at);
CREATE INDEX IF NOT EXISTS idx_mac_vendor_is_private ON mac_vendor_ranges(is_private);

-- Add constraints
-- Note: We don't add NOT NULL to new columns for backward compatibility

-- Add table and column comments for documentation
COMMENT ON TABLE mac_vendor_ranges IS 'MAC address vendor information from multiple authoritative sources (IEEE, Wireshark, maclookup.app). Updated daily via multi-source import.';

COMMENT ON COLUMN mac_vendor_ranges.mac_prefix IS 'MAC address prefix (OUI) in XX:XX:XX format';
COMMENT ON COLUMN mac_vendor_ranges.vendor IS 'Full vendor/manufacturer name';
COMMENT ON COLUMN mac_vendor_ranges.vendor_short IS 'Shortened vendor name for display (from Wireshark manuf database)';
COMMENT ON COLUMN mac_vendor_ranges.vendor_address IS 'Full vendor address (from IEEE official registry)';
COMMENT ON COLUMN mac_vendor_ranges.block_type IS 'MAC address block type: MA-L (24-bit), MA-M (28-bit), MA-S (36-bit), CID (Company ID), or IAB (Individual Address Block)';
COMMENT ON COLUMN mac_vendor_ranges.is_private IS 'Whether company info is private/confidential per IEEE registry';
COMMENT ON COLUMN mac_vendor_ranges.data_source IS 'Primary data source: IEEE (most authoritative), Wireshark (community curated), or maclookup.app (aggregated)';
COMMENT ON COLUMN mac_vendor_ranges.last_updated IS 'When the vendor last updated their info with IEEE (from source metadata)';
COMMENT ON COLUMN mac_vendor_ranges.created_at IS 'When this record was first created in our database';
COMMENT ON COLUMN mac_vendor_ranges.updated_at IS 'When this record was last updated by our import process';

-- Verify migration
DO $$
DECLARE
    column_count INTEGER;
BEGIN
    SELECT COUNT(*)
    INTO column_count
    FROM information_schema.columns
    WHERE table_name = 'mac_vendor_ranges'
    AND column_name IN ('vendor_short', 'vendor_address', 'block_type', 'is_private', 'data_source', 'last_updated', 'updated_at');

    IF column_count = 7 THEN
        RAISE NOTICE 'Migration 022 completed successfully: All 7 new columns added to mac_vendor_ranges';
    ELSE
        RAISE EXCEPTION 'Migration 022 failed: Expected 7 new columns, found %', column_count;
    END IF;
END $$;

-- Display migration summary
SELECT
    'Migration 022' as migration,
    'Enhance mac_vendor_ranges' as description,
    '2025-10-31' as date,
    'Backward compatible' as compatibility,
    'Ready for multi-source import' as status;
