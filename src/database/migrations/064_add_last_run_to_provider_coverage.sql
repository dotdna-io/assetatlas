-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Add last_run_at to provider coverage views
-- Date: 2026-04-28
--
-- Why: `last_sync_at` reflects when the asset-generation pipeline last
-- produced output for a source. It tells you nothing about whether the
-- connector itself is still ingesting data — a source can have a
-- successful ingestion every hour but a broken downstream pipeline, in
-- which case `last_sync_at` keeps drifting while the connector is
-- perfectly healthy.
--
-- We now store both timestamps:
--   - last_sync_at  = MAX(started_at) WHERE asset_generation_status='completed'
--                     (last batch the pipeline successfully processed)
--   - last_run_at   = MAX(started_at) WHERE status='completed'
--                     (last successful connector ingestion, regardless of
--                      pipeline outcome)
--
-- The UI can then surface whichever is most relevant for a given widget.

BEGIN;

-- ============================================================================
-- Partial index: supports MAX(started_at) per integration filtered by
-- successful ingestion (regardless of downstream pipeline state).
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_batches_integration_ingestion_completed
    ON ingestion_batches (integration_id, started_at DESC)
    WHERE status = 'completed';


-- ============================================================================
-- Recreate mv_provider_coverage with both last_sync_at and last_run_at
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_provider_coverage CASCADE;

CREATE MATERIALIZED VIEW mv_provider_coverage AS
WITH pipeline_sync AS (
    SELECT integration_id,
           MAX(started_at) AS last_sync_at
    FROM ingestion_batches
    WHERE asset_generation_status = 'completed'
    GROUP BY integration_id
),
ingestion_run AS (
    SELECT integration_id,
           MAX(started_at) AS last_run_at
    FROM ingestion_batches
    WHERE status = 'completed'
    GROUP BY integration_id
)
SELECT
    i.id,
    i.name,
    i.integration_type,
    i.status,
    i.logo,
    ps.last_sync_at,
    ir.last_run_at,
    COUNT(DISTINCT ap.asset_id) AS asset_count,
    ROUND(AVG(a.confidence) * 100, 0) AS avg_confidence,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence >= 0.70) AS high_conf_count,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence >= 0.45 AND a.confidence < 0.70) AS medium_conf_count,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence < 0.45) AS low_conf_count
FROM integrations i
LEFT JOIN pipeline_sync ps ON ps.integration_id = i.id
LEFT JOIN ingestion_run ir ON ir.integration_id = i.id
LEFT JOIN asset_providers ap ON i.id = ap.integration_id
LEFT JOIN assets a ON ap.asset_id = a.id
WHERE i.status = 'enabled'
GROUP BY i.id, i.name, i.integration_type, i.status, i.logo,
         ps.last_sync_at, ir.last_run_at;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_provider_coverage_id
    ON mv_provider_coverage (id);


-- ============================================================================
-- Recreate mv_provider_coverage_grouped with both timestamps
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
ingestion_run AS (
    SELECT integration_id,
           MAX(started_at) AS last_run_at
    FROM ingestion_batches
    WHERE status = 'completed'
    GROUP BY integration_id
),
group_sync AS (
    SELECT i.group_id,
           MAX(ps.last_sync_at) AS last_sync_at,
           MAX(ir.last_run_at)  AS last_run_at
    FROM integrations i
    LEFT JOIN pipeline_sync ps ON ps.integration_id = i.id
    LEFT JOIN ingestion_run ir ON ir.integration_id = i.id
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
    gs.last_sync_at,
    gs.last_run_at
FROM integration_groups ig
JOIN integrations i ON i.group_id = ig.id AND i.status = 'enabled'
LEFT JOIN group_sync gs ON gs.group_id = ig.id
LEFT JOIN asset_providers ap ON ap.integration_id = i.id
LEFT JOIN assets a ON ap.asset_id = a.id
GROUP BY ig.id, ig.name, ig.integration_type, gs.last_sync_at, gs.last_run_at

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
    ps.last_sync_at,
    ir.last_run_at
FROM integrations i
LEFT JOIN pipeline_sync ps ON ps.integration_id = i.id
LEFT JOIN ingestion_run ir ON ir.integration_id = i.id
LEFT JOIN asset_providers ap ON ap.integration_id = i.id
LEFT JOIN assets a ON ap.asset_id = a.id
WHERE i.status = 'enabled' AND i.group_id IS NULL
GROUP BY i.id, i.name, i.integration_type, i.logo, ps.last_sync_at, ir.last_run_at;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_provider_coverage_grouped_id_type
    ON mv_provider_coverage_grouped (id, source_type);

COMMIT;
