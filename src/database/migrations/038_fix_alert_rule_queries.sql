-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 038: Fix alert rule queries to match actual database schema
-- Date: 2026-02-17
-- Issue: All 18 seed rules from migration 037 had incorrect column references
-- Fixes:
--   - assets.status does not exist (removed filter)
--   - assets.hostname/ip/sn are arrays, not text (use [1] accessor)
--   - assets.os_eol does not exist (use os_eol_date / os_eol_status)
--   - integrations.connector_type → integration_type
--   - integrations.enabled → status = 'enabled'
--   - pipeline_runs stats are in JSONB, not columns
--   - asset_tags_v2.rule_id → tag_rule_id
--   - tag_rules.type uses 'compliance' value in type column

BEGIN;

-- =============================================================================
-- Security rules (7)
-- =============================================================================

-- 1. OS End-of-Life — Critical
UPDATE alert_rules SET query =
'SELECT id, hostname[1] as hostname, category, subcategory, ip[1]::text as ip, os_normalized, last_time
 FROM assets
 WHERE os_eol_date IS NOT NULL AND os_eol_date < CURRENT_DATE'
WHERE slug = 'os-eol-critical';

-- 2. OS End-of-Life — Approaching
UPDATE alert_rules SET query =
'SELECT id, hostname[1] as hostname, category, subcategory, ip[1]::text as ip, os_normalized, last_time
 FROM assets
 WHERE os_eol_date IS NOT NULL
   AND os_eol_date >= CURRENT_DATE
   AND os_eol_date <= CURRENT_DATE + interval ''90 days'''
WHERE slug = 'os-eol-approaching';

-- 3. Single-Source Assets
UPDATE alert_rules SET query =
'SELECT id, hostname[1] as hostname, category, subcategory, ip[1]::text as ip, os_normalized, last_time
 FROM assets
 WHERE array_length(data_source, 1) = 1'
WHERE slug = 'single-source-assets';

-- 4. Missing Hostname
UPDATE alert_rules SET query =
'SELECT id, hostname[1] as hostname, category, subcategory, ip[1]::text as ip, os_normalized, last_time
 FROM assets
 WHERE hostname IS NULL OR array_length(hostname, 1) IS NULL OR array_length(hostname, 1) = 0'
WHERE slug = 'missing-hostname';

-- 5. Low Confidence Assets
UPDATE alert_rules SET query =
'SELECT id, hostname[1] as hostname, category, subcategory, ip[1]::text as ip, os_normalized, last_time
 FROM assets
 WHERE confidence IS NOT NULL AND confidence < 0.40'
WHERE slug = 'low-confidence-assets';

-- 6. Stale Assets — Inactive (7-29 days)
UPDATE alert_rules SET query =
'SELECT id, hostname[1] as hostname, category, subcategory, ip[1]::text as ip, os_normalized, last_time
 FROM assets
 WHERE last_time < now() - interval ''7 days''
   AND last_time >= now() - interval ''30 days'''
WHERE slug = 'stale-assets-inactive';

-- 7. Stale Assets — Retirement Candidates (30+ days)
UPDATE alert_rules SET query =
'SELECT id, hostname[1] as hostname, category, subcategory, ip[1]::text as ip, os_normalized, last_time
 FROM assets
 WHERE last_time < now() - interval ''30 days'''
WHERE slug = 'stale-assets-retirement';

-- =============================================================================
-- Compliance rules (5)
-- =============================================================================

-- 8. Uncategorized Assets
UPDATE alert_rules SET query =
'SELECT id, hostname[1] as hostname, category, subcategory, ip[1]::text as ip, os_normalized, last_time
 FROM assets
 WHERE category IS NULL'
WHERE slug = 'uncategorized-assets';

-- 9. Category Prediction Mismatch
UPDATE alert_rules SET query =
'SELECT a.id, a.hostname[1] as hostname, a.category, a.subcategory, a.ip[1]::text as ip, a.os_normalized, a.last_time
 FROM assets a
 JOIN category_overrides co ON co.asset_id = a.id
 WHERE a.category != co.category'
WHERE slug = 'category-mismatch';

-- 10. Missing Serial Number
UPDATE alert_rules SET query =
'SELECT id, hostname[1] as hostname, category, subcategory, ip[1]::text as ip, os_normalized, last_time
 FROM assets
 WHERE sn IS NULL OR array_length(sn, 1) IS NULL OR array_length(sn, 1) = 0'
WHERE slug = 'missing-serial-number';

-- 11. Compliance Tag Failures
UPDATE alert_rules SET query =
'SELECT DISTINCT a.id, a.hostname[1] as hostname, a.category, a.subcategory, a.ip[1]::text as ip, a.os_normalized, a.last_time
 FROM assets a
 JOIN asset_tags_v2 atv ON atv.asset_id = a.id
 JOIN tag_rules tr ON tr.id = atv.tag_rule_id
 WHERE tr.type = ''compliance'''
