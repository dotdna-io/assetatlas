# SPDX-License-Identifier: AGPL-3.0-or-later
"""
LLM provider clients for the chatbot feature.

:func:`make_llm_client` dispatches on ``CHAT_LLM_PROVIDER`` and returns a
client implementing the LLMClient protocol. The expected input is the dict
produced by :meth:`ChatProvidersService.get_effective_config`. There is no
env-var fallback (per issue #636): the active provider's row in
``chat_providers`` is the only source of truth.

Supported providers:
  anthropic      — first-class, uses prompt caching by default.
  openai         — OpenAI-hosted models (gpt-4o, gpt-4.1, etc.).
  openai_compat  — any OpenAI-API-compatible endpoint (vLLM, Ollama, LiteLLM)
                   reachable via ``CHAT_LLM_BASE_URL``. Probes tool support on
                   first use and falls back to prompt-based tool dispatch
                   when the server rejects ``tools``.
  aws_bedrock    — Anthropic / Llama / Mistral / Cohere via Amazon Bedrock.
                   Auth via access keys or assume_role.
  foundry        — Microsoft Foundry pass-through. Dispatches on
                   ``CHAT_LLM_FOUNDRY_PROTOCOL`` (one of ``anthropic``,
                   ``openai_chat``, ``openai_responses``) to the matching
                   client; URL parsing in ``foundry_url.py``.
"""

from __future__ import annotations

from typing import Any, Mapping

import boto3
from anthropic import AsyncAnthropic

from .anthropic_client import AnthropicClient
from .bedrock_client import BedrockClient
from .base import LLMChunk, LLMClient, LLMMessage, StreamResult, ToolSpec, Usage
from .openai_client import OpenAIClient
from .openai_compat_client import OpenAICompatClient

__all__ = [
    "make_llm_client",
    "AnthropicClient",
    "BedrockClient",
    "OpenAIClient",
    "OpenAICompatClient",
    "LLMChunk",
    "LLMClient",
    "LLMMessage",
    "StreamResult",
    "ToolSpec",
    "Usage",
]


def _normalize_openai_compat_base_url(url: str) -> str:
    """Strip a trailing ``/chat/completions`` from an OpenAI-compatible base URL.

    The openai SDK appends ``chat/completions`` to ``base_url`` itself, so an
    admin who pastes the full endpoint (``…/v1/chat/completions`` — the shape
    most vLLM/Ollama/LiteLLM curl examples show) would otherwise get a doubled
    path and a 404 on every request. Removing the suffix (and any surrounding
    whitespace / trailing slash) makes both the bare base and the full-endpoint
    paste work. The missing-``/v1`` case is left alone — it isn't safe to
    guess a prefix, since not every compat server uses one.
    """
    trimmed = url.strip().rstrip("/")
    suffix = "/chat/completions"
    if trimmed.endswith(suffix):
        trimmed = trimmed[: -len(suffix)]
    return trimmed


