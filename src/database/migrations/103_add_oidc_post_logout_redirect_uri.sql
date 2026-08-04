-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 103: OIDC RP-initiated logout (post_logout_redirect_uri)
--
-- Adds the new oidc.post_logout_redirect_uri setting. Empty string means
-- RP-initiated logout is disabled — sign-out only clears the AssetAtlas
-- session and does NOT redirect through the IdP's end_session_endpoint.
--
-- Idempotent. Safe to re-run.

BEGIN;

INSERT INTO system_settings (category, key, value, value_type, default_value, description, validation_rule, is_sensitive, requires_restart)
VALUES
    ('oidc', 'post_logout_redirect_uri', '', 'string', '',
     'Public URL the IdP redirects to after RP-initiated logout (e.g., https://app.example.com/login). MUST be registered in the IdP. Empty = RP-initiated logout disabled (only the AssetAtlas session is cleared on sign-out).',
     NULL, FALSE, FALSE)
ON CONFLICT (category, key) DO NOTHING;

COMMIT;
