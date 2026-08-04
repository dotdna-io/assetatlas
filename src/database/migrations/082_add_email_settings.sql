-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 082: Seed email settings category
-- Adds ten rows under the new 'email' category in system_settings.
-- Idempotent via ON CONFLICT DO NOTHING.
-- Follows the seeding pattern established by migrations 042, 052, 053.

BEGIN;

INSERT INTO system_settings (category, key, value, value_type, default_value, description, validation_rule, is_sensitive, requires_restart)
VALUES
    ('email', 'enabled', 'false', 'boolean', 'false',
     'Master toggle for outbound email.',
     NULL, FALSE, FALSE),

    ('email', 'smtp_host', '', 'string', '',
     'SMTP relay hostname.',
     NULL, FALSE, FALSE),

    ('email', 'smtp_port', '587', 'integer', '587',
     'SMTP relay port.',
     '{"min": 1, "max": 65535}', FALSE, FALSE),

    ('email', 'smtp_security', 'starttls', 'string', 'starttls',
     'Transport security mode.',
     '{"enum": ["none", "starttls", "tls"]}', FALSE, FALSE),

    ('email', 'smtp_username', '', 'string', '',
     'SMTP auth username (blank for unauthenticated relay).',
     NULL, FALSE, FALSE),

    ('email', 'smtp_password', '', 'string', '',
     'SMTP auth password; stored encrypted at rest via Fernet helper.',
     NULL, TRUE, FALSE),

    ('email', 'from_address', '', 'string', '',
     'From address on outbound mail.',
     NULL, FALSE, FALSE),

    ('email', 'from_display_name', 'AssetAtlas', 'string', 'AssetAtlas',
     'Display name shown alongside the From address.',
     NULL, FALSE, FALSE),

    ('email', 'reply_to_address', '', 'string', '',
     'Optional Reply-To address.',
     NULL, FALSE, FALSE),

    ('email', 'frontend_url', '', 'string', '',
     'Public base URL used to build links in outbound emails.',
     NULL, FALSE, FALSE)

ON CONFLICT (category, key) DO NOTHING;

-- Verification
SELECT COUNT(*) AS email_settings_count
FROM system_settings
WHERE category = 'email';

COMMIT;
