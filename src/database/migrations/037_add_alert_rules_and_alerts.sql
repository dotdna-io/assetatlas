-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 037: Add alert_rules and alerts tables for alerting engine
-- Date: 2026-02-17
-- Issue: #20

BEGIN;

-- =============================================================================
-- Table 1: alert_rules — Rule definitions
-- =============================================================================

CREATE TABLE IF NOT EXISTS alert_rules (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    slug            TEXT UNIQUE NOT NULL,
    type            TEXT NOT NULL CHECK (type IN ('security', 'compliance', 'operational')),
    severity        TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    description     TEXT NOT NULL,
    query           TEXT NOT NULL,
    threshold       JSONB,
    enabled         BOOLEAN NOT NULL DEFAULT true,
    source          TEXT NOT NULL DEFAULT 'system' CHECK (source IN ('system', 'user')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE alert_rules IS 'SQL-based alerting rule definitions evaluated post-pipeline';
COMMENT ON COLUMN alert_rules.slug IS 'Unique identifier for dedup (e.g., os-eol-critical)';
COMMENT ON COLUMN alert_rules.query IS 'SQL query returning matching assets or aggregate metrics';
COMMENT ON COLUMN alert_rules.threshold IS 'JSONB: {metric, operator, value, unit} — current_value computed at eval time';

-- =============================================================================
-- Table 2: alerts — Fired alert instances
-- =============================================================================

CREATE TABLE IF NOT EXISTS alerts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id             UUID NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
    type                TEXT NOT NULL CHECK (type IN ('security', 'compliance', 'operational')),
    severity            TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    title               TEXT NOT NULL,
    description         TEXT NOT NULL,
    threshold           JSONB,
    status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'resolved', 'acknowledged')),
    asset_count         INTEGER NOT NULL DEFAULT 0,
    assets_by_category  JSONB NOT NULL DEFAULT '{}',
    data_sources        TEXT[] NOT NULL DEFAULT '{}',
    asset_ids           UUID[] NOT NULL DEFAULT '{}',
    source              TEXT NOT NULL DEFAULT 'system',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
CREATE INDEX IF NOT EXISTS idx_alerts_rule_id ON alerts(rule_id);
CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts(created_at);
CREATE INDEX IF NOT EXISTS idx_alerts_type ON alerts(type);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);

COMMENT ON TABLE alerts IS 'Fired alert instances — denormalized for fast frontend reads';

-- =============================================================================
-- Seed: 18 out-of-the-box alert rules
-- =============================================================================

-- Security rules (7)

INSERT INTO alert_rules (name, slug, type, severity, description, query, threshold) VALUES
(
    'OS End-of-Life — Critical',
    'os-eol-critical',
    'security',
    'critical',
    'Assets running operating systems past their end-of-life date.',
    'SELECT id, hostname, category, subcategory, ip::text, os_normalized, last_time
     FROM assets
     WHERE os_eol IS NOT NULL AND os_eol < 0
       AND status != ''retired''',
    '{"metric": "os_eol_days", "operator": "<", "value": 0, "unit": "assets"}'
),
(
    'OS End-of-Life — Approaching',
    'os-eol-approaching',
    'security',
    'medium',
    'Assets with OS end-of-life within 90 days.',
    'SELECT id, hostname, category, subcategory, ip::text, os_normalized, last_time
     FROM assets
     WHERE os_eol IS NOT NULL AND os_eol > 0 AND os_eol <= 90
       AND status != ''retired''',
    '{"metric": "os_eol_days", "operator": "<=", "value": 90, "unit": "assets"}'
),
(
    'Single-Source Assets',
    'single-source-assets',
    'security',
    'medium',
    'Assets reported by only one data source — reduced confidence.',
    'SELECT id, hostname, category, subcategory, ip::text, os_normalized, last_time
     FROM assets
     WHERE array_length(data_source, 1) = 1
       AND status != ''retired''',
    '{"metric": "source_count", "operator": "=", "value": 1, "unit": "assets"}'
),
(
    'Missing Hostname',
    'missing-hostname',
    'security',
    'high',
    'Assets without a hostname — cannot be reliably identified.',
    'SELECT id, hostname, category, subcategory, ip::text, os_normalized, last_time
     FROM assets
     WHERE (hostname IS NULL OR hostname = '''')
       AND status != ''retired''',
    '{"metric": "hostname_missing", "operator": "=", "value": true, "unit": "assets"}'
),
(
    'Low Confidence Assets',
    'low-confidence-assets',
    'security',
    'medium',
    'Assets with confidence score below 0.40.',
    'SELECT id, hostname, category, subcategory, ip::text, os_normalized, last_time
     FROM assets
     WHERE confidence IS NOT NULL AND confidence < 0.40
       AND status != ''retired''',
    '{"metric": "confidence", "operator": "<", "value": 0.40, "unit": "assets"}'
),
(
    'Stale Assets — Inactive',
    'stale-assets-inactive',
    'security',
    'high',
    'Assets not seen in 7-29 days.',
    'SELECT id, hostname, category, subcategory, ip::text, os_normalized, last_time
     FROM assets
     WHERE last_time < now() - interval ''7 days''
       AND last_time >= now() - interval ''30 days''
       AND status != ''retired''',
    '{"metric": "days_since_seen", "operator": ">=", "value": 7, "unit": "assets"}'
),
(
    'Stale Assets — Retirement Candidates',
    'stale-assets-retirement',
    'security',
    'medium',
    'Assets not seen in 30+ days — candidates for retirement.',
    'SELECT id, hostname, category, subcategory, ip::text, os_normalized, last_time
     FROM assets
     WHERE last_time < now() - interval ''30 days''
       AND status != ''retired''',
    '{"metric": "days_since_seen", "operator": ">=", "value": 30, "unit": "assets"}'
);

