-- SPDX-License-Identifier: AGPL-3.0-or-later
-- ============================================================================
-- Migration 021: Add OS End-of-Life (EOL) Tracking
-- ============================================================================
-- Description: Creates infrastructure for tracking operating system end-of-life
--              dates and enriching assets with EOL status information.
-- Created: 2025-10-31
-- Author: AssetAtlas
-- ============================================================================

-- ============================================================================
-- SECTION 1: OS EOL Data Repository
-- ============================================================================
-- Stores EOL data from endoflife.date API and other authoritative sources
-- ============================================================================

CREATE TABLE os_eol_data (
    -- Primary key
    id SERIAL PRIMARY KEY,

    -- Product identification
    product_name VARCHAR(255) NOT NULL,       -- e.g., "windows", "ubuntu", "macos"
    cycle VARCHAR(100) NOT NULL,              -- e.g., "10-22h2", "24.04", "13"
    release_label VARCHAR(255),               -- e.g., "10 22H2", "24.04 LTS", "Ventura"
    codename VARCHAR(100),                    -- e.g., "Noble Numbat", "Ventura"

    -- Date information
    release_date DATE,                        -- Initial release date
    eol_date DATE,                            -- End of Life date (when all support stops)
    support_end_date DATE,                    -- End of standard support
    extended_support_date DATE,               -- End of extended support (if available)

    -- Version details
    latest_version VARCHAR(100),              -- Latest patch version (e.g., "10.0.19045")
    latest_release_date DATE,                 -- Date of latest patch

    -- Metadata
    lts BOOLEAN DEFAULT false,                -- Long-Term Support indicator
    link TEXT,                                -- Official documentation URL
    data_source VARCHAR(50) DEFAULT 'endoflife.date',

    -- Raw data and tracking
    raw_data JSONB,                           -- Full API response for future reference
    last_synced TIMESTAMPTZ DEFAULT NOW(),    -- When this record was last updated

    -- Constraints
    UNIQUE(product_name, cycle)
);

-- Indexes for fast lookups
CREATE INDEX idx_os_eol_product_cycle ON os_eol_data(product_name, cycle);
CREATE INDEX idx_os_eol_eol_date ON os_eol_data(eol_date) WHERE eol_date IS NOT NULL;
CREATE INDEX idx_os_eol_product ON os_eol_data(product_name);
CREATE INDEX idx_os_eol_last_synced ON os_eol_data(last_synced DESC);

-- Comments for documentation
COMMENT ON TABLE os_eol_data IS
'Repository of operating system end-of-life dates from authoritative sources like endoflife.date.
Used to enrich assets with OS EOL status information.';

COMMENT ON COLUMN os_eol_data.product_name IS
'Normalized product name (e.g., "windows", "ubuntu", "macos"). Maps to endoflife.date product identifiers.';

COMMENT ON COLUMN os_eol_data.cycle IS
'Version/release cycle identifier (e.g., "10-22h2" for Windows 10 22H2, "24.04" for Ubuntu 24.04 LTS).';

COMMENT ON COLUMN os_eol_data.eol_date IS
'Date when the OS version reaches end-of-life and no longer receives any support or updates.';

COMMENT ON COLUMN os_eol_data.support_end_date IS
'Date when standard support ends (may differ from EOL for products with extended support).';

COMMENT ON COLUMN os_eol_data.extended_support_date IS
'Date when extended/paid support ends (if available). For Ubuntu ESM, Windows ESU, etc.';


-- ============================================================================
-- SECTION 2: Asset EOL Enrichment Fields
-- ============================================================================
-- Add EOL-related columns to assets table
-- ============================================================================

-- Add EOL date fields
ALTER TABLE assets
    ADD COLUMN os_eol_date DATE,
    ADD COLUMN os_support_end_date DATE,
    ADD COLUMN os_extended_support_date DATE,
    ADD COLUMN os_is_lts BOOLEAN DEFAULT NULL,
    ADD COLUMN os_eol_status VARCHAR(50),
    ADD COLUMN os_eol_checked_at TIMESTAMPTZ;

-- Add indexes for EOL queries
CREATE INDEX idx_assets_eol_status ON assets(os_eol_status) WHERE os_eol_status IS NOT NULL;
CREATE INDEX idx_assets_eol_date ON assets(os_eol_date) WHERE os_eol_date IS NOT NULL;
CREATE INDEX idx_assets_eol_checked ON assets(os_eol_checked_at DESC);

-- Add constraint for EOL status values
ALTER TABLE assets
    ADD CONSTRAINT chk_os_eol_status
    CHECK (os_eol_status IN ('active', 'approaching_eol', 'end_of_life', 'unknown', NULL));

