-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 080: Re-seed baseline posture sub-rules using the FieldId schema.
--
-- Migration 064 seeded five baseline sub-rules in the pre-cutover
-- string-field schema (`{"field": "Data Source Category", ...}`). Migration 071
-- then TRUNCATED posture_sub_rules as part of the #455 FieldId cutover, so
-- on every install from that point on there are no default posture rules —
-- users see empty categories with nothing evaluated (#456).
--
-- This migration re-seeds four of the five baselines in the canonical
-- FieldId shape (`{"field_id": {"kind": "static", "name": "..."}, ...}`)
-- using "Data Source Category" for coverage checks. "Data Source Category"
-- became a resolvable static field alongside this migration (see the
-- catalog entry in src/modules/fields/catalog.py).
--
-- The `domain_joined` category is intentionally left without a default:
-- no canonical "Domain" field exists in the catalog yet; a default there
-- would silently fail to resolve, reproducing the original bug. Users can
-- still author a rule against an extended_attribute (e.g. Entra ID domain)
-- from the UI.
--
-- Depends on: 079
-- Idempotent: yes (ON CONFLICT (category_id, name) DO NOTHING). Runs against
-- post-070 category ids ('secured', not 'endpoint_protection').

BEGIN;

INSERT INTO posture_sub_rules (
    category_id, name, description, enabled,
    target_filters, compliance_criteria, output_mode, output_value, display_order
) VALUES
  ('secured',
   'Default EDR coverage',
   'Asset is seen by at least one EDR-category data source.',
   true,
   '[{"field_id": {"kind": "static", "name": "Category"}, "operator": "in", "value": "server,workstation"}]'::jsonb,
   '{"logic": "any", "conditions": [{"field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains", "value": "EDR"}]}'::jsonb,
   NULL, NULL, 0),

  ('vulnerability_scanning',
   'Default VM coverage',
   'Asset is seen by at least one vulnerability-management data source.',
   true,
   '[{"field_id": {"kind": "static", "name": "Category"}, "operator": "in", "value": "server,workstation,network"}]'::jsonb,
   '{"logic": "any", "conditions": [{"field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains", "value": "Vulnerability"}]}'::jsonb,
   NULL, NULL, 0),

  ('device_management',
   'Default MDM coverage',
   'Asset is seen by at least one MDM/UEM data source.',
   true,
   '[{"field_id": {"kind": "static", "name": "Category"}, "operator": "in", "value": "workstation,mobile"}]'::jsonb,
   '{"logic": "any", "conditions": [{"field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains", "value": "MDM"}]}'::jsonb,
   NULL, NULL, 0),

  ('monitored',
   'Default monitoring coverage',
   'Asset is seen by at least one SIEM/monitoring data source.',
   true,
   '[{"field_id": {"kind": "static", "name": "Category"}, "operator": "in", "value": "server,workstation,network,iot"}]'::jsonb,
   '{"logic": "any", "conditions": [{"field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains", "value": "SIEM"}]}'::jsonb,
   NULL, NULL, 0)

ON CONFLICT (category_id, name) DO NOTHING;

COMMIT;
