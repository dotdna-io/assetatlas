-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Update assets table schema to support array types for multi-value fields
-- Date: 2025-10-26
-- Description: Convert single-value columns to arrays to support multiple values from different data sources

-- Start transaction
BEGIN;

-- Backup existing data type info
-- NOTE: This migration will convert existing single values to arrays

-- Convert text fields to text arrays
ALTER TABLE assets
    ALTER COLUMN asset TYPE text[] USING CASE WHEN asset IS NULL THEN NULL ELSE ARRAY[asset] END,
    ALTER COLUMN hostname TYPE text[] USING CASE WHEN hostname IS NULL THEN NULL ELSE ARRAY[hostname] END,
    ALTER COLUMN fqdn TYPE text[] USING CASE WHEN fqdn IS NULL THEN NULL ELSE ARRAY[fqdn] END,
    ALTER COLUMN sn TYPE text[] USING CASE WHEN sn IS NULL THEN NULL ELSE ARRAY[sn] END,
    ALTER COLUMN manufacturer TYPE text[] USING CASE WHEN manufacturer IS NULL THEN NULL ELSE ARRAY[manufacturer] END,
    ALTER COLUMN model TYPE text[] USING CASE WHEN model IS NULL THEN NULL ELSE ARRAY[model] END,
    ALTER COLUMN os_normalized TYPE text[] USING CASE WHEN os_normalized IS NULL THEN NULL ELSE ARRAY[os_normalized] END,
    ALTER COLUMN mac_vendor_enriched TYPE text[] USING CASE WHEN mac_vendor_enriched IS NULL THEN NULL ELSE ARRAY[mac_vendor_enriched] END,
    ALTER COLUMN vendor_category TYPE text[] USING CASE WHEN vendor_category IS NULL THEN NULL ELSE ARRAY[vendor_category] END;

-- Convert network types to arrays
ALTER TABLE assets
    ALTER COLUMN ip TYPE inet[] USING CASE WHEN ip IS NULL THEN NULL ELSE ARRAY[ip] END,
    ALTER COLUMN mac TYPE macaddr[] USING CASE WHEN mac IS NULL THEN NULL ELSE ARRAY[mac] END;

-- Convert compliance/risk fields to arrays (to track status from multiple sources)
ALTER TABLE assets
    ALTER COLUMN is_compliant TYPE text[] USING CASE
        WHEN is_compliant IS NULL THEN NULL
        WHEN is_compliant = TRUE THEN ARRAY['true']
        WHEN is_compliant = FALSE THEN ARRAY['false']
    END,
    ALTER COLUMN is_risky TYPE text[] USING CASE
        WHEN is_risky IS NULL THEN NULL
        WHEN is_risky = TRUE THEN ARRAY['true']
        WHEN is_risky = FALSE THEN ARRAY['false']
    END;

-- Commit transaction
COMMIT;

-- Add comments to document the change
COMMENT ON COLUMN assets.asset IS 'Array of asset identifiers from multiple sources';
COMMENT ON COLUMN assets.hostname IS 'Array of hostnames from multiple sources';
COMMENT ON COLUMN assets.fqdn IS 'Array of fully qualified domain names from multiple sources';
COMMENT ON COLUMN assets.ip IS 'Array of IP addresses from multiple sources';
COMMENT ON COLUMN assets.mac IS 'Array of MAC addresses from multiple sources';
COMMENT ON COLUMN assets.sn IS 'Array of serial numbers from multiple sources';
COMMENT ON COLUMN assets.manufacturer IS 'Array of manufacturers from multiple sources';
COMMENT ON COLUMN assets.model IS 'Array of models from multiple sources';
COMMENT ON COLUMN assets.os_normalized IS 'Array of normalized OS names from multiple sources';
COMMENT ON COLUMN assets.mac_vendor_enriched IS 'Array of MAC vendor enrichments';
COMMENT ON COLUMN assets.vendor_category IS 'Array of vendor categories from multiple sources';
COMMENT ON COLUMN assets.is_compliant IS 'Array of compliance statuses (''true'', ''false'', ''n/a'')';
COMMENT ON COLUMN assets.is_risky IS 'Array of risk flags (''true'', ''false'')';
