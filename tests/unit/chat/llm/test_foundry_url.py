# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Foundry URL parser.

Three Foundry protocols, three URL shapes. The parser strips the SDK-appended
suffix (so SDK + base_url reconstructs the original request URL) and lifts
``?api-version=…`` out of the query string. Other query params are preserved.
"""

from __future__ import annotations

import pytest

from src.api.blueprints.chat.llm.foundry_url import parse_foundry_url


def test_anthropic_url_strips_messages_suffix():
    url = "https://securityfoundry.services.ai.azure.com/anthropic/v1/messages"
    base, version = parse_foundry_url(url, "anthropic")
    assert base == "https://securityfoundry.services.ai.azure.com/anthropic"
    assert version is None


def test_openai_chat_url_strips_suffix_and_lifts_api_version():
    url = (
        "https://securityfoundry.services.ai.azure.com/models/chat/completions"
        "?api-version=2024-05-01-preview"
    )
    base, version = parse_foundry_url(url, "openai_chat")
    assert base == "https://securityfoundry.services.ai.azure.com/models"
    assert version == "2024-05-01-preview"


def test_openai_responses_url_strips_suffix_and_lifts_api_version():
    url = (
        "https://securityfoundry.cognitiveservices.azure.com/openai/responses"
        "?api-version=2025-04-01-preview"
    )
    base, version = parse_foundry_url(url, "openai_responses")
    assert base == "https://securityfoundry.cognitiveservices.azure.com/openai"
    assert version == "2025-04-01-preview"


def test_trailing_slash_is_stripped_from_base():
    url = "https://example.com/path/anthropic/v1/messages"
    base, _ = parse_foundry_url(url, "anthropic")
    assert base == "https://example.com/path/anthropic"


def test_other_query_params_are_preserved_on_base():
    url = (
        "https://example.com/models/chat/completions"
        "?api-version=2024-05-01-preview&deployment=my-dep"
    )
    base, version = parse_foundry_url(url, "openai_chat")
    assert version == "2024-05-01-preview"
    assert "deployment=my-dep" in base
    assert "api-version" not in base


def test_url_with_no_scheme_raises_value_error():
    with pytest.raises(ValueError, match="http"):
        parse_foundry_url("example.com/anthropic/v1/messages", "anthropic")


def test_url_with_ftp_scheme_raises_value_error():
    with pytest.raises(ValueError, match="http"):
        parse_foundry_url("ftp://example.com/anthropic/v1/messages", "anthropic")


def test_wrong_suffix_for_anthropic_protocol_raises():
    with pytest.raises(ValueError, match="/v1/messages"):
        parse_foundry_url(
            "https://example.com/models/chat/completions?api-version=v",
            "anthropic",
        )


def test_wrong_suffix_for_openai_chat_protocol_raises():
    with pytest.raises(ValueError, match="/chat/completions"):
        parse_foundry_url(
            "https://example.com/anthropic/v1/messages",
            "openai_chat",
        )


def test_wrong_suffix_for_openai_responses_protocol_raises():
    with pytest.raises(ValueError, match="/responses"):
        parse_foundry_url(
            "https://example.com/anthropic/v1/messages",
            "openai_responses",
        )


def test_openai_chat_api_version_optional():
    # New Foundry v1 API dropped the required api-version; a full request URL
    # without it must now parse rather than raise.
    base, version = parse_foundry_url(
        "https://example.com/models/chat/completions",
        "openai_chat",
    )
    assert base == "https://example.com/models"
    assert version is None


def test_openai_responses_api_version_optional():
    base, version = parse_foundry_url(
        "https://example.com/openai/responses",
        "openai_responses",
    )
    assert base == "https://example.com/openai"
    assert version is None


def test_openai_chat_v1_base_url_without_suffix():
    # Azure AI Foundry v1 hands out the bare base URL; the openai SDK appends
    # /chat/completions itself, so the parser must accept it unchanged.
    url = "https://ocs-foundry-01.services.ai.azure.com/openai/v1"
    base, version = parse_foundry_url(url, "openai_chat")
    assert base == "https://ocs-foundry-01.services.ai.azure.com/openai/v1"
    assert version is None


def test_openai_responses_v1_base_url_without_suffix():
    url = "https://ocs-foundry-01.services.ai.azure.com/openai/v1"
    base, version = parse_foundry_url(url, "openai_responses")
    assert base == "https://ocs-foundry-01.services.ai.azure.com/openai/v1"
    assert version is None


def test_anthropic_v1_base_url_without_suffix():
    url = "https://securityfoundry.services.ai.azure.com/anthropic"
    base, version = parse_foundry_url(url, "anthropic")
    assert base == "https://securityfoundry.services.ai.azure.com/anthropic"
    assert version is None


def test_v1_base_url_lifts_preview_api_version():
    # v1 preview features still travel as ?api-version=preview on the base URL.
    url = (
        "https://ocs-foundry-01.services.ai.azure.com/openai/v1"
        "?api-version=preview"
    )
    base, version = parse_foundry_url(url, "openai_chat")
    assert base == "https://ocs-foundry-01.services.ai.azure.com/openai/v1"
    assert version == "preview"


def test_anthropic_api_version_optional():
    url = "https://example.com/anthropic/v1/messages?api-version=2024"
    base, version = parse_foundry_url(url, "anthropic")
    assert base == "https://example.com/anthropic"
    assert version == "2024"
