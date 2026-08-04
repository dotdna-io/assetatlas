-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 106: add 'foundry' chat provider
-- Extends chat_providers to accept provider='foundry' and adds a
-- foundry_protocol column to distinguish the three Foundry-exposed
-- protocols (Anthropic Messages / OpenAI chat.completions / OpenAI Responses).
-- Idempotent: DROP/ADD constraint, IF NOT EXISTS column, ON CONFLICT.

BEGIN;

ALTER TABLE chat_providers DROP CONSTRAINT IF EXISTS chat_providers_provider_check;
ALTER TABLE chat_providers ADD CONSTRAINT chat_providers_provider_check
    CHECK (provider IN ('anthropic', 'openai', 'openai_compat', 'aws_bedrock', 'foundry'));

ALTER TABLE chat_providers
    ADD COLUMN IF NOT EXISTS foundry_protocol TEXT NOT NULL DEFAULT '';

INSERT INTO chat_providers (provider, model)
VALUES ('foundry', '')
ON CONFLICT (provider) DO NOTHING;

UPDATE system_settings
   SET validation_rule = '{"enum": ["anthropic", "openai", "openai_compat", "aws_bedrock", "foundry"]}',
       description     = 'Active chat LLM provider. One of: anthropic, openai, openai_compat, aws_bedrock, foundry.'
 WHERE category = 'chat' AND key = 'active_provider';

COMMIT;
