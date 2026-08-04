-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 104: per-provider chat config
-- Replaces the single-active-provider chat_llm_* settings with one
-- chat_providers row per provider plus a single chat.active_provider
-- setting. UI is now the only source of truth for chat config (the
-- CHAT_LLM_* env fallback is dropped — see issue #636).
--
-- Idempotent: CREATE TABLE IF NOT EXISTS, INSERT ... ON CONFLICT DO NOTHING,
-- and DELETE on the old keys is safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS chat_providers (
    provider          TEXT PRIMARY KEY
                      CHECK (provider IN ('anthropic', 'openai', 'openai_compat')),
    model             TEXT NOT NULL DEFAULT '',
    api_key_encrypted TEXT NOT NULL DEFAULT '',  -- '' = no key set
    base_url          TEXT NOT NULL DEFAULT '',  -- openai_compat only
    prompt_cache      BOOLEAN NOT NULL DEFAULT TRUE,
    thinking_enabled  BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed one row per provider so the admin UI always has something to render.
INSERT INTO chat_providers (provider, model)
VALUES
    ('anthropic', 'claude-sonnet-4-6'),
    ('openai', 'gpt-4o-mini'),
    ('openai_compat', '')
ON CONFLICT (provider) DO NOTHING;

-- Backfill: copy any existing chat_llm_* values into the active provider's
-- row, then upsert chat.active_provider so the runtime can find it. Uses
-- a DO block so the lookup-then-update logic stays inside one transaction.
DO $$
DECLARE
    v_provider TEXT;
    v_model    TEXT;
    v_api_key  TEXT;
    v_base_url TEXT;
    v_cache    TEXT;
    v_thinking TEXT;
BEGIN
    SELECT value INTO v_provider FROM system_settings
        WHERE category = 'chat' AND key = 'llm_provider';
    SELECT value INTO v_model    FROM system_settings
        WHERE category = 'chat' AND key = 'llm_model';
    SELECT value INTO v_api_key  FROM system_settings
        WHERE category = 'chat' AND key = 'llm_api_key';
    SELECT value INTO v_base_url FROM system_settings
        WHERE category = 'chat' AND key = 'llm_base_url';
    SELECT value INTO v_cache    FROM system_settings
        WHERE category = 'chat' AND key = 'prompt_cache';
    SELECT value INTO v_thinking FROM system_settings
        WHERE category = 'chat' AND key = 'thinking_enabled';

    IF v_provider IS NOT NULL
       AND v_provider IN ('anthropic', 'openai', 'openai_compat') THEN
        -- COALESCE(NULLIF(...,''), col) preserves seeded defaults when the
        -- old row was empty. Old api_key values were stored as plaintext —
        -- the runtime detects this via is_encrypted() and treats them as
        -- already-decrypted, then re-encrypts on next save.
        UPDATE chat_providers
           SET model             = COALESCE(NULLIF(v_model,    ''), model),
               api_key_encrypted = COALESCE(NULLIF(v_api_key,  ''), api_key_encrypted),
               base_url          = COALESCE(NULLIF(v_base_url, ''), base_url),
               prompt_cache      = CASE WHEN v_cache    IN ('true', 'false')
                                        THEN v_cache::boolean
                                        ELSE prompt_cache END,
               thinking_enabled  = CASE WHEN v_thinking IN ('true', 'false')
                                        THEN v_thinking::boolean
                                        ELSE thinking_enabled END,
               updated_at        = NOW()
         WHERE provider = v_provider;
    END IF;

    INSERT INTO system_settings
        (category, key, value, value_type, default_value, description,
         validation_rule, is_sensitive, requires_restart)
    VALUES
        ('chat', 'active_provider',
         CASE WHEN v_provider IN ('anthropic', 'openai', 'openai_compat')
              THEN v_provider ELSE 'anthropic' END,
         'string', 'anthropic',
         'Active chat LLM provider. One of: anthropic, openai, openai_compat.',
         '{"enum": ["anthropic", "openai", "openai_compat"]}', FALSE, FALSE)
    ON CONFLICT (category, key) DO NOTHING;
END $$;

-- Drop the old single-provider keys; their data is now in chat_providers.
DELETE FROM system_settings
 WHERE category = 'chat'
   AND key IN ('llm_provider', 'llm_model', 'llm_api_key',
               'llm_base_url', 'prompt_cache', 'thinking_enabled');

COMMIT;
