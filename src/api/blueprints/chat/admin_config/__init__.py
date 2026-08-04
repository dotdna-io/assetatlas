# SPDX-License-Identifier: AGPL-3.0-or-later
"""Admin chat-config module — per-provider configuration storage.

Splits the formerly single-row chat config into one row per provider
(anthropic / openai / openai_compat / aws_bedrock) plus a single
``chat.active_provider`` setting that points at the row in use. UI is the only source of truth;
there is no ``.env`` fallback for chat config (see issue #636).
"""

from .models import (AdminChatConfig, AdminChatConfigUpdate, ChatProvider,
                     CHAT_PROVIDERS, ProviderConfig, ProviderConfigUpdate)
from .repository import ChatProvidersRepository, ProviderRow
from .service import ChatProvidersService

__all__ = [
    "AdminChatConfig",
    "AdminChatConfigUpdate",
    "ChatProvider",
    "CHAT_PROVIDERS",
    "ChatProvidersRepository",
    "ChatProvidersService",
    "ProviderConfig",
    "ProviderConfigUpdate",
    "ProviderRow",
]