def make_llm_client(config: Mapping[str, Any]) -> LLMClient:
    """Construct an LLM client from a ``CHAT_LLM_*`` config dict.

    Raises ``ValueError`` when ``CHAT_LLM_PROVIDER`` is missing/unsupported,
    when the api key is empty for any provider, or when ``openai_compat`` is
    requested without ``CHAT_LLM_BASE_URL``.
    """
    provider = str(config.get("CHAT_LLM_PROVIDER", "")).lower()
    model = str(config.get("CHAT_LLM_MODEL", ""))
    api_key = str(config.get("CHAT_LLM_API_KEY", ""))

    # Self-hosted vLLM/Ollama deployments don't need a key — only the hosted
    # providers do. The OpenAI SDK still insists on a non-empty header so
    # ``openai_compat`` substitutes a sentinel just below.
    if provider in ("anthropic", "openai") and not api_key:
        raise ValueError(
            f"Provider {provider!r} has no API key configured."
        )

    cache_raw = config.get("CHAT_PROMPT_CACHE", True)
    cache = cache_raw if isinstance(cache_raw, bool) \
        else (isinstance(cache_raw, str) and cache_raw.lower() == "true")

    if provider == "anthropic":
        sdk = AsyncAnthropic(api_key=api_key)
        return AnthropicClient(sdk=sdk, model=model, prompt_cache=cache)

    if provider == "openai":
        from openai import AsyncOpenAI
        sdk = AsyncOpenAI(api_key=api_key)
        return OpenAIClient(sdk=sdk, model=model, prompt_cache=cache)

    if provider == "openai_compat":
        base_url = _normalize_openai_compat_base_url(
            str(config.get("CHAT_LLM_BASE_URL", ""))
        )
        if not base_url:
            raise ValueError(
                "openai_compat requires a base URL — set Base URL on the "
                "OpenAI-compatible card."
            )
        thinking_raw = config.get("CHAT_LLM_THINKING_ENABLED", True)
        thinking_enabled = thinking_raw if isinstance(thinking_raw, bool) \
            else str(thinking_raw).lower() == "true"
        from openai import AsyncOpenAI
        sdk = AsyncOpenAI(api_key=api_key or "unused", base_url=base_url)
        return OpenAICompatClient(
            sdk=sdk, model=model, base_url=base_url,
            prompt_cache=cache, thinking_enabled=thinking_enabled,
        )

    if provider == "foundry":
        from .foundry_url import parse_foundry_url
        foundry_protocol = str(config.get("CHAT_LLM_FOUNDRY_PROTOCOL", ""))
        target_url = str(config.get("CHAT_LLM_BASE_URL", ""))
        if not target_url:
            raise ValueError("Microsoft Foundry requires a Target URL.")
        if not api_key:
            raise ValueError("Microsoft Foundry requires an API key.")
        base_url, api_version = parse_foundry_url(target_url, foundry_protocol)

        if foundry_protocol == "anthropic":
            sdk = AsyncAnthropic(api_key=api_key, base_url=base_url)
            # prompt_cache disabled by default for Foundry — Azure's Anthropic
            # proxy may reject `cache_control: ephemeral` blocks. If Foundry
            # adds support, flip this back to `cache`.
            return AnthropicClient(sdk=sdk, model=model, prompt_cache=False)

        if foundry_protocol == "openai_chat":
            from openai import AsyncOpenAI
            default_query = (
                {"api-version": api_version} if api_version else None
            )
            sdk = AsyncOpenAI(
                api_key=api_key, base_url=base_url,
                default_query=default_query,
                # Classic Azure endpoints (…?api-version=…) authenticate via the
                # `api-key` header, not the `Authorization: Bearer` the SDK
                # sends from api_key=. The v1 surface accepts api-key too, so
                # send it on both URL shapes to avoid a 401 on classic.
                default_headers={"api-key": api_key},
            )
            # Plain OpenAIClient — Foundry chat.completions speaks the
            # schema natively, no tools probe needed (unlike vLLM/Ollama
            # via OpenAICompatClient).
            return OpenAIClient(sdk=sdk, model=model, prompt_cache=False)

        if foundry_protocol == "openai_responses":
            from openai import AsyncOpenAI
            from .foundry_responses_client import FoundryResponsesClient
            default_query = (
                {"api-version": api_version} if api_version else None
            )
            sdk = AsyncOpenAI(
                api_key=api_key, base_url=base_url,
                default_query=default_query,
                # See openai_chat above: Azure's classic surface needs the
                # api-key header, and the v1 surface accepts it too.
                default_headers={"api-key": api_key},
            )
            return FoundryResponsesClient(sdk=sdk, model=model)

        raise ValueError(
            f"Unknown Microsoft Foundry protocol {foundry_protocol!r}"
        )

    if provider == "aws_bedrock":
        return _make_bedrock_client(config, model=model, cache=cache)

    raise ValueError(f"Unknown CHAT_LLM_PROVIDER={provider!r}")


def _make_bedrock_client(
    config: Mapping[str, Any], *, model: str, cache: bool,
) -> BedrockClient:
    """Resolve AWS credentials per the configured auth_mode and return a
    BedrockClient. Raises ``ValueError`` with an actionable message on any
    misconfiguration so /test surfaces a clear hint instead of a 500."""
    region = str(config.get("CHAT_LLM_AWS_REGION", "")).strip()
    if not region:
        raise ValueError("AWS region is required for the AWS Bedrock provider.")

    auth = config.get("CHAT_LLM_AWS_AUTH") or {}
    if not isinstance(auth, dict):
        raise ValueError("CHAT_LLM_AWS_AUTH must be a dict (parsed JSON blob).")

    from .aws_credentials import resolve_aws_credentials
    client_kwargs = resolve_aws_credentials(
        region, auth=auth, session_name_prefix="assetatlas-chat",
    )

    thinking_raw = config.get("CHAT_LLM_THINKING_ENABLED", False)
    thinking = thinking_raw if isinstance(thinking_raw, bool) \
        else str(thinking_raw).lower() == "true"

    runtime = boto3.client("bedrock-runtime", **client_kwargs)
    return BedrockClient(
        runtime=runtime, model=model,
        prompt_cache=cache, thinking_enabled=thinking,
    )
