-- SPDX-License-Identifier: AGPL-3.0-or-later
BEGIN;

-- Recreate mv_asset_quality_metrics with per-category confidence distribution counts
DROP MATERIALIZED VIEW IF EXISTS mv_asset_quality_metrics;

CREATE MATERIALIZED VIEW mv_asset_quality_metrics AS
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
    END AS confidence_level,
    COUNT(*) FILTER (WHERE confidence >= 0.70) AS high_count,
    COUNT(*) FILTER (WHERE confidence >= 0.45 AND confidence < 0.70) AS medium_count,
    COUNT(*) FILTER (WHERE confidence < 0.45) AS low_count
FROM assets
GROUP BY
    CASE
        WHEN LOWER(category) IN ('ot', 'iot') THEN 'OT/IoT'
        ELSE COALESCE(category, 'Uncategorized')
    END;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_asset_quality_metrics_category
    ON mv_asset_quality_metrics (category);

COMMIT;
