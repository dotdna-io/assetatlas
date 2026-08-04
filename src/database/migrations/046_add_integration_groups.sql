-- SPDX-License-Identifier: AGPL-3.0-or-later
BEGIN;

-- Integration groups: logical grouping of integrations that share the same type.
-- Used to treat multiple instances (e.g., 10 Entra tenants) as one logical source
-- for scoring and reporting.
CREATE TABLE IF NOT EXISTS integration_groups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    integration_type TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_integration_groups_type
    ON integration_groups(integration_type);

-- FK on integrations: each integration can belong to at most one group
ALTER TABLE integrations
    ADD COLUMN IF NOT EXISTS group_id UUID REFERENCES integration_groups(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_integrations_group_id
    ON integrations(group_id);

-- Materialized view: grouped provider coverage
-- Groups → one row per group (aggregated across members)
-- Ungrouped → one row per integration (same as mv_provider_coverage)
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_provider_coverage_grouped AS
SELECT
    ig.id,
    ig.name,
    ig.integration_type,
    'group'::text AS source_type,
    (SELECT i2.logo FROM integrations i2 WHERE i2.group_id = ig.id LIMIT 1) AS logo,
    COUNT(DISTINCT ap.asset_id) AS asset_count,
    ROUND(AVG(a.confidence) * 100, 0) AS avg_confidence,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence >= 0.70) AS high_conf_count,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence >= 0.45 AND a.confidence < 0.70) AS medium_conf_count,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence < 0.45) AS low_conf_count,
    MAX(i.last_sync_at) AS last_sync_at
FROM integration_groups ig
JOIN integrations i ON i.group_id = ig.id AND i.status = 'enabled'
LEFT JOIN asset_providers ap ON ap.integration_id = i.id
LEFT JOIN assets a ON ap.asset_id = a.id
GROUP BY ig.id, ig.name, ig.integration_type

UNION ALL

SELECT
    i.id,
    i.name,
    i.integration_type,
    'integration'::text AS source_type,
    i.logo,
    COUNT(DISTINCT ap.asset_id) AS asset_count,
    ROUND(AVG(a.confidence) * 100, 0) AS avg_confidence,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence >= 0.70) AS high_conf_count,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence >= 0.45 AND a.confidence < 0.70) AS medium_conf_count,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence < 0.45) AS low_conf_count,
    i.last_sync_at
FROM integrations i
LEFT JOIN asset_providers ap ON ap.integration_id = i.id
LEFT JOIN assets a ON ap.asset_id = a.id
WHERE i.status = 'enabled' AND i.group_id IS NULL
GROUP BY i.id, i.name, i.integration_type, i.logo, i.last_sync_at;

-- Composite unique index for REFRESH MATERIALIZED VIEW CONCURRENTLY
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_provider_coverage_grouped_id_type
    ON mv_provider_coverage_grouped (id, source_type);

COMMIT;
