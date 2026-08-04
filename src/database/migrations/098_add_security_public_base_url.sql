-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 098: Add public_base_url to the security settings category and
-- consolidate it with the deprecated email.frontend_url.
--
-- The new security.public_base_url is the single source of truth for the
-- public HTTPS URL where users reach AssetAtlas. It replaces:
--   - email.frontend_url      (used to build invite/reset links in email)
--   - ASSETATLAS_PUBLIC_BASE_URL env var (used by the Teams manifest, the
--     teams_link service, and the Teams messaging endpoint computation)
--
-- The env var is kept as a bootstrap fallback when the DB row is empty so
-- first-boot installs continue to work without operator intervention.
--
-- Migration semantics:
--   1. Insert security.public_base_url, defaulting to the existing
--      email.frontend_url value if a non-empty one is configured. This
--      preserves the email link behavior across the upgrade.
--   2. Delete the now-orphan email.frontend_url row so the Email settings
--      tab and API stop offering it.
--
-- Idempotent: safe to re-run.

BEGIN;

INSERT INTO system_settings (category, key, value, value_type, default_value, description, validation_rule, is_sensitive, requires_restart)
SELECT
    'security',
    'public_base_url',
    COALESCE(
        (SELECT value
           FROM system_settings
          WHERE category = 'email' AND key = 'frontend_url'
            AND value IS NOT NULL AND value <> ''),
        ''
    ),
    'string',
    '',
    'Public HTTPS URL where users reach AssetAtlas (e.g., https://assetatlas.example.com). Used to build email links, Teams link tokens, and the Teams app manifest. Empty falls back to the ASSETATLAS_PUBLIC_BASE_URL env var. Changing this requires re-uploading the Teams manifest, updating the Bot Framework messaging endpoint, and updating the OIDC redirect URI registered with the IdP.',
    NULL, FALSE, FALSE
ON CONFLICT (category, key) DO NOTHING;

DELETE FROM system_settings
 WHERE category = 'email' AND key = 'frontend_url';

-- Verification
SELECT category, key, value
  FROM system_settings
 WHERE (category = 'security' AND key = 'public_base_url')
    OR (category = 'email' AND key = 'frontend_url');

COMMIT;
