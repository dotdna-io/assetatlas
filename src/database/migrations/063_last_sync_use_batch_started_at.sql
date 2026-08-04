-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Use batch ingestion time for "Last sync" instead of pipeline run time
-- Date: 2026-04-15
-- Issue: #441 follow-up
--
-- Why: Migration 062 sourced last_sync_at from
-- MAX(asset_generation_completed_at) FILTER (asset_generation_status='completed').
-- That timestamp is when the asset-generation pipeline last ran, not when the
-- underlying batch was synced from the source. If ingestion has been failing
-- for weeks but the pipeline keeps re-stamping the last healthy batch, the UI
-- showed a recent "last sync" even though no new data had arrived in weeks.
--
-- Fix: last_sync_at = MAX(ingestion_batches.started_at) FILTER
-- (asset_generation_status='completed'). This is the ingestion time of the
-- most recent batch the pipeline successfully processed — what the Recent
-- Batches table already displays on the integration-details page.

BEGIN;

-- ============================================================================
-- Partial index: supports MAX(started_at) lookups per integration filtered by
-- completed pipeline status — used by the dashboard repo live fallback, the
-- batch repo's CTE, the MCP monitoring tool, and the materialized views below.
-- ============================================================================

DROP INDEX IF EXISTS idx_batches_integration_pipeline_completed;

CREATE INDEX IF NOT EXISTS idx_batches_integration_pipeline_started
    ON ingestion_batches (integration_id, started_at DESC)
    WHERE asset_generation_status = 'completed';


-- ============================================================================
-- Recreate mv_provider_coverage
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_provider_coverage CASCADE;

CREATE MATERIALIZED VIEW mv_provider_coverage AS
WITH pipeline_sync AS (
    SELECT integration_id,
           MAX(started_at) AS last_sync_at
    FROM ingestion_batches
    WHERE asset_generation_status = 'completed'
    GROUP BY integration_id
)
SELECT
    i.id,
    i.name,
    i.integration_type,
    i.status,
    i.logo,
    ps.last_sync_at,
    COUNT(DISTINCT ap.asset_id) AS asset_count,
    ROUND(AVG(a.confidence) * 100, 0) AS avg_confidence,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence >= 0.70) AS high_conf_count,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence >= 0.45 AND a.confidence < 0.70) AS medium_conf_count,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence < 0.45) AS low_conf_count
FROM integrations i
LEFT JOIN pipeline_sync ps ON ps.integration_id = i.id
LEFT JOIN asset_providers ap ON i.id = ap.integration_id
LEFT JOIN assets a ON ap.asset_id = a.id
WHERE i.status = 'enabled'
GROUP BY i.id, i.name, i.integration_type, i.status, i.logo, ps.last_sync_at;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_provider_coverage_id
    ON mv_provider_coverage (id);


-- ============================================================================
-- Recreate mv_provider_coverage_grouped
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_provider_coverage_grouped CASCADE;

CREATE MATERIALIZED VIEW mv_provider_coverage_grouped AS
WITH pipeline_sync AS (
    SELECT integration_id,
           MAX(started_at) AS last_sync_at
    FROM ingestion_batches
    WHERE asset_generation_status = 'completed'
    GROUP BY integration_id
),
group_sync AS (
    SELECT i.group_id,
           MAX(ps.last_sync_at) AS last_sync_at
    FROM integrations i
    JOIN pipeline_sync ps ON ps.integration_id = i.id
    WHERE i.group_id IS NOT NULL
    GROUP BY i.group_id
)
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
    gs.last_sync_at
FROM integration_groups ig
JOIN integrations i ON i.group_id = ig.id AND i.status = 'enabled'
LEFT JOIN group_sync gs ON gs.group_id = ig.id
LEFT JOIN asset_providers ap ON ap.integration_id = i.id
LEFT JOIN assets a ON ap.asset_id = a.id
GROUP BY ig.id, ig.name, ig.integration_type, gs.last_sync_at

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
    ps.last_sync_at
FROM integrations i
LEFT JOIN pipeline_sync ps ON ps.integration_id = i.id
LEFT JOIN asset_providers ap ON ap.integration_id = i.id
LEFT JOIN assets a ON ap.asset_id = a.id
WHERE i.status = 'enabled' AND i.group_id IS NULL
GROUP BY i.id, i.name, i.integration_type, i.logo, ps.last_sync_at;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_provider_coverage_grouped_id_type
    ON mv_provider_coverage_grouped (id, source_type);

COMMIT;