-- Comments for documentation
COMMENT ON COLUMN assets.os_eol_date IS
'End-of-life date for the operating system. Date when the OS version no longer receives support or updates.';

COMMENT ON COLUMN assets.os_support_end_date IS
'End of standard support date. May differ from EOL date if extended support is available.';

COMMENT ON COLUMN assets.os_extended_support_date IS
'End of extended/paid support date, if applicable (e.g., Ubuntu ESM, Windows ESU).';

COMMENT ON COLUMN assets.os_is_lts IS
'Indicates if the OS version is a Long-Term Support (LTS) release. LTS versions typically have longer support periods.';

COMMENT ON COLUMN assets.os_eol_status IS
'Current EOL status:
- "active": EOL date is more than 6 months away
- "approaching_eol": EOL date is within 6 months
- "end_of_life": EOL date has passed
- "unknown": No EOL data available for this OS';

COMMENT ON COLUMN assets.os_eol_checked_at IS
'Timestamp when EOL information was last checked/updated for this asset.
Used to identify assets that need re-enrichment.';


-- ============================================================================
-- SECTION 3: Asset Providers History EOL Fields
-- ============================================================================
-- Add EOL tracking to historical provider data
-- ============================================================================

-- Add EOL fields to asset_providers_history for historical tracking
ALTER TABLE asset_providers_history
    ADD COLUMN os_eol_date DATE,
    ADD COLUMN os_support_end_date DATE,
    ADD COLUMN os_eol_status VARCHAR(50);

COMMENT ON COLUMN asset_providers_history.os_eol_date IS
'Historical EOL date at the time of this provider record. Allows tracking of EOL status changes over time.';


-- ============================================================================
-- SECTION 4: Sample Data for Testing (Optional)
-- ============================================================================
-- Uncomment to populate with sample EOL data for testing
-- ============================================================================

/*
-- Sample Windows 10 22H2 (EOL: October 2025)
INSERT INTO os_eol_data (
    product_name, cycle, release_label, release_date, eol_date,
    support_end_date, latest_version, lts, link, raw_data
) VALUES (
    'windows', '10-22h2', '10 22H2', '2022-10-18', '2025-10-14',
    '2025-10-14', '10.0.19045', false,
    'https://learn.microsoft.com/lifecycle/products/windows-10-home-and-pro',
    '{"cycle":"10-22h2","releaseLabel":"10 22H2","releaseDate":"2022-10-18","eol":"2025-10-14"}'::jsonb
);

-- Sample Ubuntu 24.04 LTS (EOL: April 2029)
INSERT INTO os_eol_data (
    product_name, cycle, release_label, codename, release_date, eol_date,
    support_end_date, extended_support_date, latest_version, lts, link, raw_data
) VALUES (
    'ubuntu', '24.04', '24.04 LTS', 'Noble Numbat', '2024-04-25', '2029-04-25',
    '2029-04-25', '2036-04-25', '24.04.3', true,
    'https://wiki.ubuntu.com/Releases',
    '{"cycle":"24.04","codename":"Noble Numbat","lts":true,"releaseDate":"2024-04-25","eol":"2029-04-25"}'::jsonb
);

-- Sample macOS Ventura (EOL: September 2025)
INSERT INTO os_eol_data (
    product_name, cycle, release_label, codename, release_date, eol_date,
    support_end_date, latest_version, lts, link, raw_data
) VALUES (
    'macos', '13', '13', 'Ventura', '2022-10-24', '2025-09-15',
    '2025-09-15', '13.7.8', false,
    'https://support.apple.com/en-us/HT201222',
    '{"cycle":"13","codename":"Ventura","releaseDate":"2022-10-24","eol":"2025-09-15"}'::jsonb
);
*/


-- ============================================================================
-- SECTION 5: Verification Queries
-- ============================================================================
-- Use these to verify the migration was successful
-- ============================================================================

-- Verify os_eol_data table structure
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'os_eol_data') THEN
        RAISE NOTICE 'SUCCESS: os_eol_data table created';
    ELSE
        RAISE EXCEPTION 'FAILED: os_eol_data table not found';
    END IF;
END $$;

-- Verify assets table columns were added
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'assets' AND column_name = 'os_eol_date'
    ) THEN
        RAISE NOTICE 'SUCCESS: EOL columns added to assets table';
    ELSE
        RAISE EXCEPTION 'FAILED: EOL columns not found in assets table';
    END IF;
END $$;

-- Show table structures
SELECT
    'os_eol_data' as table_name,
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_name = 'os_eol_data'
ORDER BY ordinal_position;

SELECT
    'assets (EOL columns)' as table_name,
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_name = 'assets' AND column_name LIKE 'os_eol%'
ORDER BY ordinal_position;
