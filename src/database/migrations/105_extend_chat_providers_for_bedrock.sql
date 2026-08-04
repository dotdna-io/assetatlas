-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 105: extend chat_providers to include aws_bedrock
-- Adds 'aws_bedrock' to the provider CHECK constraint, seeds an empty row,
-- and widens the system_settings enum for chat.active_provider.
-- Idempotent: DROP/ADD constraint and INSERT ... ON CONFLICT are safe to
-- re-run.

BEGIN;

ALTER TABLE chat_providers DROP CONSTRAINT IF EXISTS chat_providers_provider_check;
ALTER TABLE chat_providers ADD CONSTRAINT chat_providers_provider_check
    CHECK (provider IN ('anthropic', 'openai', 'openai_compat', 'aws_bedrock'));

INSERT INTO chat_providers (provider, model)
VALUES ('aws_bedrock', '')
ON CONFLICT (provider) DO NOTHING;

UPDATE system_settings
   SET validation_rule = '{"enum": ["anthropic", "openai", "openai_compat", "aws_bedrock"]}',
       description     = 'Active chat LLM provider. One of: anthropic, openai, openai_compat, aws_bedrock.'
 WHERE category = 'chat' AND key = 'active_provider';

COMMIT;
