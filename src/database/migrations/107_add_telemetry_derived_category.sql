-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 107: add 'telemetry derived' source-reliability score
--
-- Patches existing source_reliability_scores rows so saved overrides gain the
-- new key. Idempotent via the `?` jsonb-key guard.

BEGIN;

UPDATE system_settings
   SET value = (value::jsonb || jsonb_build_object('telemetry derived', 0.70))::text,
       updated_at = now()
 WHERE category = 'asset_generation'
   AND key      = 'source_reliability_scores'
   AND value_type = 'json'
   AND NOT (value::jsonb ? 'telemetry derived');

COMMIT;
