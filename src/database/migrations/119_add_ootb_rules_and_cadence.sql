-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 119: OOTB alert rule pack + interval evaluation cadence (#584 §3)
--
-- Part 1 (this commit): adds alert_rules.evaluation_cadence so a rule can be
--   evaluated in-pipeline ('pipeline', the default — Stage 12 behavior is
--   unchanged) or by the scheduler worker every 5 minutes ('interval').
-- Later parts append the seed rules (posture, system-health, gated update
--   check, and the six session-addition rules).
--
-- Idempotent: guarded ALTERs (ADD COLUMN IF NOT EXISTS / DROP CONSTRAINT IF
--   EXISTS) and ON CONFLICT (slug) DO NOTHING seeds. Every change here is
--   dual-written to src/database/schema-full.sql (column + every seed row) so
--   fresh installs — which bootstrap from schema-full.sql and mark migrations
--   as already-applied without running them — get the same result as upgrades.

BEGIN;

-- --------------------------------------------------------------------------
-- Part 1 — evaluation cadence column
-- --------------------------------------------------------------------------
ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS evaluation_cadence text NOT NULL DEFAULT 'pipeline';
ALTER TABLE alert_rules DROP CONSTRAINT IF EXISTS alert_rules_cadence_check;
ALTER TABLE alert_rules ADD CONSTRAINT alert_rules_cadence_check
    CHECK (evaluation_cadence IN ('pipeline', 'interval'));

-- --------------------------------------------------------------------------
-- Seed rules are appended below this line by later parts of Phase 3.
-- --------------------------------------------------------------------------

-- Part 2 - posture-tier OOTB rules (#584 §3.1 + §3.2)
INSERT INTO alert_rules
    (name, slug, type, severity, description, query,
     target_filters, compliance_criteria, threshold, silence_duration,
     enabled, evaluation_type, evaluation_cadence, source)
VALUES
    (
        'Posture Tier Drop — Secured to Discovered',
        'posture-tier-drop-secured-to-discovered',
        'security',
        'high',
        'An asset''s posture tier fell from Secured to Discovered within the last 24 hours, per asset_chronicle posture_rules transitions.',
        $q$SELECT DISTINCT a.id, a.hostname[1] AS hostname, a.category, a.subcategory,
       a.ip[1]::text AS ip, a.os_normalized, a.last_time
FROM asset_chronicle ac
JOIN assets a ON a.id = ac.asset_id
WHERE ac.event_type = 'attribute_updated'
  AND ac.time >= now() - interval '24 hours'
  AND ac.attribute_changes ? 'posture_rules'
  AND (ac.attribute_changes -> 'posture_rules' -> 'old' -> 0 -> 'secured'   ->> 'state') = 'true'
  AND (ac.attribute_changes -> 'posture_rules' -> 'new' -> 0 -> 'secured'   ->> 'state') IS DISTINCT FROM 'true'
  AND (ac.attribute_changes -> 'posture_rules' -> 'new' -> 0 -> 'monitored' ->> 'state') IS DISTINCT FROM 'true'$q$,
        NULL,
        NULL,
        '{"min":1}'::jsonb,
        NULL,
        true,
        'sql',
        'pipeline',
        'system'
    ),
    (
        'Posture Tier Drop — Secured Coverage Fell Over 5%',
        'posture-tier-drop-secured-to-monitored-pct',
        'security',
        'medium',
        'The Secured-tier asset count fell more than 5% versus the previous posture snapshot (posture_tier_snapshots).',
        $q$WITH latest_two AS (
    SELECT secured_count,
           ROW_NUMBER() OVER (ORDER BY snapshot_at DESC, id DESC) AS rn
    FROM posture_tier_snapshots
),
cur AS (SELECT secured_count FROM latest_two WHERE rn = 1),
prev AS (SELECT secured_count FROM latest_two WHERE rn = 2)
SELECT '7c9e6a41-3d2b-4f8a-9e10-3a1b2c3d0032'::uuid AS id, NULL AS hostname, NULL AS category,
       NULL AS subcategory, NULL AS ip, NULL AS os_normalized, now() AS last_time
FROM cur, prev
WHERE prev.secured_count > 0
  AND (prev.secured_count - cur.secured_count)::float / prev.secured_count * 100 > 5$q$,
        NULL,
        NULL,
        '{"metric":"secured_to_monitored_pct","operator":">","value":5,"unit":"percent"}'::jsonb,
        NULL,
        true,
        'sql',
        'pipeline',
        'system'
    )
ON CONFLICT (slug) DO NOTHING;


-- Part 3 - system-health placeholders (Prometheus path) + gated update check (#584 §3.3/§3.4)
INSERT INTO alert_rules
    (name, slug, type, severity, description, query,
     target_filters, compliance_criteria, threshold, silence_duration,
     enabled, evaluation_type, evaluation_cadence, source)
VALUES
    (
        'AssetAtlas Update Available',
        'assetatlas-update-available',
        'operational',
        'low',
        'A newer AssetAtlas release is available. Evaluated out-of-band by the daily update-check job only when the system.enable_update_check setting is enabled (default off); air-gapped installs stay silent.',
        NULL,
        NULL,
        NULL,
        NULL,
        NULL,
        true,
        'external',
        'pipeline',
        'system'
    ),
    (
        'System Api Error Rate',
        'system-api-error-rate',
        'operational',
        'medium',
        'API 5xx error rate above 1% over 5 minutes. Evaluated by Prometheus (monitoring/alerts/system.yml, alert: system-api-error-rate) when the monitoring overlay is enabled; this DB rule is a disabled placeholder and is not evaluated by AssetAtlas itself.',
        NULL,
        NULL,
        NULL,
        NULL,
        NULL,
        false,
        'external',
        'pipeline',
        'system'
    ),
    (
        'System Db Slow Queries',
        'system-db-slow-queries',
        'operational',
        'medium',
        'Database query p95 latency above 500ms over 5 minutes. Evaluated by Prometheus (monitoring/alerts/system.yml, alert: system-db-slow-queries) when the monitoring overlay is enabled; this DB rule is a disabled placeholder and is not evaluated by AssetAtlas itself.',
        NULL,
        NULL,
        NULL,
        NULL,
        NULL,
        false,
        'external',
        'pipeline',
        'system'
    ),
    (
        'System Db Connection Pool',
        'system-db-connection-pool',
        'operational',
        'medium',
        'Database connection pool over 85% utilized for 5+ minutes. Evaluated by Prometheus (monitoring/alerts/system.yml, alert: system-db-connection-pool) when the monitoring overlay is enabled; this DB rule is a disabled placeholder and is not evaluated by AssetAtlas itself.',
        NULL,
        NULL,
        NULL,
        NULL,
        NULL,
        false,
        'external',
        'pipeline',
        'system'
    ),
    (
        'System Scheduler Stalled',
        'system-scheduler-stalled',
        'operational',
        'critical',
        'Scheduler worker heartbeat stale for more than 10 minutes. Evaluated by Prometheus (monitoring/alerts/system.yml, alert: system-scheduler-stalled) when the monitoring overlay is enabled; this DB rule is a disabled placeholder and is not evaluated by AssetAtlas itself.',
        NULL,
        NULL,
        NULL,
        NULL,
        NULL,
        false,
        'external',
        'pipeline',
        'system'
    )
ON CONFLICT (slug) DO NOTHING;

-- Part 3b - update-check gate setting (default OFF; air-gapped safe)
INSERT INTO system_settings (category, key, value, value_type, default_value, description, validation_rule, is_sensitive, requires_restart)
VALUES
    ('system', 'enable_update_check', 'false', 'boolean', 'false',
     'Allow a daily check against the public release feed for a newer AssetAtlas version. Off by default; makes no outbound network call while disabled (safe for air-gapped installs).',
     NULL, FALSE, FALSE)
ON CONFLICT (category, key) DO NOTHING;


-- Part 4 - six session-addition OOTB rules (#584)
INSERT INTO alert_rules
    (name, slug, type, severity, description, query,
     target_filters, compliance_criteria, threshold, silence_duration,
     enabled, evaluation_type, evaluation_cadence, source)
VALUES
    (
        'OS End-of-Life Transition (24h)',
        'ootb-os-eol-transition',
        'security',
        'high',
        'An asset''s OS crossed into end-of-life within the last 24 hours (asset_chronicle os_eol_status transition).',
        $q$SELECT DISTINCT a.id, a.hostname[1] AS hostname, a.category, a.subcategory,
       a.ip[1]::text AS ip, a.os_normalized, a.last_time
FROM asset_chronicle ac
JOIN assets a ON a.id = ac.asset_id
WHERE ac.event_type = 'attribute_updated'
  AND ac.time >= now() - interval '24 hours'
  AND ac.attribute_changes ? 'os_eol_status'
  AND ac.attribute_changes -> 'os_eol_status' -> 'new' ? 'end_of_life'
  AND NOT (ac.attribute_changes -> 'os_eol_status' -> 'old' ? 'end_of_life')$q$,
        NULL,
        NULL,
        '{"min":1}'::jsonb,
        NULL,
        true,
        'sql',
        'pipeline',
        'system'
    ),
    (
        'OS Support Ending Within 60 Days',
        'ootb-os-eol-forecast-60d',
        'compliance',
        'medium',
        'Assets whose OS vendor support ends within the next 60 days. Evaluated every 5 minutes; re-alerts at most weekly.',
        $q$SELECT id, hostname[1] AS hostname, category, subcategory,
       ip[1]::text AS ip, os_normalized, last_time
FROM assets
WHERE os_support_end_date IS NOT NULL
  AND os_support_end_date BETWEEN now()::date AND (now() + interval '60 days')::date$q$,
        NULL,
        NULL,
        '{"min":1}'::jsonb,
        10080,
        true,
        'sql',
        'interval',
        'system'
    ),
    (
        'Assets Nearing Grace-Period Removal',
        'ootb-grace-expiry-preview',
        'operational',
        'medium',
        'Absent assets whose removal_grace_period expires within 7 days (missing_since older than grace_days - 7). Grace window read inline from system_settings.',
        $q$WITH grace AS (
    SELECT value::int AS days FROM system_settings
    WHERE category = 'asset_generation' AND key = 'removal_grace_period_days'
)
SELECT id, hostname[1] AS hostname, category, subcategory,
       ip[1]::text AS ip, os_normalized, last_time
FROM assets, grace
WHERE missing_since IS NOT NULL
  AND grace.days > 7
  AND missing_since < now() - ((grace.days - 7) || ' days')::interval$q$,
        NULL,
        NULL,
        '{"min":1}'::jsonb,
        1440,
        true,
        'sql',
        'interval',
        'system'
    ),
    (
        'Security Coverage Below Floor (Server/Workstation)',
        'ootb-security-coverage-floor',
        'security',
        'high',
        'Fires when more than 5% of servers/workstations lack a security data source (endpoint security or vulnerability management); security coverage is below the 95% floor. Lists the uncovered assets.',
        NULL,
        '[{"field":{"kind":"static","name":"Category"},"op":"any","value":["server","workstation"]}]'::jsonb,
        '{"logic":"all","conditions":[{"field":{"kind":"static","name":"Data Source Category"},"op":"none","value":["endpoint security","vulnerability management"]}]}'::jsonb,
        '{"condition":"exceeds","value":5,"unit":"percent"}'::jsonb,
        NULL,
        true,
        'filter',
        'pipeline',
        'system'
    ),
    (
        'Asset With Public IP Address',
        'ootb-public-ip-asset',
        'security',
        'high',
        'An asset holds an IPv4 outside RFC1918/loopback/link-local (a routable public address). ip[] members are regex-validated before the ::inet cast.',
        $q$SELECT DISTINCT a.id, a.hostname[1] AS hostname, a.category, a.subcategory,
       a.ip[1]::text AS ip, a.os_normalized, a.last_time
FROM assets a
WHERE EXISTS (
    SELECT 1 FROM (
        SELECT addr::inet AS ia
        FROM unnest(a.ip) AS addr
        WHERE addr ~ '^((25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])\.){3}(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])$'
    ) v
    WHERE NOT (v.ia <<= '0.0.0.0/8'::inet
            OR v.ia <<= '10.0.0.0/8'::inet
            OR v.ia <<= '100.64.0.0/10'::inet
            OR v.ia <<= '127.0.0.0/8'::inet
            OR v.ia <<= '169.254.0.0/16'::inet
            OR v.ia <<= '172.16.0.0/12'::inet
            OR v.ia <<= '192.168.0.0/16'::inet
            OR v.ia <<= '224.0.0.0/4'::inet
            OR v.ia <<= '240.0.0.0/4'::inet)
)$q$,
        NULL,
        NULL,
        '{"min":1}'::jsonb,
        NULL,
        true,
        'sql',
        'pipeline',
        'system'
    ),
    (
        'Chronicle Silence — Run Produced No Events',
        'ootb-chronicle-silence',
        'operational',
        'high',
        'The most recent completed pipeline run recorded assets but no chronicle events — the #813 silent-failure class.',
        $q$WITH latest AS (
    SELECT id, stats, completed_at
    FROM pipeline_runs
    WHERE status = 'completed'
    ORDER BY created_at DESC
    LIMIT 1
)
SELECT id, NULL AS hostname, NULL AS category, NULL AS subcategory,
       NULL AS ip, NULL AS os_normalized, completed_at AS last_time
FROM latest
WHERE stats -> 'chronicle_events' IS NULL
  AND (stats ->> 'total_assets')::int > 0$q$,
        NULL,
        NULL,
        '{"min":1}'::jsonb,
        NULL,
        true,
        'sql',
        'pipeline',
        'system'
    )
ON CONFLICT (slug) DO NOTHING;


COMMIT;
