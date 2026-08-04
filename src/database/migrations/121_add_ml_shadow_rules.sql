-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 121: anomaly-detection phase-5 ML shadow detectors (spec 2026-07-02, #584 §4 phase 3)
-- Seeds the two ML anomaly rules (newcomer + behavior), both in SHADOW mode: their detectors
-- run and record findings into pipeline_runs.stats, but the service creates NO alerts while
-- threshold.mode = 'shadow'. An operator flips threshold.mode to 'active' (anomaly threshold
-- edits are allowed on system rules per phase 1) once shadow findings prove worth analyst time.
-- No new dependencies and no scikit-learn: the scorer is a dependency-free histogram outlier
-- model (numpy/pandas only) — see spec §14.1. Idempotent: INSERT ... ON CONFLICT DO NOTHING.
-- The engine + phase-1/2 rules landed in migrations 117/118.
BEGIN;

INSERT INTO alert_rules (name, slug, description, type, severity, enabled, source, evaluation_type, detector_key, threshold)
VALUES
('ML newcomer anomaly', 'anomaly-ml-newcomer',
 'Unsupervised outlier score for newly-created assets vs this deployment''s own fleet — flags devices whose category/OS/manufacturer/source combination is unlike anything the fleet has seen. Shadow mode: findings recorded in run stats, no alerts until an operator sets threshold.mode to "active".',
 'security', 'medium', true, 'system', 'anomaly', 'ml_newcomer',
 '{"mode": "shadow", "pct_medium": 0.5, "pct_high": 0.1, "min_fit_rows": 500, "fit_budget_seconds": 10, "top_k_categories": 15, "max_fit_rows": 25000}'::jsonb),
('ML behavior anomaly', 'anomaly-ml-behavior',
 'Unsupervised outlier score for each asset''s recent change-behavior window vs the fleet distribution — flags assets whose churn pattern is unlike their peers. Shadow mode: findings recorded in run stats, no alerts until an operator sets threshold.mode to "active".',
 'data_quality', 'medium', true, 'system', 'anomaly', 'ml_behavior',
 '{"mode": "shadow", "pct_medium": 0.5, "pct_high": 0.1, "min_fit_rows": 500, "fit_budget_seconds": 10, "window_runs": 30}'::jsonb)
ON CONFLICT (slug) DO NOTHING;

COMMIT;
