-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 087: Seed token-verify rate-limit setting
-- Adds auth.token_verify_rate_limit_per_ip_per_hour for use by the
-- /password-reset/verify, /onboarding/validate, and /onboarding/complete
-- endpoints. Complements the existing password_reset_rate_limit_per_ip_per_hour
-- (added in migration 086) which gates the /password-reset/request endpoint.
-- Idempotent via ON CONFLICT DO NOTHING.

BEGIN;

INSERT INTO system_settings (category, key, value, value_type, default_value, description, validation_rule, is_sensitive, requires_restart)
VALUES
    ('auth', 'token_verify_rate_limit_per_ip_per_hour', '20', 'integer', '20',
     'Maximum number of public token-handling requests (password-reset verify, onboarding validate, onboarding complete) accepted per source IP per rolling hour. Default 20 is lenient enough for legitimate wizard page reloads while still discouraging abuse.',
     '{"min": 1, "max": 1000}', FALSE, FALSE)
ON CONFLICT (category, key) DO NOTHING;

-- Verification
SELECT COUNT(*) AS auth_settings_count
FROM system_settings
WHERE category = 'auth';

COMMIT;
