-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Add materialized views for dashboard query optimization
-- Date: 2026-02-21
-- Purpose: Pre-compute expensive dashboard aggregates to eliminate repeated
--          full table scans on every dashboard page load.
--          Views are refreshed after each pipeline run via application code.

BEGIN;

-- ============================================================================
-- 1. Dashboard Summary View
-- Replaces: get_total_assets_count, get_assets_added_today,
--           get_category_breakdown, get_confidence_distribution
-- ============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_dashboard_summary AS
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
                        WHEN confidence >= 0.70 THEN 'high'
                        WHEN confidence >= 0.45 THEN 'medium'
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
-- 2. Provider Coverage View
-- Replaces: get_integration_stats (expensive 3-table LEFT JOIN)
-- ============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_provider_coverage AS
SELECT
    i.id,
    i.name,
    i.integration_type,
    i.status,
    i.logo,
    i.last_sync_at,
    COUNT(DISTINCT ap.asset_id) AS asset_count,
    ROUND(AVG(a.confidence) * 100, 0) AS avg_confidence,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence >= 0.70) AS high_conf_count,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence >= 0.45 AND a.confidence < 0.70) AS medium_conf_count,
    COUNT(DISTINCT a.id) FILTER (WHERE a.confidence < 0.45) AS low_conf_count
FROM integrations i
LEFT JOIN asset_providers ap ON i.id = ap.integration_id
LEFT JOIN assets a ON ap.asset_id = a.id
WHERE i.status = 'enabled'
GROUP BY i.id, i.name, i.integration_type, i.status, i.logo, i.last_sync_at;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_provider_coverage_id
    ON mv_provider_coverage (id);


-- ============================================================================
-- 3. Asset Quality Metrics View
-- Replaces: get_category_confidence_averages
-- ============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_asset_quality_metrics AS
SELECT
    CASE
        WHEN LOWER(category) IN ('ot', 'iot') THEN 'OT/IoT'
        ELSE COALESCE(category, 'Uncategorized')
    END AS category,
    AVG(confidence) AS avg_confidence,
    COUNT(*) AS count,
    CASE
        WHEN AVG(confidence) >= 0.70 THEN 'high'
        WHEN AVG(confidence) >= 0.45 THEN 'medium'
        ELSE 'low'
    END AS confidence_level
FROM assets
GROUP BY
    CASE
        WHEN LOWER(category) IN ('ot', 'iot') THEN 'OT/IoT'
        ELSE COALESCE(category, 'Uncategorized')
    END;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_asset_quality_metrics_category
    ON mv_asset_quality_metrics (category);

COMMIT;
