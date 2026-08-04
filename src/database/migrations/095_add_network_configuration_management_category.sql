-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 095: add 'network configuration management' source-reliability score
--
-- Patches existing source_reliability_scores rows so saved overrides gain the
-- new key. Idempotent via the `?` jsonb-key guard.

BEGIN;

UPDATE system_settings
   SET value = (value::jsonb || jsonb_build_object('network configuration management', 0.80))::text,
       updated_at = now()
 WHERE category = 'asset_generation'
   AND key      = 'source_reliability_scores'
   AND value_type = 'json'
   AND NOT (value::jsonb ? 'network configuration management');

COMMIT;
