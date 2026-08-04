-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 089: Seed oidc settings category
-- Adds rows under the new 'oidc' category in system_settings.
-- Idempotent via ON CONFLICT DO NOTHING.

BEGIN;

INSERT INTO system_settings (category, key, value, value_type, default_value, description, validation_rule, is_sensitive, requires_restart)
VALUES
    ('oidc', 'enabled', 'false', 'boolean', 'false',
     'Master toggle for OIDC SSO authentication.',
     NULL, FALSE, FALSE),

    ('oidc', 'provider_name', 'Single sign-on', 'string', 'Single sign-on',
     'Display label on the LoginPage button (e.g., "Microsoft Entra", "Google", "Single sign-on").',
     NULL, FALSE, FALSE),

    ('oidc', 'discovery_url', '', 'string', '',
     'OIDC discovery document URL (e.g., https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration).',
     NULL, FALSE, FALSE),

    ('oidc', 'client_id', '', 'string', '',
     'OIDC application (client) ID issued by the identity provider.',
     NULL, FALSE, FALSE),

    ('oidc', 'client_secret', '', 'string', '',
     'OIDC client secret; stored encrypted at rest via Fernet helper.',
     NULL, TRUE, FALSE),

    ('oidc', 'redirect_uri', '', 'string', '',
     'Public callback URL registered in the IdP (e.g., https://app.example.com/api/auth/oidc/callback). MUST match what is configured in the IdP exactly.',
     NULL, FALSE, FALSE),

    ('oidc', 'frontend_complete_url', '/oidc-complete', 'string', '/oidc-complete',
     'Frontend route the callback redirects to with the one-time exchange code (relative path).',
     NULL, FALSE, FALSE),

    ('oidc', 'scopes', 'openid email profile', 'string', 'openid email profile',
     'Space-separated OIDC scopes to request. Add a groups scope if your provider exposes group claims (e.g., "openid email profile groups" for some IdPs, or no extra scope for Entra with groups in the token).',
     NULL, FALSE, FALSE),

    ('oidc', 'claim_mail', 'email', 'string', 'email',
     'ID token claim that holds the user''s email address (Entra: "email" or "preferred_username").',
     NULL, FALSE, FALSE),

    ('oidc', 'claim_name', 'name', 'string', 'name',
     'ID token claim that holds the user''s display name.',
     NULL, FALSE, FALSE),

    ('oidc', 'claim_groups', 'groups', 'string', 'groups',
     'ID token claim that holds the user''s groups (multi-valued; values typically full DNs or group object IDs depending on IdP).',
     NULL, FALSE, FALSE),

    ('oidc', 'group_to_role_mapping', '{}', 'json', '{}',
     'JSON object mapping group claim values (case-insensitive) to AssetAtlas roles. Example: {"00000000-0000-0000-0000-000000000001": "admin"}. Roles: user|admin|super_admin.',
     NULL, FALSE, FALSE),

    ('oidc', 'default_role', 'user', 'string', 'user',
     'Role assigned when no group in group_to_role_mapping matches the user.',
     '{"enum": ["user", "admin", "super_admin"]}', FALSE, FALSE),

    ('oidc', 'allow_local_fallback', 'true', 'boolean', 'true',
     'Continue to allow local password authentication on the LoginPage. Disable to require SSO for everyone.',
     NULL, FALSE, FALSE),

    ('oidc', 'auto_create_users', 'true', 'boolean', 'true',
     'Provision local user records on first successful SSO sign-in. Disable to require admins to pre-create users by email.',
     NULL, FALSE, FALSE),

    ('oidc', 'http_timeout_seconds', '10', 'integer', '10',
     'HTTP timeout for outbound calls to the IdP (discovery, token exchange, JWKS).',
     '{"min": 1, "max": 60}', FALSE, FALSE)

ON CONFLICT (category, key) DO NOTHING;

SELECT COUNT(*) AS oidc_settings_count
FROM system_settings
WHERE category = 'oidc';

COMMIT;
