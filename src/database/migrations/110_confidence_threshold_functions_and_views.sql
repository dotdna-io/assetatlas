-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Configurable confidence thresholds for dashboard materialized views
-- Date: 2026-06-11
--
-- Why: the dashboard high/medium/low confidence buckets were precomputed in
-- four materialized views whose SQL hardcoded 0.70 / 0.45. The operator-set
-- values live in system_settings (asset_generation.confidence_threshold_high /
-- _medium), so the dashboard ignored the slider. We now bucket through two
-- STABLE SQL functions that read system_settings, and redefine the four views
-- to call them instead of the literals. Bodies are copied VERBATIM from their
-- current definitions (migrations 044/045/064); ONLY the threshold comparisons
-- change.

BEGIN;

-- Configurable confidence thresholds as SQL functions so the dashboard
-- materialized views bucket by the operator-set values in system_settings
-- instead of hardcoded literals. STABLE = re-evaluated on each MV refresh;
-- COALESCE falls back to the schema defaults (medium 0.45 / high 0.70) when
-- the operator has never moved the slider (no row yet).
CREATE OR REPLACE FUNCTION confidence_threshold_high() RETURNS float
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(
        (SELECT value::float FROM system_settings
         WHERE category = 'asset_generation' AND key = 'confidence_threshold_high'), 0.70
    );
$$;

CREATE OR REPLACE FUNCTION confidence_threshold_medium() RETURNS float
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(
        (SELECT value::float FROM system_settings
         WHERE category = 'asset_generation' AND key = 'confidence_threshold_medium'), 0.45
    );
$$;

-- Redefine the four dashboard MVs to bucket via the functions. Each body is
-- copied VERBATIM from its current definition; ONLY the threshold comparisons
-- change: the high-bucket literal becomes confidence_threshold_high() and the
-- medium-bucket literal becomes confidence_threshold_medium(), keeping the same
-- >= / < operators. Recreate each view's UNIQUE index (CONCURRENTLY refresh
-- needs it).
-- Dependency order follows migration 064: mv_provider_coverage before
-- mv_provider_coverage_grouped.

-- ============================================================================
-- 1. mv_dashboard_summary (current def: migration 044)
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_dashboard_summary CASCADE;

CREATE MATERIALIZED VIEW mv_dashboard_summary AS
SELECT
    'summary' AS view_key,
    (SELECT COUNT(*) FROM assets) AS total_assets,
    (SELECT COUNT(*) FROM assets WHERE first_seen::date = CURRENT_DATE) AS assets_added_today,
    (
        SELECT json_agg(row_to_json(cats) ORDER BY cats.count DESC)
        FROM (
            SELECT
                COALESCE(category, 'Uncategorized') AS category,
                COUNT(*) AS count,
                ROUND(COUNT(*) * 100.0 / NULLIF(SUM(COUNT(*)) OVER (), 0), 1) AS percentage
            FROM assets
            GROUP BY category
        ) cats
    ) AS category_breakdown,
    (
        SELECT json_agg(row_to_json(conf) ORDER BY sort_order)
        FROM (
            SELECT
                level,
                COUNT(*) AS count,
                ROUND(COUNT(*) * 100.0 / NULLIF(SUM(COUNT(*)) OVER (), 0), 1) AS percentage,
                CASE level
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 3
                END AS sort_order
            FROM (
                SELECT
                    CASE
                        WHEN confidence >= confidence_threshold_high() THEN 'high'
                        WHEN confidence >= confidence_threshold_medium() THEN 'medium'
                        ELSE 'low'
                    END AS level
                FROM assets
            ) levels
            GROUP BY level
        ) conf
    ) AS confidence_distribution,
    NOW() AS refreshed_at;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_dashboard_summary_key
    ON mv_dashboard_summary (view_key);


-- ============================================================================
-- 2. mv_asset_quality_metrics (current def: migration 045)
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_asset_quality_metrics CASCADE;

CREATE MATERIALIZED VIEW mv_asset_quality_metrics AS
SELECT
    CASE
        WHEN LOWER(category) IN ('ot', 'iot') THEN 'OT/IoT'
        ELSE COALESCE(category, 'Uncategorized')
    END AS category,
    AVG(confidence) AS avg_confidence,
    COUNT(*) AS count,
    CASE
        WHEN AVG(confidence) >= confidence_threshold_high() THEN 'high'
        WHEN AVG(confidence) >= confidence_threshold_medium() THEN 'medium'
        ELSE 'low'
    END AS confidence_level,
    COUNT(*) FILTER (WHERE confidence >= confidence_threshold_high()) AS high_count,
    COUNT(*) FILTER (WHERE confidence >= confidence_threshold_medium() AND confidence < confidence_threshold_high()) AS medium_count,
    COUNT(*) FILTER (WHERE confidence < confidence_threshold_medium()) AS low_count
FROM assets
GROUP BY
    CASE
        WHEN LOWER(category) IN ('ot', 'iot') THEN 'OT/IoT'
        ELSE COALESCE(category, 'Uncategorized')
    END;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_asset_quality_metrics_category
    ON mv_asset_quality_metrics (category);


-- ============================================================================
-- 3. mv_provider_coverage (current def: migration 064)
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
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence >= confidence_threshold_high()) AS high_conf_count,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence >= confidence_threshold_medium() AND a.confidence < confidence_threshold_high()) AS medium_conf_count,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence < confidence_threshold_medium()) AS low_conf_count
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
-- 4. mv_provider_coverage_grouped (current def: migration 064)
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
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence >= confidence_threshold_high()) AS high_conf_count,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence >= confidence_threshold_medium() AND a.confidence < confidence_threshold_high()) AS medium_conf_count,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence < confidence_threshold_medium()) AS low_conf_count,
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
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence >= confidence_threshold_high()) AS high_conf_count,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence >= confidence_threshold_medium() AND a.confidence < confidence_threshold_high()) AS medium_conf_count,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence < confidence_threshold_medium()) AS low_conf_count,
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
