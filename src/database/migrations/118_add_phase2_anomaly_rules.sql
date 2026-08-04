-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 118: anomaly-detection phase-2 detectors (spec 2026-07-02, #584 §4 phase 2)
-- Seeds the 7 phase-2 detector rules (A5, A6, B1a, B1b, B2 disabled, B3, D2) and the two
-- coverage settings D2 needs. Idempotent: INSERT ... ON CONFLICT DO NOTHING.
-- The engine + phase-1 rules landed in migration 117.
BEGIN;

INSERT INTO alert_rules (name, slug, description, type, severity, enabled, source, evaluation_type, detector_key, threshold)
VALUES
('Identity churn anomaly', 'anomaly-identity-churn',
 'Identity-instability rate spike: merged-asset surge, re-add surge, or a simultaneous created+removed spike (re-mint/over-merge signature) vs this deployment''s own baseline.',
 'data_quality', 'medium', true, 'system', 'anomaly', 'identity_churn',
 '{"z_medium": 3, "z_high": 5, "z_critical": 8, "min_count": 50, "window_runs": 30, "min_samples": 8}'::jsonb),
('Confidence distribution shift', 'anomaly-confidence-shift',
 'Fleet confidence distribution degraded — the low-confidence share rose vs this deployment''s own baseline.',
 'data_quality', 'medium', true, 'system', 'anomaly', 'confidence_shift',
 '{"z_medium": 3, "min_pp": 5, "window_runs": 30, "min_samples": 8}'::jsonb),
('Per-asset change burst', 'anomaly-asset-change-burst',
 'One or more assets changed an unusually large number of distinct attributes this run (re-image, identity instability, device replacement).',
 'data_quality', 'medium', true, 'system', 'anomaly', 'asset_change_burst',
 '{"min_attrs": 8, "systemic_pct": 20}'::jsonb),
('Persistent asset thrash', 'anomaly-asset-thrash',
 'Assets whose attributes oscillate across many recent runs — almost always two sources fighting over a value.',
 'data_quality', 'medium', true, 'system', 'anomaly', 'asset_thrash',
 '{"window_runs": 10, "min_runs": 8, "systemic_pct": 20}'::jsonb),
('Presence flapping', 'anomaly-presence-flap',
 'Assets flapping between present and missing across recent snapshots. Default off — post-grace weekend flap is expected; enable deliberately.',
 'data_quality', 'low', false, 'system', 'anomaly', 'presence_flap',
 '{"min_flaps": 4, "window_days": 30}'::jsonb),
('Identity swap', 'anomaly-identity-swap',
 'A stable hostname now reports a different serial, or a stable IP a different MAC — hardware swap, name impersonation, or spoofing/ARP takeover.',
 'security', 'high', true, 'system', 'anomaly', 'identity_swap',
 '{"stability_days": 14, "min_seen": 3}'::jsonb),
('Unprotected newcomer in secured segment', 'anomaly-unprotected-newcomer',
 'A newly-seen asset carries no security tooling while its network segment is otherwise security-covered.',
 'security', 'high', true, 'system', 'anomaly', 'unprotected_newcomer',
 '{"settle_min_days": 2, "settle_max_days": 7}'::jsonb)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO system_settings (category, key, value, value_type, default_value, description)
VALUES
('anomaly_detection', 'segment_coverage_pct', '80', 'integer', '80',
 'Peer coverage share (percent) above which a segment counts as security-covered (D2)'),
('anomaly_detection', 'eligible_categories_for_coverage', '["Server", "Workstation", "Laptop"]', 'json', '["Server", "Workstation", "Laptop"]',
 'Asset categories expected to carry security tooling')
ON CONFLICT (category, key) DO NOTHING;

-- Restore the 'failed batch' routing to the A3 description. Phase 1 dropped it
-- because s20 hardcoded success=True (routing unreachable); phase 2 threads real
-- per-batch outcomes into ctx.stats['current_batches'], so it is reachable again.
UPDATE alert_rules
SET description = 'Per-source device-count drop/spike, failed batch, or missing batch vs that source''s baseline.'
WHERE slug = 'anomaly-source-volume';

COMMIT;
