# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the foundry-specific paths in ChatProvidersService.

Mirrors the structure of test_chat_providers_service.py: in-memory repo +
mocked settings manager, no live DB.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.api.blueprints.chat.admin_config.models import ProviderConfigUpdate
from src.api.blueprints.chat.admin_config.repository import ProviderRow
from src.api.blueprints.chat.admin_config.service import ChatProvidersService


def _row(**kw):
    defaults = {
        "provider": "foundry",
        "model": "",
        "api_key_encrypted": "",
        "base_url": "",
        "prompt_cache": True,
        "thinking_enabled": True,
        "foundry_protocol": "",
    }
    return ProviderRow(**{**defaults, **kw})


def _service():
    rows = {
        "anthropic": _row(provider="anthropic", model="claude-sonnet-4-6"),
        "openai": _row(provider="openai", model="gpt-4o-mini"),
        "openai_compat": _row(provider="openai_compat"),
        "aws_bedrock": _row(provider="aws_bedrock"),
        "foundry": _row(provider="foundry"),
    }
    repo = MagicMock(name="ChatProvidersRepository")
    repo.list_all.side_effect = lambda: dict(rows)
    repo.get.side_effect = lambda p: rows.get(p)

    def upsert(provider, fields):
        existing = rows[provider]
        merged = {**existing.__dict__, **fields, "provider": provider}
        new = ProviderRow(**merged)
        rows[provider] = new
        return new

    repo.upsert.side_effect = upsert

    sm = MagicMock(name="settings_manager")
    state = {"active": "anthropic"}
    sm.get.side_effect = lambda c, k, default=None: (
        state["active"] if (c, k) == ("chat", "active_provider") else default
    )
    sm.set.return_value = (True, None)

    return ChatProvidersService(repo, sm), rows


def test_update_persists_foundry_protocol_and_base_url():
    svc, rows = _service()
    svc.update_provider("foundry", ProviderConfigUpdate(
        model="claude-sonnet-4-6",
        api_key="key-abc",
        base_url="https://example.com/anthropic/v1/messages",
        foundry_protocol="anthropic",
    ))
    saved = rows["foundry"]
    assert saved.model == "claude-sonnet-4-6"
    assert saved.base_url == "https://example.com/anthropic/v1/messages"
    assert saved.foundry_protocol == "anthropic"
    assert saved.api_key_encrypted  # encrypted, not plaintext
    assert saved.api_key_encrypted != "key-abc"


def test_admin_config_surfaces_foundry_protocol():
    svc, rows = _service()
    svc.update_provider("foundry", ProviderConfigUpdate(
        model="gpt-5.4-mini",
        api_key="k",
        base_url="https://example.com/openai/responses?api-version=v",
        foundry_protocol="openai_responses",
    ))
    cfg = svc.get_admin_config(chat_enabled=True)
    p = cfg.providers["foundry"]
    assert p.foundry_protocol == "openai_responses"
    assert p.base_url == "https://example.com/openai/responses?api-version=v"
    assert p.api_key_configured is True
    assert p.api_key_preview.endswith("k") or p.api_key_preview.startswith("•")


def test_get_effective_config_emits_foundry_keys():
    svc, rows = _service()
    svc.update_provider("foundry", ProviderConfigUpdate(
        model="Kimi-K2.5",
        api_key="k1",
        base_url="https://example.com/models/chat/completions?api-version=v",
        foundry_protocol="openai_chat",
    ))
    eff = svc.get_effective_config("foundry")
    assert eff["CHAT_LLM_PROVIDER"] == "foundry"
    assert eff["CHAT_LLM_MODEL"] == "Kimi-K2.5"
    assert eff["CHAT_LLM_API_KEY"] == "k1"
    assert eff["CHAT_LLM_BASE_URL"].endswith("api-version=v")
    assert eff["CHAT_LLM_FOUNDRY_PROTOCOL"] == "openai_chat"


def test_effective_config_raises_when_url_missing():
    svc, _rows = _service()
    svc.update_provider("foundry", ProviderConfigUpdate(
        model="m", api_key="k", foundry_protocol="anthropic",
    ))
    with pytest.raises(ValueError, match="Target URL"):
        svc.get_effective_config("foundry")


def test_effective_config_raises_when_protocol_missing():
    svc, _rows = _service()
    svc.update_provider("foundry", ProviderConfigUpdate(
        model="m", api_key="k",
        base_url="https://example.com/anthropic/v1/messages",
    ))
    with pytest.raises(ValueError, match="protocol"):
        svc.get_effective_config("foundry")


def test_effective_config_raises_when_api_key_missing():
    svc, _rows = _service()
    svc.update_provider("foundry", ProviderConfigUpdate(
        model="m",
        base_url="https://example.com/anthropic/v1/messages",
        foundry_protocol="anthropic",
    ))
    with pytest.raises(ValueError, match="API key"):
        svc.get_effective_config("foundry")
