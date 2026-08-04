-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 147: Revise the out-of-the-box posture rules (#1157).
--
-- Two of the four shipped default sub-rules match ZERO assets on real data:
-- 'Default MDM coverage' looks for the substring "MDM" in data_source_category,
-- but the category is spelled "Mobile Device Management" ("MDM" is not a
-- substring of it), and 'domain_joined' ships with no sub-rule at all, so the
-- category evaluates nothing. This migration revises all four shipped defaults
-- and seeds the two missing pieces (a legacy-"EDR" sibling for secured, and the
-- first domain_joined rule) to the approved spec:
--
--   secured / 'Default EDR coverage'      -> 'Endpoint security coverage'
--       criteria: Data Source Category contains "endpoint security"
--                 AND confidence_level any of "medium,high"
--                 AND Last Seen within_last "15d"
--   secured / NEW 'Endpoint security coverage (legacy EDR category)'
--       same, but contains "EDR". The synonym OR cannot nest inside the AND
--       (compliance_criteria is flat), so it is a sibling sub-rule — sub-rules
--       within one category combine as OR.
--   vulnerability_scanning / 'Default VM coverage'
--                                             -> 'Vulnerability scanning coverage'
--       criteria: Data Source Category contains "vulnerability management"
--   domain_joined / NEW 'Active Directory domain membership'
--       criteria: OS Normalized contains "windows"
--                 AND Data Source contains "Active Directory" (contains, NOT eq:
--                 live installs name integrations e.g. "HSI Active Directory")
--                 AND confidence_level any of "medium,high"
--                 AND Last Seen within_last "15d"
--   device_management / 'Default MDM coverage' -> 'MDM enrollment'
--       criteria: Data Source Category contains "mobile device management"
--       target_filters narrowed to Category any of "mobile"
--   monitored / 'Default monitoring coverage'  -> 'Monitoring coverage'
--       criteria: Data Source Category contains "observability" OR "SIEM"
--                 OR "Network Monitoring"
--       target_filters -> Category any of "server,ot,network"
--
-- posture_categories.applicable_categories is revised for the same two
-- categories: device_management ["workstation","mobile"] -> ["mobile"];
-- monitored ["server","workstation","network","iot"] -> ["server","ot","network"].
--
-- GUARD DISCIPLINE — these rows are user-editable in the Posture Rules UI and
-- ARE edited on live installs, so every statement fires only on rows still at
-- the shipped default state; anything an operator touched is left alone.
-- Guards key on (category_id, name) + exact shipped target_filters /
-- compliance_criteria JSONB — NEVER on the row id (080 let gen_random_uuid()
-- default, so ids are random on migrated installs; the fixed UUIDs in
-- schema-full.sql exist only on fresh installs) and NEVER on description.
-- `enabled` is never written, so an operator's disable is respected.
--
-- GUARD JSON PROVENANCE — the guard literals below are the CURRENT shipped
-- content, i.e. migration 080's re-seed as rewritten by migration 109's
-- operator normalisation ("in" -> "any"). They are deliberately NOT 064's
-- literals: 064's rows were TRUNCATEd by 071 (FieldId cutover) and 080
-- re-seeded in the FieldId shape, so 064's pre-cutover JSON matches zero rows
-- on every install at >= 080 and guards taken from it would silently no-op
-- everywhere. JSONB equality is structural, so key order in the literals is
-- irrelevant.
--
-- Statement order is load-bearing in exactly one place: the secured legacy-EDR
-- sibling INSERT guards on 'Default EDR coverage' still being at its shipped
-- state, which the secured UPDATE below destroys — so the INSERT runs FIRST.
-- Everything else is order-independent (each guard keys on a distinct name).
--
-- Idempotent: re-running is a no-op (renamed rows fail the name guard; both
-- INSERTs fail their EXISTS guards, with ON CONFLICT (category_id, name) as a
-- second layer). Fresh-install seeds are dual-written to schema-full.sql.
-- Depends on: 146
BEGIN;

-- ---------------------------------------------------------------------------
-- 1. secured: NEW sibling sub-rule for the legacy "EDR" category spelling.
--    BEFORE the secured UPDATE (see header). Guarded on 'Default EDR coverage'
--    still being at its shipped state — if the operator edited or renamed the
--    default, we do not stack a new rule next to their custom one.
-- ---------------------------------------------------------------------------
INSERT INTO posture_sub_rules (category_id, name, description, target_filters, compliance_criteria)
SELECT 'secured',
       'Endpoint security coverage (legacy EDR category)',
       'Asset is seen by a data source still using the legacy EDR category, with at least medium confidence and activity in the last 15 days.',
       '[{"value": "server,workstation", "field_id": {"kind": "static", "name": "Category"}, "operator": "any"}]'::jsonb,
       '{"logic": "all", "conditions": [{"value": "EDR", "field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains"}, {"value": "medium,high", "field_id": {"kind": "virtual", "name": "confidence_level"}, "operator": "any"}, {"value": "15d", "field_id": {"kind": "static", "name": "Last Seen"}, "operator": "within_last"}]}'::jsonb
WHERE EXISTS (
    SELECT 1 FROM posture_sub_rules
    WHERE category_id = 'secured'
      AND name = 'Default EDR coverage'
      AND target_filters = '[{"field_id": {"kind": "static", "name": "Category"}, "operator": "any", "value": "server,workstation"}]'::jsonb
      AND compliance_criteria = '{"logic": "any", "conditions": [{"field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains", "value": "EDR"}]}'::jsonb
)
ON CONFLICT (category_id, name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2. domain_joined: seed the category's FIRST sub-rule. The category ships
--    bare, but an operator may have authored their own rule — don't stack on
--    top of it.
-- ---------------------------------------------------------------------------
INSERT INTO posture_sub_rules (category_id, name, description, target_filters, compliance_criteria)
SELECT 'domain_joined',
       'Active Directory domain membership',
       'Windows server or workstation seen by an Active Directory data source with at least medium confidence and activity in the last 15 days.',
       '[{"value": "server,workstation", "field_id": {"kind": "static", "name": "Category"}, "operator": "any"}]'::jsonb,
       '{"logic": "all", "conditions": [{"value": "windows", "field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "contains"}, {"value": "Active Directory", "field_id": {"kind": "static", "name": "Data Source"}, "operator": "contains"}, {"value": "medium,high", "field_id": {"kind": "virtual", "name": "confidence_level"}, "operator": "any"}, {"value": "15d", "field_id": {"kind": "static", "name": "Last Seen"}, "operator": "within_last"}]}'::jsonb
WHERE NOT EXISTS (
    SELECT 1 FROM posture_sub_rules WHERE category_id = 'domain_joined'
)
ON CONFLICT (category_id, name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 3. Guarded UPDATEs of the four shipped defaults (rename + new description +
--    revised filters/criteria). Each fires only while the row is byte-for-byte
--    at its shipped content under its shipped name.
-- ---------------------------------------------------------------------------
UPDATE posture_sub_rules
   SET name = 'Endpoint security coverage',
       description = 'Asset is seen by an endpoint-security data source with at least medium confidence and activity in the last 15 days.',
       target_filters = '[{"value": "server,workstation", "field_id": {"kind": "static", "name": "Category"}, "operator": "any"}]'::jsonb,
       compliance_criteria = '{"logic": "all", "conditions": [{"value": "endpoint security", "field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains"}, {"value": "medium,high", "field_id": {"kind": "virtual", "name": "confidence_level"}, "operator": "any"}, {"value": "15d", "field_id": {"kind": "static", "name": "Last Seen"}, "operator": "within_last"}]}'::jsonb,
       updated_at = NOW()
 WHERE category_id = 'secured'
   AND name = 'Default EDR coverage'
   AND target_filters = '[{"field_id": {"kind": "static", "name": "Category"}, "operator": "any", "value": "server,workstation"}]'::jsonb
   AND compliance_criteria = '{"logic": "any", "conditions": [{"field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains", "value": "EDR"}]}'::jsonb;

UPDATE posture_sub_rules
   SET name = 'Vulnerability scanning coverage',
       description = 'Asset is seen by at least one vulnerability-management data source.',
       target_filters = '[{"value": "server,workstation,network", "field_id": {"kind": "static", "name": "Category"}, "operator": "any"}]'::jsonb,
       compliance_criteria = '{"logic": "any", "conditions": [{"value": "vulnerability management", "field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains"}]}'::jsonb,
       updated_at = NOW()
 WHERE category_id = 'vulnerability_scanning'
   AND name = 'Default VM coverage'
   AND target_filters = '[{"field_id": {"kind": "static", "name": "Category"}, "operator": "any", "value": "server,workstation,network"}]'::jsonb
   AND compliance_criteria = '{"logic": "any", "conditions": [{"field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains", "value": "Vulnerability"}]}'::jsonb;

UPDATE posture_sub_rules
   SET name = 'MDM enrollment',
       description = 'Mobile asset is seen by at least one mobile-device-management data source.',
       target_filters = '[{"value": "mobile", "field_id": {"kind": "static", "name": "Category"}, "operator": "any"}]'::jsonb,
       compliance_criteria = '{"logic": "any", "conditions": [{"value": "mobile device management", "field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains"}]}'::jsonb,
       updated_at = NOW()
 WHERE category_id = 'device_management'
   AND name = 'Default MDM coverage'
   AND target_filters = '[{"field_id": {"kind": "static", "name": "Category"}, "operator": "any", "value": "workstation,mobile"}]'::jsonb
   AND compliance_criteria = '{"logic": "any", "conditions": [{"field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains", "value": "MDM"}]}'::jsonb;

UPDATE posture_sub_rules
   SET name = 'Monitoring coverage',
       description = 'Asset is seen by an observability, SIEM, or network-monitoring data source.',
       target_filters = '[{"value": "server,ot,network", "field_id": {"kind": "static", "name": "Category"}, "operator": "any"}]'::jsonb,
       compliance_criteria = '{"logic": "any", "conditions": [{"value": "observability", "field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains"}, {"value": "SIEM", "field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains"}, {"value": "Network Monitoring", "field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains"}]}'::jsonb,
       updated_at = NOW()
 WHERE category_id = 'monitored'
   AND name = 'Default monitoring coverage'
   AND target_filters = '[{"field_id": {"kind": "static", "name": "Category"}, "operator": "any", "value": "server,workstation,network,iot"}]'::jsonb
   AND compliance_criteria = '{"logic": "any", "conditions": [{"field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains", "value": "SIEM"}]}'::jsonb;

-- ---------------------------------------------------------------------------
-- 4. posture_categories: narrow the two revised applicable_categories lists.
--    Guarded on the shipped value so an operator's own edit is left alone.
-- ---------------------------------------------------------------------------
UPDATE posture_categories
   SET applicable_categories = '["mobile"]'::jsonb
 WHERE id = 'device_management'
   AND applicable_categories = '["workstation", "mobile"]'::jsonb;

UPDATE posture_categories
   SET applicable_categories = '["server", "ot", "network"]'::jsonb
 WHERE id = 'monitored'
   AND applicable_categories = '["server", "workstation", "network", "iot"]'::jsonb;

COMMIT;
