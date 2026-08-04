-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 117: anomaly-detection engine (spec 2026-07-02, #584 §4 / #486)
-- Idempotent: guarded ALTERs, CREATE TABLE IF NOT EXISTS, INSERT ... ON CONFLICT DO NOTHING.
BEGIN;

ALTER TABLE alert_rules DROP CONSTRAINT IF EXISTS alert_rules_evaluation_type_check;
ALTER TABLE alert_rules ADD CONSTRAINT alert_rules_evaluation_type_check
    CHECK (evaluation_type = ANY (ARRAY['sql'::text, 'filter'::text, 'external'::text, 'anomaly'::text]));

ALTER TABLE alert_rules DROP CONSTRAINT IF EXISTS alert_rules_path_check;
ALTER TABLE alert_rules ADD CONSTRAINT alert_rules_path_check CHECK (
    ((evaluation_type = 'sql')      AND (query IS NOT NULL) AND (target_filters IS NULL) AND (compliance_criteria IS NULL))
 OR ((evaluation_type = 'filter')   AND (query IS NULL) AND ((target_filters IS NOT NULL) OR (compliance_criteria IS NOT NULL)))
 OR ((evaluation_type = 'external') AND (query IS NULL) AND (target_filters IS NULL) AND (compliance_criteria IS NULL))
 OR ((evaluation_type = 'anomaly')  AND (query IS NULL) AND (target_filters IS NULL) AND (compliance_criteria IS NULL))
);

ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS detector_key text;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS evidence jsonb;

CREATE TABLE IF NOT EXISTS anomaly_profiles (
    profile_kind text NOT NULL,
    key          text NOT NULL,
    value        text NOT NULL,
    first_seen   timestamptz NOT NULL,
    last_seen    timestamptz NOT NULL,
    seen_count   integer NOT NULL DEFAULT 1,
    PRIMARY KEY (profile_kind, key, value)
);

INSERT INTO alert_rules (name, slug, description, type, severity, enabled, source, evaluation_type, detector_key, threshold)
VALUES
('Mass new assets', 'anomaly-mass-new-assets',
 'Unusual spike in newly created assets vs this deployment''s own baseline.',
 'operational', 'medium', true, 'system', 'anomaly', 'mass_new_assets',
 '{"z_medium": 3, "z_high": 5, "z_critical": 8, "min_count": 20, "window_runs": 30, "min_samples": 8}'::jsonb),
('Mass disappearance', 'anomaly-mass-missing',
 'Unusual number of assets going missing from all sources this run (missing_since onsets).',
 'security', 'medium', true, 'system', 'anomaly', 'mass_missing',
 '{"z_medium": 3, "z_high": 5, "z_critical": 8, "min_count": 20, "window_runs": 30, "min_samples": 8}'::jsonb),
('Source volume anomaly', 'anomaly-source-volume',
 'Per-source device-count drop/spike or missing batch vs that source''s baseline.',
 'operational', 'medium', true, 'system', 'anomaly', 'source_volume',
 '{"z_medium": 3, "z_high": 5, "z_critical": 8, "min_count": 25, "min_pct": 15, "window_runs": 30, "min_samples": 8}'::jsonb),
('Attribute churn anomaly', 'anomaly-attribute-churn',
 'Fleet-wide attribute-change spike, split into systemic (one attribute, many assets) vs scattered churn.',
 'data_quality', 'medium', true, 'system', 'anomaly', 'attribute_churn',
 '{"z_medium": 3, "z_high": 5, "z_critical": 8, "min_count": 100, "systemic_pct": 20, "window_runs": 30, "min_samples": 8}'::jsonb),
('Out-of-profile device in segment', 'anomaly-segment-outlier',
 'New asset whose category does not fit its network segment''s homogeneous profile.',
 'security', 'medium', true, 'system', 'anomaly', 'segment_outlier',
 '{"severity": "medium"}'::jsonb),
('First-seen manufacturer or OS', 'anomaly-first-seen-value',
 'A manufacturer or OS never seen before in this fleet appeared on a new asset.',
 'security', 'low', true, 'system', 'anomaly', 'first_seen_value',
 '{"severity": "low"}'::jsonb),
('Security source coverage loss', 'anomaly-security-source-loss',
 'Multiple assets lost a security-class data source this run while still visible via other sources.',
 'security', 'high', true, 'system', 'anomaly', 'security_source_loss',
 '{"z_medium": 3, "min_count": 5, "window_days": 90, "min_samples": 8, "severity": "high"}'::jsonb)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO system_settings (category, key, value, value_type, default_value, description)
VALUES
('anomaly_detection', 'security_source_categories', '["endpoint security"]', 'json', '["endpoint security"]',
 'data_source_category values that count as security tooling (D1/D2 detectors)'),
('anomaly_detection', 'security_source_names', '[]', 'json', '[]',
 'Explicit data_source names treated as security tooling, in addition to the categories'),
('anomaly_detection', 'segment_min_population', '10', 'integer', '10',
 'Minimum pre-existing assets in a segment before profiling it (C1)'),
('anomaly_detection', 'segment_homogeneity_pct', '90', 'integer', '90',
 'Dominant-category share (percent) required to call a segment homogeneous (C1)'),
('anomaly_detection', 'aggregation_threshold', '10', 'integer', '10',
 'Collapse per-entity findings into one aggregate alert above this count per detector per run')
ON CONFLICT (category, key) DO NOTHING;

COMMIT;
