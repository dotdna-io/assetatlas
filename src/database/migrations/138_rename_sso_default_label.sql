-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 138: Shorten the default SSO button label
-- 'Sign in with Single sign-on' read clumsily on the login page; the default
-- provider_name becomes 'SSO' ('Sign in with SSO'). Only rows still carrying
-- the old default are updated — a custom provider name is left untouched.
-- Idempotent: re-running matches no rows / rewrites the same values.

BEGIN;

UPDATE system_settings
SET value = 'SSO'
WHERE category = 'oidc'
  AND key = 'provider_name'
  AND value = 'Single sign-on';

UPDATE system_settings
SET default_value = 'SSO'
WHERE category = 'oidc'
  AND key = 'provider_name'
  AND default_value = 'Single sign-on';

COMMIT;
