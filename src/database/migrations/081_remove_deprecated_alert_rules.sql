-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 081: Remove 18 deprecated OOTB alert rules (seeded by 037 and 059)
-- Date: 2026-04-24
--
-- Historical alert instances cascade-delete via alerts.rule_id ON DELETE CASCADE.

BEGIN;

DELETE FROM alert_rules
WHERE source = 'system'
  AND slug IN (
    'os-eol-critical',
    'os-eol-approaching',
    'single-source-assets',
    'missing-hostname',
    'low-confidence-assets',
    'stale-assets-inactive',
    'uncategorized-assets',
    'category-mismatch',
    'missing-serial-number',
    'compliance-tag-failures',
    'duplicate-hostnames',
    'pipeline-conflicts',
    'high-excluded-records',
    'new-assets-surge',
    'os-family-conflict',
    'manufacturer-family-conflict',
    'excessive-providers',
    'multiple-hostnames'
  );

COMMIT;
