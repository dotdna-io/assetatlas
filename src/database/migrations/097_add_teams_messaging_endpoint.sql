-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 097: Add messaging_endpoint override to the teams settings category.
-- When set, the UI/manifest use this URL verbatim; when empty, the messaging
-- endpoint is computed from ASSETATLAS_PUBLIC_BASE_URL.
-- Idempotent. Mirrors the seeding pattern of migration 096.

BEGIN;

INSERT INTO system_settings (category, key, value, value_type, default_value, description, validation_rule, is_sensitive, requires_restart)
VALUES
    ('teams', 'messaging_endpoint', '', 'string', '',
     'Operator-supplied messaging endpoint URL (HTTPS). Empty = computed from ASSETATLAS_PUBLIC_BASE_URL.',
     NULL, FALSE, FALSE)
ON CONFLICT (category, key) DO NOTHING;

-- Verification
SELECT COUNT(*) AS teams_settings_count
FROM system_settings
WHERE category = 'teams';

COMMIT;
