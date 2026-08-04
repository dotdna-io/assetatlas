# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for src/api/config.py configuration classes.
"""


def test_chat_config_defaults():
    from src.api.config import DevelopmentConfig
    c = DevelopmentConfig()
    assert c.CHAT_ENABLED is True
    assert c.CHAT_MAX_MESSAGES_PER_USER_PER_DAY == 200
    assert c.CHAT_MAX_TOOL_ITERATIONS == 10
    assert c.CHAT_TURN_TIMEOUT_SECONDS == 60
    assert c.CHAT_MAX_INPUT_TOKENS == 100000
    assert c.CHAT_MAX_OUTPUT_TOKENS == 2000
    assert c.CHAT_RETENTION_DAYS == 90
    # Provider config (provider/model/api_key/base_url/prompt_cache/
    # thinking_enabled) lives in the chat_providers table per issue #636.
