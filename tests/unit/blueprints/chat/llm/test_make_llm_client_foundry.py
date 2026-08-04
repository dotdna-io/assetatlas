# SPDX-License-Identifier: AGPL-3.0-or-later
"""make_llm_client dispatch tests for the foundry provider.

These cover URL parsing, SDK kwargs, and the protocol→client mapping. The
SDK constructors themselves are patched at the module level so we can assert
on the kwargs we pass without doing any network I/O.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.api.blueprints.chat.llm import make_llm_client
from src.api.blueprints.chat.llm.anthropic_client import AnthropicClient


_BASE = {
    "CHAT_LLM_PROVIDER": "foundry",
    "CHAT_LLM_API_KEY": "key-abc",
    "CHAT_PROMPT_CACHE": True,
    "CHAT_LLM_THINKING_ENABLED": False,
}


def test_foundry_anthropic_protocol_builds_anthropic_client_with_base_url():
    cfg = {
        **_BASE,
        "CHAT_LLM_MODEL": "claude-sonnet-4-6",
        "CHAT_LLM_BASE_URL": (
            "https://securityfoundry.services.ai.azure.com/anthropic/v1/messages"
        ),
        "CHAT_LLM_FOUNDRY_PROTOCOL": "anthropic",
    }
    with patch(
        "src.api.blueprints.chat.llm.AsyncAnthropic"
    ) as anth_ctor:
        client = make_llm_client(cfg)

    assert isinstance(client, AnthropicClient)
    anth_ctor.assert_called_once()
    kwargs = anth_ctor.call_args.kwargs
    assert kwargs["api_key"] == "key-abc"
    assert kwargs["base_url"] == (
        "https://securityfoundry.services.ai.azure.com/anthropic"
    )
    # prompt_cache is hardcoded False for foundry/anthropic — Azure's proxy
    # may reject cache_control blocks.
    assert client._prompt_cache is False
    assert client.model == "claude-sonnet-4-6"


def test_foundry_anthropic_missing_url_raises():
    cfg = {
        **_BASE,
        "CHAT_LLM_MODEL": "claude-sonnet-4-6",
        "CHAT_LLM_BASE_URL": "",
        "CHAT_LLM_FOUNDRY_PROTOCOL": "anthropic",
    }
    with pytest.raises(ValueError, match="Target URL"):
        make_llm_client(cfg)


def test_foundry_missing_api_key_raises():
    cfg = {
        **_BASE,
        "CHAT_LLM_API_KEY": "",
        "CHAT_LLM_MODEL": "claude-sonnet-4-6",
        "CHAT_LLM_BASE_URL": "https://example.com/anthropic/v1/messages",
        "CHAT_LLM_FOUNDRY_PROTOCOL": "anthropic",
    }
    with pytest.raises(ValueError, match="API key"):
        make_llm_client(cfg)


def test_foundry_unknown_protocol_raises():
    cfg = {
        **_BASE,
        "CHAT_LLM_MODEL": "x",
        "CHAT_LLM_BASE_URL": "https://example.com/anthropic/v1/messages",
        "CHAT_LLM_FOUNDRY_PROTOCOL": "garbage",
    }
    with pytest.raises(ValueError, match="protocol"):
        make_llm_client(cfg)


from src.api.blueprints.chat.llm.openai_client import OpenAIClient


def test_foundry_openai_chat_protocol_builds_openai_client_with_default_query():
    cfg = {
        **_BASE,
        "CHAT_LLM_MODEL": "Kimi-K2.5",
        "CHAT_LLM_BASE_URL": (
            "https://securityfoundry.services.ai.azure.com/models/chat/completions"
            "?api-version=2024-05-01-preview"
        ),
        "CHAT_LLM_FOUNDRY_PROTOCOL": "openai_chat",
    }
    with patch("openai.AsyncOpenAI") as oai_ctor:
        client = make_llm_client(cfg)

    assert isinstance(client, OpenAIClient)
    oai_ctor.assert_called_once()
    kwargs = oai_ctor.call_args.kwargs
    assert kwargs["api_key"] == "key-abc"
    assert kwargs["base_url"] == (
        "https://securityfoundry.services.ai.azure.com/models"
    )
    assert kwargs["default_query"] == {"api-version": "2024-05-01-preview"}
    # Foundry chat.completions uses the plain OpenAIClient (not the
    # OpenAICompatClient probe path — Foundry speaks the schema natively).
    from src.api.blueprints.chat.llm.openai_compat_client import (
        OpenAICompatClient,
    )
    assert not isinstance(client, OpenAICompatClient)
    assert client.model == "Kimi-K2.5"


def test_foundry_openai_chat_v1_base_url_builds_client_without_api_version():
    # Azure AI Foundry's v1 API hands the operator a bare base URL with no
    # /chat/completions suffix and no api-version; the SDK appends the suffix
    # itself. make_llm_client must build the client (default_query=None) rather
    # than reject it — regression for the v1 endpoint failing the connection
    # test with "Target URL path must end with '/chat/completions'".
    cfg = {
        **_BASE,
        "CHAT_LLM_MODEL": "ocs-grok-4.3",
        "CHAT_LLM_BASE_URL": (
            "https://ocs-foundry-01.services.ai.azure.com/openai/v1"
        ),
        "CHAT_LLM_FOUNDRY_PROTOCOL": "openai_chat",
    }
    with patch("openai.AsyncOpenAI") as oai_ctor:
        client = make_llm_client(cfg)

    assert isinstance(client, OpenAIClient)
    kwargs = oai_ctor.call_args.kwargs
    assert kwargs["base_url"] == (
        "https://ocs-foundry-01.services.ai.azure.com/openai/v1"
    )
    assert kwargs["default_query"] is None


def test_foundry_openai_chat_sends_azure_api_key_header():
    # Classic Azure endpoints (…?api-version=…) authenticate via the `api-key`
    # header, not `Authorization: Bearer` (which AsyncOpenAI sends by default),
    # so a valid key would otherwise 401. The v1 surface accepts api-key too,
    # so we send it on both URL shapes.
    cfg = {
        **_BASE,
        "CHAT_LLM_MODEL": "gpt-4o",
        "CHAT_LLM_BASE_URL": (
            "https://securityfoundry.services.ai.azure.com/models/chat/completions"
            "?api-version=2024-05-01-preview"
        ),
        "CHAT_LLM_FOUNDRY_PROTOCOL": "openai_chat",
    }
    with patch("openai.AsyncOpenAI") as oai_ctor:
        make_llm_client(cfg)
    assert oai_ctor.call_args.kwargs["default_headers"] == {"api-key": "key-abc"}


def test_foundry_openai_responses_sends_azure_api_key_header():
    cfg = {
        **_BASE,
        "CHAT_LLM_MODEL": "gpt-5-mini",
        "CHAT_LLM_BASE_URL": (
            "https://securityfoundry.cognitiveservices.azure.com/openai/responses"
            "?api-version=2025-04-01-preview"
        ),
        "CHAT_LLM_FOUNDRY_PROTOCOL": "openai_responses",
    }
    with patch("openai.AsyncOpenAI") as oai_ctor:
        make_llm_client(cfg)
    assert oai_ctor.call_args.kwargs["default_headers"] == {"api-key": "key-abc"}


def test_foundry_openai_responses_protocol_builds_responses_client():
    from src.api.blueprints.chat.llm.foundry_responses_client import (
        FoundryResponsesClient,
    )
    cfg = {
        **_BASE,
        "CHAT_LLM_MODEL": "gpt-5.4-mini",
        "CHAT_LLM_BASE_URL": (
            "https://securityfoundry.cognitiveservices.azure.com/openai/responses"
            "?api-version=2025-04-01-preview"
        ),
        "CHAT_LLM_FOUNDRY_PROTOCOL": "openai_responses",
    }
    with patch("openai.AsyncOpenAI") as oai_ctor:
        client = make_llm_client(cfg)

    assert isinstance(client, FoundryResponsesClient)
    oai_ctor.assert_called_once()
    kwargs = oai_ctor.call_args.kwargs
    assert kwargs["api_key"] == "key-abc"
    assert kwargs["base_url"] == (
        "https://securityfoundry.cognitiveservices.azure.com/openai"
    )
    assert kwargs["default_query"] == {"api-version": "2025-04-01-preview"}
    assert client.model == "gpt-5.4-mini"
