-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 116: Seed chat routing settings
-- Adds tool_routing_enabled and tool_routing_top_k under the 'chat' category.
-- Idempotent via ON CONFLICT (category, key) DO NOTHING.

BEGIN;

INSERT INTO system_settings (category, key, value, value_type, default_value, description, validation_rule, is_sensitive, requires_restart)
VALUES
    ('chat', 'tool_routing_enabled', 'false', 'boolean', 'false',
     'Advertise only a core tool set + question-relevant tools to the chat LLM (with find_tools recovery), instead of all tools. Reduces input tokens per turn.',
     NULL, FALSE, FALSE),

    ('chat', 'tool_routing_top_k', '5', 'integer', '5',
     'How many question-relevant gated tools to advertise per turn when tool_routing_enabled is on.',
     '{"min": 1, "max": 20}', FALSE, FALSE)

ON CONFLICT (category, key) DO NOTHING;

COMMIT;
