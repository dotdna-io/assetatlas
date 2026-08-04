-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 129: ML anomaly detectors fire as normal rules — remove shadow mode (#902)
-- The two ML rules seeded by migration 121 carried threshold.mode = 'shadow', which made the
-- service record their findings into pipeline_runs.stats without ever creating alerts. Issue
-- #902 removes shadow mode entirely: ml_newcomer and ml_behavior now flow through the same
-- aggregate→fire path as every other anomaly detector (the shadow gate is deleted in code).
-- This migration brings existing installs in line with the code + schema-full.sql seed:
--   1. strip the dead "mode" key from each rule's threshold jsonb, and
--   2. drop the now-false "Shadow mode: …" sentence from each rule's user-facing description.
-- The rules are already enabled=true (migration 121), so no enable change is needed. Idempotent:
-- (threshold - 'mode') and replace() are both no-ops once the key/sentence are already gone.
BEGIN;

UPDATE alert_rules
   SET threshold = threshold - 'mode',
       description = replace(
           description,
           ' Shadow mode: findings recorded in run stats, no alerts until an operator sets threshold.mode to "active".',
           '')
 WHERE source = 'system'
   AND slug IN ('anomaly-ml-newcomer', 'anomaly-ml-behavior');

COMMIT;
