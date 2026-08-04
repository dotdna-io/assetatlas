-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 088: Seed ldap settings category
-- Adds rows under the new 'ldap' category in system_settings.
-- Idempotent via ON CONFLICT DO NOTHING.
-- Follows the seeding pattern established by migrations 042, 052, 053, 082, 086.

BEGIN;

INSERT INTO system_settings (category, key, value, value_type, default_value, description, validation_rule, is_sensitive, requires_restart)
VALUES
    ('ldap', 'enabled', 'false', 'boolean', 'false',
     'Master toggle for LDAP authentication.',
     NULL, FALSE, FALSE),

    ('ldap', 'server_url', '', 'string', '',
     'LDAP server URL (ldap://host:389 or ldaps://host:636).',
     NULL, FALSE, FALSE),

    ('ldap', 'use_ssl', 'true', 'boolean', 'true',
     'Use LDAPS (TLS). Disabling means cleartext bind credentials over the wire.',
     NULL, FALSE, FALSE),

    ('ldap', 'verify_cert', 'true', 'boolean', 'true',
     'Validate server TLS certificate. Disable only for self-signed dev environments.',
     NULL, FALSE, FALSE),

    ('ldap', 'connect_timeout_seconds', '10', 'integer', '10',
     'LDAP connection + bind timeout in seconds.',
     '{"min": 1, "max": 60}', FALSE, FALSE),

    ('ldap', 'bind_dn', '', 'string', '',
     'Service account DN used to search for users (e.g., CN=svc_assetatlas,OU=Service Accounts,DC=corp,DC=com).',
     NULL, FALSE, FALSE),

    ('ldap', 'bind_password', '', 'string', '',
     'Service account password; stored encrypted at rest via Fernet helper.',
     NULL, TRUE, FALSE),

    ('ldap', 'base_dn', '', 'string', '',
     'Base DN for user search (e.g., DC=corp,DC=com).',
     NULL, FALSE, FALSE),

    ('ldap', 'user_search_filter', '(&(objectClass=person)(|(sAMAccountName={username})(mail={username})(userPrincipalName={username})))', 'string', '(&(objectClass=person)(|(sAMAccountName={username})(mail={username})(userPrincipalName={username})))',
     'LDAP filter to find a user by their submitted username. The literal "{username}" is replaced with the safely-escaped input.',
     NULL, FALSE, FALSE),

    ('ldap', 'attribute_mail', 'mail', 'string', 'mail',
     'LDAP attribute that holds the user''s email address.',
     NULL, FALSE, FALSE),

    ('ldap', 'attribute_full_name', 'displayName', 'string', 'displayName',
     'LDAP attribute that holds the user''s display name (cn is a common fallback).',
     NULL, FALSE, FALSE),

    ('ldap', 'attribute_groups', 'memberOf', 'string', 'memberOf',
     'LDAP attribute listing the groups the user belongs to (multi-valued, full DN per value).',
     NULL, FALSE, FALSE),

    ('ldap', 'group_to_role_mapping', '{}', 'json', '{}',
     'JSON object mapping LDAP group DN (case-insensitive) to AssetAtlas role. Example: {"CN=AssetAtlas Admins,OU=Groups,DC=corp,DC=com": "admin"}. Roles: user|admin|super_admin.',
     NULL, FALSE, FALSE),

    ('ldap', 'default_role', 'user', 'string', 'user',
     'Role assigned when no group in group_to_role_mapping matches the user.',
     '{"enum": ["user", "admin", "super_admin"]}', FALSE, FALSE),

    ('ldap', 'allow_local_fallback', 'true', 'boolean', 'true',
     'When LDAP rejects credentials, fall through to local password verification. Disable to enforce LDAP-only logins.',
     NULL, FALSE, FALSE)

ON CONFLICT (category, key) DO NOTHING;

-- Verification
SELECT COUNT(*) AS ldap_settings_count
FROM system_settings
WHERE category = 'ldap';

COMMIT;
