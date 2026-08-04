-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 096: Seed teams settings category
-- Adds rows under the new 'teams' category in system_settings.
-- Idempotent via ON CONFLICT DO NOTHING.
-- Mirrors the seeding pattern of migration 088 (ldap) and 089 (oidc).

BEGIN;

INSERT INTO system_settings (category, key, value, value_type, default_value, description, validation_rule, is_sensitive, requires_restart)
VALUES
    ('teams', 'enabled', 'false', 'boolean', 'false',
     'Master toggle for the Microsoft Teams chatbot bridge.',
     NULL, FALSE, FALSE),

    ('teams', 'bot_app_id', '', 'string', '',
     'Entra application (client) ID of the Azure Bot resource (UUID).',
     NULL, FALSE, FALSE),

    ('teams', 'bot_app_password', '', 'string', '',
     'Azure Bot client secret; stored encrypted at rest via Fernet helper.',
     NULL, TRUE, FALSE),

    ('teams', 'bot_app_tenant_id', '', 'string', '',
     'Microsoft Entra tenant ID (UUID) the Azure Bot is pinned to.',
     NULL, FALSE, FALSE)
ON CONFLICT (category, key) DO NOTHING;

-- Verification
SELECT COUNT(*) AS teams_settings_count
FROM system_settings
WHERE category = 'teams';

COMMIT;
