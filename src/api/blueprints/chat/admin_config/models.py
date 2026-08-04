# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pydantic models for the admin chat-config API."""

from __future__ import annotations

from typing import Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict


ChatProvider = Literal["anthropic", "openai", "openai_compat", "aws_bedrock", "foundry"]

# Order matters for the UI: it iterates this tuple to render cards.
CHAT_PROVIDERS: tuple[ChatProvider, ...] = (
    "anthropic", "openai", "openai_compat", "aws_bedrock", "foundry",
)

# AWS Bedrock auth mode — selected per-row, governs which secret fields apply.
BedrockAuthMode = Literal["access_keys", "assume_role"]

# Foundry protocol — selected per-row on the foundry card, picks which client
# (and which SDK call shape) make_llm_client constructs.
FoundryProtocol = Literal["anthropic", "openai_chat", "openai_responses"]


class ProviderConfig(BaseModel):
    """One provider's config as returned to the admin UI.

    The raw API key never leaves the backend — `api_key_preview` is a masked
    last-4 view, and `api_key_configured` is the boolean the UI actually
    branches on.

    Bedrock-only fields (`aws_region`, `auth_mode`, `access_key_id_preview`,
    `role_arn`) are populated only for the `aws_bedrock` row; the other
    providers leave them at their empty defaults so the DTO stays uniform.
    """

    provider: ChatProvider
    model: str
    api_key_preview: str
    api_key_configured: bool
    base_url: str
    prompt_cache: bool
    thinking_enabled: bool
    # Bedrock-only — see admin_config/service.py for population logic.
    aws_region: str = ""
    auth_mode: Optional[BedrockAuthMode] = None
    access_key_id_preview: str = ""
    role_arn: str = ""
    external_id: str = ""
    base_access_key_id_preview: str = ""
    # Foundry-only — empty/None for non-foundry rows.
    foundry_protocol: Optional[FoundryProtocol] = None


class ProviderConfigUpdate(BaseModel):
    """Partial-update payload for one provider. Omitted fields stay as-is.

    `api_key` is plaintext from the UI; the service encrypts it before
    persistence. Empty string explicitly clears the stored key.

    The `aws_*` fields apply only to the `aws_bedrock` provider; sending them
    on another provider is a no-op (the service ignores them for non-Bedrock
    rows)."""
    model_config = ConfigDict(extra="forbid")

    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    prompt_cache: Optional[bool] = None
    thinking_enabled: Optional[bool] = None
    # Bedrock-only.
    aws_region: Optional[str] = None
    auth_mode: Optional[BedrockAuthMode] = None
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    role_arn: Optional[str] = None
    external_id: Optional[str] = None
    base_access_key_id: Optional[str] = None
    base_secret_access_key: Optional[str] = None
    # Foundry-only.
    foundry_protocol: Optional[FoundryProtocol] = None


class AdminChatConfig(BaseModel):
    """Full admin response: which provider is active plus all four provider
    configs. ``active_provider`` is None when the admin has disabled every
    provider (chat turned off)."""

    active_provider: Optional[ChatProvider]
    chat_enabled: bool
    providers: Dict[ChatProvider, ProviderConfig]


class AdminChatConfigUpdate(BaseModel):
    """PATCH body — change the active provider, or update one or more
    providers' configs, or both. Empty bodies are a no-op."""
    model_config = ConfigDict(extra="forbid")

    active_provider: Optional[ChatProvider] = None
    providers: Optional[Dict[ChatProvider, ProviderConfigUpdate]] = None


class AdminChatTestRequest(BaseModel):
    """POST /test body — pick a provider and overlay any unsaved fields."""
    model_config = ConfigDict(extra="forbid")

    provider: ChatProvider
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    prompt_cache: Optional[bool] = None
    thinking_enabled: Optional[bool] = None
    # Bedrock-only.
    aws_region: Optional[str] = None
    auth_mode: Optional[BedrockAuthMode] = None
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    role_arn: Optional[str] = None
    external_id: Optional[str] = None
    base_access_key_id: Optional[str] = None
    base_secret_access_key: Optional[str] = None
    # Foundry-only.
    foundry_protocol: Optional[FoundryProtocol] = None