-- Compliance rules (5)

INSERT INTO alert_rules (name, slug, type, severity, description, query, threshold) VALUES
(
    'Uncategorized Assets',
    'uncategorized-assets',
    'compliance',
    'high',
    'Assets without a category assignment.',
    'SELECT id, hostname, category, subcategory, ip::text, os_normalized, last_time
     FROM assets
     WHERE category IS NULL
       AND status != ''retired''',
    '{"metric": "uncategorized", "operator": "=", "value": true, "unit": "assets"}'
),
(
    'Category Prediction Mismatch',
    'category-mismatch',
    'compliance',
    'low',
    'Assets where manual override differs from predicted category.',
    'SELECT a.id, a.hostname, a.category, a.subcategory, a.ip::text, a.os_normalized, a.last_time
     FROM assets a
     JOIN category_overrides co ON co.asset_id = a.id
     WHERE a.category != co.category
       AND a.status != ''retired''',
    '{"metric": "category_mismatch", "operator": ">", "value": 0, "unit": "assets"}'
),
(
    'Missing Serial Number',
    'missing-serial-number',
    'compliance',
    'medium',
    'Assets without a serial number.',
    'SELECT id, hostname, category, subcategory, ip::text, os_normalized, last_time
     FROM assets
     WHERE (sn IS NULL OR sn = '''')
       AND status != ''retired''',
    '{"metric": "serial_missing", "operator": "=", "value": true, "unit": "assets"}'
),
(
    'Compliance Tag Failures',
    'compliance-tag-failures',
    'compliance',
    'high',
    'Assets tagged as non-compliant by the tag rules engine.',
    'SELECT a.id, a.hostname, a.category, a.subcategory, a.ip::text, a.os_normalized, a.last_time
     FROM assets a
     JOIN asset_tags_v2 at ON at.asset_id = a.id
     JOIN tag_rules tr ON tr.id = at.rule_id
     WHERE tr.type = ''compliance''
       AND a.status != ''retired''',
    '{"metric": "compliance_failures", "operator": ">", "value": 0, "unit": "assets"}'
),
(
    'Duplicate Hostnames',
    'duplicate-hostnames',
    'compliance',
    'medium',
    'Multiple asset IDs sharing the same hostname.',
    'SELECT id, hostname, category, subcategory, ip::text, os_normalized, last_time
     FROM assets
     WHERE hostname IN (
         SELECT hostname FROM assets
         WHERE hostname IS NOT NULL AND hostname != ''''
           AND status != ''retired''
         GROUP BY hostname HAVING COUNT(*) > 1
     ) AND status != ''retired''',
    '{"metric": "duplicate_hostnames", "operator": ">", "value": 0, "unit": "assets"}'
);

-- Operational rules (6)

INSERT INTO alert_rules (name, slug, type, severity, description, query, threshold) VALUES
(
    'Data Source Sync Failures',
    'sync-failures',
    'operational',
    'critical',
    'Data source integrations where the last sync failed.',
    'SELECT id, NULL as hostname, NULL as category, NULL as subcategory,
            NULL as ip, connector_type as os_normalized, updated_at as last_time
     FROM integrations
     WHERE last_sync_status = ''failed''
       AND enabled = true',
    '{"metric": "sync_failures", "operator": ">", "value": 0, "unit": "integrations"}'
),
(
    'Data Source Stale',
    'data-source-stale',
    'operational',
    'high',
    'Enabled integrations not synced in more than 24 hours.',
    'SELECT id, NULL as hostname, NULL as category, NULL as subcategory,
            NULL as ip, connector_type as os_normalized, last_sync_at as last_time
     FROM integrations
     WHERE enabled = true
       AND (last_sync_at IS NULL OR last_sync_at < now() - interval ''24 hours'')',
    '{"metric": "hours_since_sync", "operator": ">", "value": 24, "unit": "integrations"}'
),
(
    'Pipeline Conflicts Detected',
    'pipeline-conflicts',
    'operational',
    'medium',
    'Assets with unresolved conflicts from the pipeline.',
    'SELECT a.id, a.hostname, a.category, a.subcategory, a.ip::text, a.os_normalized, a.last_time
     FROM assets a
     JOIN asset_conflicts c ON c.asset_id = a.id
     WHERE c.status = ''unresolved''
       AND a.status != ''retired''',
    '{"metric": "unresolved_conflicts", "operator": ">", "value": 0, "unit": "assets"}'
),
(
    'High Excluded Records',
    'high-excluded-records',
    'operational',
    'high',
    'More than 5% of records excluded in the last pipeline run.',
    'SELECT id, NULL as hostname, NULL as category, NULL as subcategory,
            NULL as ip, NULL as os_normalized, completed_at as last_time
     FROM pipeline_runs
     WHERE status = ''completed''
       AND total_records > 0
       AND excluded_records::float / total_records > 0.05
     ORDER BY completed_at DESC LIMIT 1',
    '{"metric": "excluded_pct", "operator": ">", "value": 5, "unit": "percent"}'
),
(
    'New Assets Surge',
    'new-assets-surge',
    'operational',
    'medium',
    'More than 20% more new assets than the previous pipeline run.',
    'WITH recent_runs AS (
         SELECT new_assets, completed_at,
                LAG(new_assets) OVER (ORDER BY completed_at) as prev_new_assets
         FROM pipeline_runs
         WHERE status = ''completed'' AND new_assets IS NOT NULL
         ORDER BY completed_at DESC LIMIT 2
     )
     SELECT id, NULL as hostname, NULL as category, NULL as subcategory,
            NULL as ip, NULL as os_normalized, completed_at as last_time
     FROM pipeline_runs
     WHERE status = ''completed''
       AND id = (
           SELECT pr.id FROM pipeline_runs pr
           JOIN recent_runs rr ON pr.completed_at = rr.completed_at
           WHERE rr.prev_new_assets > 0
             AND rr.new_assets::float / rr.prev_new_assets > 1.2
           LIMIT 1
       )',
    '{"metric": "new_assets_pct_change", "operator": ">", "value": 20, "unit": "percent"}'
),
(
    'Assets Disappeared',
    'assets-disappeared',
    'operational',
    'high',
    'More than 10% of assets disappeared since the previous pipeline run.',
    'WITH recent_runs AS (
         SELECT total_assets, completed_at,
                LAG(total_assets) OVER (ORDER BY completed_at) as prev_total
         FROM pipeline_runs
         WHERE status = ''completed'' AND total_assets IS NOT NULL
         ORDER BY completed_at DESC LIMIT 2
     )
     SELECT id, NULL as hostname, NULL as category, NULL as subcategory,
            NULL as ip, NULL as os_normalized, completed_at as last_time
     FROM pipeline_runs
     WHERE status = ''completed''
       AND id = (
           SELECT pr.id FROM pipeline_runs pr
           JOIN recent_runs rr ON pr.completed_at = rr.completed_at
           WHERE rr.prev_total > 0
             AND (rr.prev_total - rr.total_assets)::float / rr.prev_total > 0.10
           LIMIT 1
       )',
    '{"metric": "assets_disappeared_pct", "operator": ">", "value": 10, "unit": "percent"}'
);

COMMIT;
