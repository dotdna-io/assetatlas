-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 137: fix the 'assets-disappeared' operational rule's metric.
--
-- The rule ("More than 10% of assets disappeared since the previous pipeline
-- run.") compared stats->>'total_assets_created' between the two latest completed
-- runs. That field is NOT the asset population: since issue #796 it holds the
-- per-run count of newly created + re-added assets (chronicle asset_created +
-- asset_readded, set in s18_change_tracking), a small, volatile number that is
-- typically 0-2 in steady state. Dividing its run-over-run change by that tiny
-- number means a 1->0 or 2->0 tick reads as a "100% drop" and trips the >10%
-- threshold, so the rule fired constantly while the real population never moved
-- (e.g. dev DB: total_assets flat at 73740 across runs, total_assets_created 1->0).
--
-- The population lives in stats->>'total_assets' (len(result_df), set in
-- pipeline_service._sanitize_stats). Compare that instead so the rule fires only
-- on a genuine >10% population drop -- which is what "assets disappeared" means,
-- and what the fallback (len(result_df)) the rule was originally written against
-- always was. Grace-period carry-forward (#795/#800) keeps the population stable
-- across telemetry dips, so this no longer false-fires on weekend source gaps.
--
-- Query-only change; the threshold JSON (metric assets_disappeared_pct, > 10
-- percent) and the copy phrase ("the share of assets that disappeared this run")
-- are unchanged and now accurate. Idempotent: a plain UPDATE of the stored SQL;
-- affects 0 rows on installs where the rule was removed. Dual-written to
-- schema-full.sql for fresh installs.
BEGIN;

UPDATE alert_rules SET query = $q$WITH recent AS (
     SELECT (stats->>'total_assets')::int as total_assets, completed_at,
            LAG((stats->>'total_assets')::int) OVER (ORDER BY completed_at) as prev_total
     FROM pipeline_runs
     WHERE status = 'completed'
       AND stats IS NOT NULL
       AND stats->>'total_assets' IS NOT NULL
     ORDER BY completed_at DESC LIMIT 2
 )
 SELECT pr.id, NULL as hostname, NULL as category, NULL as subcategory,
        NULL as ip, NULL as os_normalized, pr.completed_at as last_time
 FROM pipeline_runs pr
 JOIN recent r ON pr.completed_at = r.completed_at
 WHERE r.prev_total > 0
   AND (r.prev_total - r.total_assets)::float / r.prev_total > 0.10
 LIMIT 1$q$
WHERE slug = 'assets-disappeared';

COMMIT;