WHERE slug = 'compliance-tag-failures';

-- 12. Duplicate Hostnames
UPDATE alert_rules SET query =
'SELECT id, hostname[1] as hostname, category, subcategory, ip[1]::text as ip, os_normalized, last_time
 FROM assets
 WHERE hostname[1] IN (
     SELECT hostname[1] FROM assets
     WHERE hostname IS NOT NULL AND array_length(hostname, 1) > 0
     GROUP BY hostname[1] HAVING COUNT(*) > 1
 )'
WHERE slug = 'duplicate-hostnames';

-- =============================================================================
-- Operational rules (6)
-- =============================================================================

-- 13. Data Source Sync Failures
UPDATE alert_rules SET query =
'SELECT id, NULL as hostname, NULL as category, NULL as subcategory,
        NULL as ip, integration_type as os_normalized, updated_at as last_time
 FROM integrations
 WHERE last_sync_status = ''failed''
   AND status = ''enabled'''
WHERE slug = 'sync-failures';

-- 14. Data Source Stale
UPDATE alert_rules SET query =
'SELECT id, NULL as hostname, NULL as category, NULL as subcategory,
        NULL as ip, integration_type as os_normalized, last_sync_at as last_time
 FROM integrations
 WHERE status = ''enabled''
   AND (last_sync_at IS NULL OR last_sync_at < now() - interval ''24 hours'')'
WHERE slug = 'data-source-stale';

-- 15. Pipeline Conflicts Detected
UPDATE alert_rules SET query =
'SELECT a.id, a.hostname[1] as hostname, a.category, a.subcategory, a.ip[1]::text as ip, a.os_normalized, a.last_time
 FROM assets a
 JOIN asset_conflicts c ON c.asset_id = a.id
 WHERE c.status = ''unresolved'''
WHERE slug = 'pipeline-conflicts';

-- 16. High Excluded Records (uses pipeline_runs.stats JSONB)
UPDATE alert_rules SET query =
'SELECT id, NULL as hostname, NULL as category, NULL as subcategory,
        NULL as ip, NULL as os_normalized, completed_at as last_time
 FROM pipeline_runs
 WHERE status = ''completed''
   AND stats IS NOT NULL
   AND (stats->>''total_assets_created'')::int > 0
 ORDER BY completed_at DESC LIMIT 1'
WHERE slug = 'high-excluded-records';

-- 17. New Assets Surge
UPDATE alert_rules SET query =
'WITH recent AS (
     SELECT (stats->>''total_assets_created'')::int as total_assets, completed_at,
            LAG((stats->>''total_assets_created'')::int) OVER (ORDER BY completed_at) as prev_total
     FROM pipeline_runs
     WHERE status = ''completed''
       AND stats IS NOT NULL
       AND stats->>''total_assets_created'' IS NOT NULL
     ORDER BY completed_at DESC LIMIT 2
 )
 SELECT pr.id, NULL as hostname, NULL as category, NULL as subcategory,
        NULL as ip, NULL as os_normalized, pr.completed_at as last_time
 FROM pipeline_runs pr
 JOIN recent r ON pr.completed_at = r.completed_at
 WHERE r.prev_total > 0
   AND r.total_assets::float / r.prev_total > 1.2
 LIMIT 1'
WHERE slug = 'new-assets-surge';

-- 18. Assets Disappeared
UPDATE alert_rules SET query =
'WITH recent AS (
     SELECT (stats->>''total_assets_created'')::int as total_assets, completed_at,
            LAG((stats->>''total_assets_created'')::int) OVER (ORDER BY completed_at) as prev_total
     FROM pipeline_runs
     WHERE status = ''completed''
       AND stats IS NOT NULL
       AND stats->>''total_assets_created'' IS NOT NULL
     ORDER BY completed_at DESC LIMIT 2
 )
 SELECT pr.id, NULL as hostname, NULL as category, NULL as subcategory,
        NULL as ip, NULL as os_normalized, pr.completed_at as last_time
 FROM pipeline_runs pr
 JOIN recent r ON pr.completed_at = r.completed_at
 WHERE r.prev_total > 0
   AND (r.prev_total - r.total_assets)::float / r.prev_total > 0.10
 LIMIT 1'
WHERE slug = 'assets-disappeared';

COMMIT;
