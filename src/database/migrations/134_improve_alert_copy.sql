-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 134: plain-language alert rule copy.
-- Rewrites name/description on 16 built-in alert rules (6 OOTB rules seeded via
-- the schema-full COPY block, 10 anomaly-detection rules seeded via migrations
-- 117/118/121) to plain, operator-facing language. query/threshold/subject_type
-- and every other column are untouched. Idempotent: re-running sets the same
-- values; a slug that doesn't exist on this install is a harmless 0-row no-op.
BEGIN;

-- OOTB rules (schema-full.sql alert_rules COPY block).
UPDATE public.alert_rules SET description = 'This asset has a public, internet-routable IP address, which may expose it to the internet.'
    WHERE slug = 'ootb-public-ip-asset';
UPDATE public.alert_rules SET description = 'An asset dropped from Secured to Discovered posture in the last 24 hours — it lost the coverage that had it protected.'
    WHERE slug = 'posture-tier-drop-secured-to-discovered';
UPDATE public.alert_rules SET name = 'Pipeline Run Recorded No Changes', description = 'The latest run found assets but logged no changes, which can mean it silently failed to track history.'
    WHERE slug = 'ootb-chronicle-silence';
UPDATE public.alert_rules SET description = 'The number of Secured assets fell more than 5% since the previous posture check.'
    WHERE slug = 'posture-tier-drop-secured-to-monitored-pct';
UPDATE public.alert_rules SET description = 'A user was granted admin or super-admin access in the last 24 hours.'
    WHERE slug = 'auth-admin-role-granted';
UPDATE public.alert_rules SET description = 'More than 5% of servers and workstations have no security tool (endpoint security or vulnerability management) covering them.'
    WHERE slug = 'ootb-security-coverage-floor';

-- Anomaly-detection rules (schema-full.sql alert_rules INSERT blocks).
UPDATE public.alert_rules SET name = 'Mass Disappearance', description = 'An unusual number of assets stopped being seen across all sources this run.'
    WHERE slug = 'anomaly-mass-missing';
UPDATE public.alert_rules SET name = 'Identity Churn', description = 'An unusual spike in assets being merged, re-added, or replaced this run — a sign of unstable identity matching.'
    WHERE slug = 'anomaly-identity-churn';
UPDATE public.alert_rules SET name = 'Unusual New Asset', description = 'A newly added asset looks unlike anything else in your environment (its type, OS, maker, and sources don''t match known patterns).'
    WHERE slug = 'anomaly-ml-newcomer';
UPDATE public.alert_rules SET name = 'Unusual Change Behavior', description = 'An asset is changing in a way that doesn''t match how similar assets normally change.'
    WHERE slug = 'anomaly-ml-behavior';
UPDATE public.alert_rules SET name = 'Mass New Assets', description = 'An unusually large number of new assets appeared this run compared with what''s normal here.'
    WHERE slug = 'anomaly-mass-new-assets';
UPDATE public.alert_rules SET name = 'Source Volume Anomaly', description = 'A data source returned far more or far fewer assets than usual, or failed to return a batch.'
    WHERE slug = 'anomaly-source-volume';
UPDATE public.alert_rules SET name = 'Attribute Churn', description = 'An unusual amount of attribute changes this run — either one field across many assets, or scattered across many.'
    WHERE slug = 'anomaly-attribute-churn';
UPDATE public.alert_rules SET name = 'Out-of-Profile Device in Segment', description = 'A new asset doesn''t fit the kind of devices that normally live on its network segment.'
    WHERE slug = 'anomaly-segment-outlier';
UPDATE public.alert_rules SET name = 'Confidence Drop', description = 'More assets than usual now have low-confidence data.'
    WHERE slug = 'anomaly-confidence-shift';
UPDATE public.alert_rules SET name = 'Per-Asset Change Burst', description = 'One or more assets changed an unusually large number of fields at once (often a re-image or device replacement).'
    WHERE slug = 'anomaly-asset-change-burst';

COMMIT;
