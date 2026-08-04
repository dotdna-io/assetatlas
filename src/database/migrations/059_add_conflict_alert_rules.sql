-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 059: Add data_quality alert rules for conflict detection
-- Date: 2026-03-23
-- Issue: Replace structurally-impossible conflict detector with business-level
--        data quality checks surfaced through the existing alert system (S20).

BEGIN;

-- Allow 'data_quality' as a valid alert rule type
ALTER TABLE alert_rules DROP CONSTRAINT IF EXISTS alert_rules_type_check;
ALTER TABLE alert_rules ADD CONSTRAINT alert_rules_type_check
    CHECK (type IN ('security', 'compliance', 'operational', 'data_quality'));

ALTER TABLE alerts DROP CONSTRAINT IF EXISTS alerts_type_check;
ALTER TABLE alerts ADD CONSTRAINT alerts_type_check
    CHECK (type IN ('security', 'compliance', 'operational', 'data_quality'));

-- Conflict detection alert rules
INSERT INTO alert_rules (name, slug, description, type, severity, query, enabled, threshold)
VALUES
(
    'OS Family Conflict',
    'os-family-conflict',
    'Assets with conflicting operating system families across providers (e.g., Windows + macOS on the same asset)',
    'data_quality',
    'critical',
    'SELECT DISTINCT a.id, a.hostname[1] as hostname, a.category, a.subcategory, a.ip[1]::text as ip, a.os_normalized, a.last_time
     FROM assets a
     WHERE a.id IN (
       SELECT p.asset_id FROM asset_providers p
       WHERE p.os_normalized IS NOT NULL
       GROUP BY p.asset_id
       HAVING
         (bool_or(p.os_normalized ILIKE ''windows%'') AND bool_or(p.os_normalized ILIKE ''macos%''))
         OR (bool_or(p.os_normalized ILIKE ''windows%'') AND bool_or(p.os_normalized ILIKE ''apple ios%''))
         OR (bool_or(p.os_normalized ILIKE ''macos%'') AND bool_or(p.os_normalized ILIKE ''android%''))
     )',
    true,
    '{"min": 1}'::jsonb
),
(
    'Manufacturer Family Conflict',
    'manufacturer-family-conflict',
    'Assets with conflicting manufacturer families across providers (e.g., Dell + Lenovo on the same asset)',
    'data_quality',
    'high',
    'SELECT DISTINCT a.id, a.hostname[1] as hostname, a.category, a.subcategory, a.ip[1]::text as ip, a.os_normalized, a.last_time
     FROM assets a
     WHERE a.id IN (
       SELECT asset_id FROM (
         SELECT asset_id, count(DISTINCT CASE
           WHEN manufacturer ILIKE ''%dell%'' THEN ''dell''
           WHEN manufacturer ILIKE ''%lenovo%'' THEN ''lenovo''
           WHEN manufacturer ILIKE ''%hp%'' OR manufacturer ILIKE ''%hewlett%'' THEN ''hp''
           WHEN manufacturer ILIKE ''%apple%'' THEN ''apple''
           ELSE NULL END) as families
         FROM asset_providers WHERE manufacturer IS NOT NULL GROUP BY asset_id
       ) x WHERE families > 1
     )',
    true,
    '{"min": 1}'::jsonb
),
(
    'Suspected Over-Merge (Excessive Providers)',
    'excessive-providers',
    'Assets with more than 30 providers, suggesting incorrect identity merging',
    'data_quality',
    'high',
    'SELECT a.id, a.hostname[1] as hostname, a.category, a.subcategory, a.ip[1]::text as ip, a.os_normalized, a.last_time
     FROM assets a
     WHERE a.id IN (
       SELECT asset_id FROM asset_providers GROUP BY asset_id HAVING count(*) > 30
     )',
    true,
    '{"min": 1}'::jsonb
),
(
    'Suspected Over-Merge (Multiple Hostnames)',
    'multiple-hostnames',
    'Assets with more than 3 distinct hostnames, suggesting incorrect identity merging',
    'data_quality',
    'medium',
    'SELECT a.id, a.hostname[1] as hostname, a.category, a.subcategory, a.ip[1]::text as ip, a.os_normalized, a.last_time
     FROM assets a
     WHERE array_length(a.hostname, 1) > 3',
    true,
    '{"min": 1}'::jsonb
)
ON CONFLICT (slug) DO NOTHING;

COMMIT;
