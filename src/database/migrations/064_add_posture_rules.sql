-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 064: Add posture_rules system (tables + assets column + seed data)
-- Depends on: 001 (assets table)
-- Idempotent: yes (IF NOT EXISTS, ON CONFLICT DO NOTHING)

BEGIN;

-- =============================================================================
-- posture_categories: fixed set of 5, seeded below. ON DELETE RESTRICT from
-- posture_sub_rules so categories behave as schema.
-- =============================================================================
CREATE TABLE IF NOT EXISTS posture_categories (
    id                    VARCHAR(64) PRIMARY KEY,
    name                  TEXT NOT NULL,
    description           TEXT NOT NULL,
    icon                  TEXT NOT NULL,
    applicable_categories JSONB NOT NULL,
    display_order         SMALLINT NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =============================================================================
-- posture_sub_rules: user-managed. target_filters + compliance_criteria mirror
-- the FilterCondition shape from tag_rules.
-- =============================================================================
CREATE TABLE IF NOT EXISTS posture_sub_rules (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id         VARCHAR(64) NOT NULL REFERENCES posture_categories(id) ON DELETE RESTRICT,
    name                TEXT NOT NULL,
    description         TEXT,
    enabled             BOOLEAN NOT NULL DEFAULT true,
    target_filters      JSONB NOT NULL,
    compliance_criteria JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_posture_sub_rules_category
    ON posture_sub_rules(category_id);
CREATE INDEX IF NOT EXISTS idx_posture_sub_rules_enabled
    ON posture_sub_rules(category_id, enabled) WHERE enabled = true;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'posture_sub_rules_category_name_unique'
    ) THEN
        ALTER TABLE posture_sub_rules
            ADD CONSTRAINT posture_sub_rules_category_name_unique UNIQUE (category_id, name);
    END IF;
END$$;

-- updated_at trigger
CREATE OR REPLACE FUNCTION set_posture_sub_rule_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_posture_sub_rules_updated_at ON posture_sub_rules;
CREATE TRIGGER trg_posture_sub_rules_updated_at
    BEFORE UPDATE ON posture_sub_rules
    FOR EACH ROW EXECUTE FUNCTION set_posture_sub_rule_updated_at();

-- =============================================================================
-- assets.posture_rules column (per-asset evaluated state)
-- =============================================================================
ALTER TABLE assets
    ADD COLUMN IF NOT EXISTS posture_rules JSONB NOT NULL DEFAULT '{}'::jsonb;

-- =============================================================================
-- Seed: 5 posture categories (icons copied from ui/prod/src/pages/rules/posture-data.ts)
-- =============================================================================
INSERT INTO posture_categories (id, name, description, icon, applicable_categories, display_order) VALUES
  ('endpoint_protection',
   'Endpoint Protection',
   'Assets with active endpoint detection and response (EDR) agents',
   'M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z',
   '["server", "workstation"]'::jsonb,
   1),
  ('vulnerability_scanning',
   'Vulnerability Scanning',
   'Assets covered by vulnerability scanning tools',
   'M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z',
   '["server", "workstation", "network"]'::jsonb,
   2),
  ('domain_joined',
   'Domain Joined',
   'Assets joined to an Active Directory domain',
   'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z',
   '["server", "workstation"]'::jsonb,
   3),
  ('device_management',
   'Device Management',
   'Assets enrolled in device management (MDM/UEM)',
   'M17 1.01L7 1c-1.1 0-2 .9-2 2v18c0 1.1.9 2 2 2h10c1.1 0 2-.9 2-2V3c0-1.1-.9-1.99-2-1.99zM17 19H7V5h10v14z',
   '["workstation", "mobile"]'::jsonb,
   4),
  ('monitored',
   'Monitored',
   'Assets visible to monitoring or SIEM platforms',
   'M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z',
   '["server", "workstation", "network", "iot"]'::jsonb,
   5)
ON CONFLICT (id) DO NOTHING;

-- =============================================================================
-- Seed: baseline sub-rules (one per category). Users can edit or delete these.
-- Filter contents intentionally minimal — tighter rules are a user concern.
-- =============================================================================
INSERT INTO posture_sub_rules (category_id, name, target_filters, compliance_criteria) VALUES
  ('endpoint_protection',
   'Default EDR coverage',
   '[{"field": "Category", "operator": "in", "value": "server,workstation"}]'::jsonb,
   '{"logic": "any", "conditions": [{"field": "Data Source Category", "operator": "contains", "value": "EDR"}]}'::jsonb),
  ('vulnerability_scanning',
   'Default VM coverage',
   '[{"field": "Category", "operator": "in", "value": "server,workstation,network"}]'::jsonb,
   '{"logic": "any", "conditions": [{"field": "Data Source Category", "operator": "contains", "value": "Vulnerability"}]}'::jsonb),
  ('domain_joined',
   'Default domain-join check',
   '[{"field": "Category", "operator": "in", "value": "server,workstation"}]'::jsonb,
   '{"logic": "any", "conditions": [{"field": "Domain", "operator": "is_not_empty", "value": ""}]}'::jsonb),
  ('device_management',
   'Default MDM coverage',
   '[{"field": "Category", "operator": "in", "value": "workstation,mobile"}]'::jsonb,
   '{"logic": "any", "conditions": [{"field": "Data Source Category", "operator": "contains", "value": "MDM"}]}'::jsonb),
  ('monitored',
   'Default monitoring coverage',
   '[{"field": "Category", "operator": "in", "value": "server,workstation,network,iot"}]'::jsonb,
   '{"logic": "any", "conditions": [{"field": "Data Source Category", "operator": "contains", "value": "SIEM"}]}'::jsonb)
ON CONFLICT (category_id, name) DO NOTHING;

COMMIT;
