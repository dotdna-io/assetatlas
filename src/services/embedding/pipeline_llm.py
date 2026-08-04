# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Pipeline LLM clients for semantic-text generation tasks.

The asset-generation pipeline runs synchronous LLM calls for field-description
generation and synonym (search-term) generation. When an operator has
configured a chatbot LLM (Anthropic, OpenAI, or OpenAI-compatible) we reuse it
rather than maintain a second LLM configuration; otherwise generation is a
no-op via :class:`NullPipelineLLM`.

This module exposes:

* :class:`AnthropicLLM`, :class:`OpenAILLM`, :class:`OpenAICompatLLM` — sync
  adapters (``is_available``, ``generate``, ``close``, plus a
  ``config.base_url`` field for status pages).
* :class:`NullPipelineLLM` — no-op fallback used when no chat provider is
  configured or usable.
* :func:`make_pipeline_llm_client` — picks an adapter based on chat config,
  falling back to :class:`NullPipelineLLM` when chat is not set up.

Failures are surfaced as :class:`LLMConnectionError` / :class:`LLMGenerationError`
so existing call sites (``DescriptionGenerator``, ``LLMSynonymGenerator``)
catch them as before and degrade gracefully instead of crashing the pipeline.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import (TYPE_CHECKING, Any, List, Optional, Protocol,
                     Tuple, runtime_checkable)

if TYPE_CHECKING:
    from src.services.embedding.description_generator import DescriptionGenerator

logger = logging.getLogger(__name__)


def env_max_tokens(default: int = 4096) -> int:
    """Resolve ``EMBEDDING_LLM_MAX_TOKENS`` as an int, falling back to ``default``.

    The description/synonym generators call this at import time; a malformed
    value (e.g. a typo like ``"2k"``) must degrade to the default rather than
    raise ``ValueError`` and break the import of every pipeline LLM call site.
    """
    raw = os.getenv('EMBEDDING_LLM_MAX_TOKENS')
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid EMBEDDING_LLM_MAX_TOKENS=%r; using default %d", raw, default,
        )
        return default


def env_request_timeout(default: float = 120.0) -> float:
    """Resolve ``EMBEDDING_LLM_REQUEST_TIMEOUT`` (seconds) as a float.

    Bounds the wall-clock of a single pipeline LLM request so a hung or
    pathologically-slow provider can't block a background job forever.
    ``max_tokens`` already bounds how *much* a model generates; this bounds how
    *long* the request may take. A malformed value degrades to ``default``
    rather than raising at import (same contract as :func:`env_max_tokens`).
    """
    raw = os.getenv('EMBEDDING_LLM_REQUEST_TIMEOUT')
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid EMBEDDING_LLM_REQUEST_TIMEOUT=%r; using default %s",
            raw, default,
        )
        return default


class LLMClientError(Exception):
    """Base exception for pipeline LLM client errors."""
    pass


class LLMConnectionError(LLMClientError):
    """Raised when connection to the LLM provider fails."""
    pass


class LLMGenerationError(LLMClientError):
    """Raised when text generation fails."""
    pass


@runtime_checkable
class PipelineLLM(Protocol):
    """Common contract for sync pipeline LLM clients.

    Implemented by the configured chat-provider adapters or
    :class:`NullPipelineLLM`. Lets call sites (and the factory's return
    type) speak in terms of behavior rather than concrete classes.
    """

    provider: str
    model: str

    def is_available(self) -> bool: ...

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = ...,
        max_tokens: Optional[int] = ...,
        temperature: Optional[float] = ...,
    ) -> str: ...

    def close(self) -> None: ...


def describe_client(client: Any) -> Tuple[str, str]:
    """Return (provider, model) labels for any pipeline LLM client.

    Tolerant of clients that pre-date the ``provider``/``model`` attrs
    (returns sane defaults). Used for batch-level telemetry.
    """
    return (
        getattr(client, "provider", None) or "none",
        getattr(client, "model", None) or "unknown",
    )


@dataclass
class PipelineLLMConfig:
    """Common config surface for all pipeline LLM adapters.

    ``base_url`` is empty for cloud providers (anthropic, openai); set for
    self-hosted or local servers. The status endpoint reads this for display.
    """

    provider: str
    model: str
    api_key: str = ""
    base_url: str = ""
    timeout: float = 60.0
    max_tokens: int = 512
    temperature: float = 0.05
    top_p: float = 0.9


class _RemoteLLMBase:
    """Shared bits for cloud / OpenAI-compatible adapters."""

    def __init__(self, config: PipelineLLMConfig):
        self.config = config
        self.provider = config.provider
        self.model = config.model

    def is_available(self) -> bool:
        # Cloud providers: assume reachable when an API key is set. Probing
        # costs a token. Generation itself surfaces unreachability via
        # LLMConnectionError, which the call sites already handle.
        return bool(self.config.api_key)

    def close(self) -> None:  # pragma: no cover - SDK clients clean themselves
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(provider={self.provider}, model={self.model})"


class AnthropicLLM(_RemoteLLMBase):
    """Sync Anthropic adapter using ``anthropic.Anthropic.messages.create``."""

    def __init__(self, config: PipelineLLMConfig):
        super().__init__(config)
        from anthropic import Anthropic
        self._sdk = Anthropic(api_key=config.api_key, timeout=config.timeout)

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        from anthropic import APIConnectionError, APIError, APITimeoutError

        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens or self.config.max_tokens,
            "temperature": (
                temperature if temperature is not None else self.config.temperature
            ),
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        try:
            resp = self._sdk.messages.create(**kwargs)
        except (APIConnectionError, APITimeoutError) as e:
            raise LLMConnectionError(f"Anthropic request failed: {e}") from e
        except APIError as e:
            raise LLMGenerationError(f"Anthropic API error: {e}") from e

        try:
            blocks = resp.content or []
            text_parts = [b.text for b in blocks if getattr(b, "type", None) == "text"]
            text = "".join(text_parts).strip()
        except Exception as e:  # defensive: SDK response shape change
            raise LLMGenerationError(f"Failed to parse Anthropic response: {e}") from e

        if not text:
            raise LLMGenerationError("Empty content in Anthropic response")
        return text


class _OpenAIBase(_RemoteLLMBase):
    """Shared OpenAI / OpenAI-compatible chat-completions logic."""

    def __init__(self, config: PipelineLLMConfig, *, base_url: str = ""):
        super().__init__(config)
        from openai import OpenAI
        # OpenAI-compat self-hosted endpoints often accept any non-empty key.
        api_key = config.api_key or "unused"
        kwargs: dict = {"api_key": api_key, "timeout": config.timeout}
        if base_url:
            kwargs["base_url"] = base_url
        self._sdk = OpenAI(**kwargs)

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        from openai import APIConnectionError, APIError, APITimeoutError

        messages: List[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.config.max_tokens,
            "temperature": (
                temperature if temperature is not None else self.config.temperature
            ),
            "top_p": self.config.top_p,
        }

        try:
            resp = self._sdk.chat.completions.create(**kwargs)
        except (APIConnectionError, APITimeoutError) as e:
            raise LLMConnectionError(
                f"{self.provider} request failed: {e}"
            ) from e
        except APIError as e:
            raise LLMGenerationError(f"{self.provider} API error: {e}") from e

        try:
            choices = resp.choices or []
            if not choices:
                raise LLMGenerationError("No choices in response")
            content = (choices[0].message.content or "").strip()
        except LLMGenerationError:
            raise
        except Exception as e:
            raise LLMGenerationError(
                f"Failed to parse {self.provider} response: {e}"
            ) from e

        if not content:
            raise LLMGenerationError(f"Empty content in {self.provider} response")
        return content


class OpenAILLM(_OpenAIBase):
    """Sync OpenAI adapter (api.openai.com)."""


class OpenAICompatLLM(_OpenAIBase):
    """Sync OpenAI-API-compatible adapter for self-hosted endpoints."""

    # is_available() probes the server over HTTP; pipeline call sites can
    # invoke it once per field, so cache the answer briefly to avoid an HTTP
    # storm against a slow endpoint within a single batch.
    _AVAILABILITY_TTL_SECONDS = 30.0

    def __init__(self, config: PipelineLLMConfig):
        if not config.base_url:
            raise ValueError("OpenAICompatLLM requires a base_url")
        super().__init__(config, base_url=config.base_url)
        self._available_until: float = 0.0
        self._available_cached: bool = False

    def is_available(self) -> bool:
        now = time.monotonic()
        if now < self._available_until:
            return self._available_cached
        result = self._probe_models_endpoint()
        self._available_cached = result
        self._available_until = now + self._AVAILABILITY_TTL_SECONDS
        return result

    def _probe_models_endpoint(self) -> bool:
        # We try /models because OpenAI-compatible servers (vLLM, llama.cpp,
        # Ollama, LiteLLM) all expose it. 401/403 means the server is up but
        # auth-protected — that's still "available" for our purposes.
        import requests
        try:
            r = requests.get(
                f"{self.config.base_url.rstrip('/')}/models", timeout=5
            )
            return r.status_code in (200, 401, 403)
        except requests.RequestException:
            return False


class NullPipelineLLM:
    """No-op pipeline LLM used when no chat provider is configured.

    Reports itself unavailable so every call site (DescriptionGenerator,
    LLMSynonymGenerator) takes its existing fallback path; raises on
    generate() should anything call it directly.
    """

    provider = "none"
    model = "none"

    def is_available(self) -> bool:
        return False

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        raise LLMConnectionError("No LLM provider configured")

    def close(self) -> None:  # pragma: no cover - nothing to close
        pass


# Provider → (adapter class, required PipelineLLMConfig fields).
# Each adapter only constructs cleanly when its required fields are set.
_PIPELINE_LLM_BUILDERS: dict = {
    "anthropic":     (AnthropicLLM,    ("api_key", "model")),
    "openai":        (OpenAILLM,       ("api_key", "model")),
    "openai_compat": (OpenAICompatLLM, ("base_url", "model")),
}


def make_pipeline_llm_client(
    chat_providers_service: Optional[Any] = None,
) -> PipelineLLM:
    """Return the LLM client to use for pipeline semantic tasks.

    Selection rules:

    * If the active chat provider has its required fields set (api_key+model
      for cloud providers, base_url+model for openai_compat), use the
      matching sync adapter.
    * Otherwise fall back to :class:`NullPipelineLLM` (reports unavailable;
      raises if ``generate`` is called directly).

    Pass the live ``ChatProvidersService`` from ``app.chat_providers_service``.
    Without one — or without a usable config — we fall back to
    :class:`NullPipelineLLM`.
    """
    if chat_providers_service is None:
        logger.debug("Pipeline LLM: no chat config service, using NullPipelineLLM")
        return NullPipelineLLM()

    try:
        active = chat_providers_service.get_active_provider()
        effective = chat_providers_service.get_effective_config(active)
    except Exception as e:
        # ValueError is the common case: active provider has no api key.
        logger.info(
            "Pipeline LLM: chat config not usable, using NullPipelineLLM: %s", e
        )
        return NullPipelineLLM()

    cfg = PipelineLLMConfig(
        provider=str(effective.get("CHAT_LLM_PROVIDER", "")).lower(),
        model=str(effective.get("CHAT_LLM_MODEL", "")),
        api_key=str(effective.get("CHAT_LLM_API_KEY", "") or ""),
        base_url=str(effective.get("CHAT_LLM_BASE_URL", "") or ""),
        timeout=env_request_timeout(),
    )

    builder = _PIPELINE_LLM_BUILDERS.get(cfg.provider)
    if builder is None or not all(getattr(cfg, field) for field in builder[1]):
        logger.info(
            "Pipeline LLM: chat config incomplete or unsupported "
            "(provider=%r, model set=%s, api_key set=%s, base_url set=%s) "
            "— using NullPipelineLLM",
            cfg.provider, bool(cfg.model), bool(cfg.api_key), bool(cfg.base_url),
        )
        return NullPipelineLLM()

    adapter_cls = builder[0]
    try:
        client = adapter_cls(cfg)
    except Exception as e:
        logger.warning(
            "Pipeline LLM: failed to build %s adapter (%s) — using NullPipelineLLM",
            cfg.provider, e,
        )
        return NullPipelineLLM()

    logger.info(
        "Pipeline LLM: using chat-configured client (provider=%s, model=%s)",
        client.provider, client.model,
    )
    return client


def _db_chat_providers_service(db_adapter):
    """Build a ChatProvidersService straight from a db adapter (no app context).

    Used by the scheduler/CLI path where there is no ``app.chat_providers_service``.
    api-key decryption uses the env key, so no app context is required. Imports
    are function-local to avoid a services->api import cycle.
    """
    from src.api.blueprints.chat.admin_config import (
        ChatProvidersRepository, ChatProvidersService,
    )
    from src.api.modules.settings import SettingsManager
    return ChatProvidersService(
        repo=ChatProvidersRepository(db_adapter),
        settings_manager=SettingsManager(db_adapter.conn_manager),
    )


def build_pipeline_description_generator(
    db_adapter, chat_providers_service=None
) -> DescriptionGenerator:
    """Return a DescriptionGenerator backed by the *currently* configured provider.

    Resolves chat config FRESH — from the supplied service (app context) or a
    DB-backed one built from ``db_adapter`` (scheduler/CLI) — so a provider
    configured after startup is picked up without an API restart, and scheduled
    runs (no app context) can reach the provider too. Yields a generator over
    NullPipelineLLM when nothing is usable.
    """
    from src.services.embedding.description_generator import DescriptionGenerator
    svc = chat_providers_service or _db_chat_providers_service(db_adapter)
    return DescriptionGenerator(llm_client=make_pipeline_llm_client(svc))


__all__ = [
    "PipelineLLM",
    "PipelineLLMConfig",
    "AnthropicLLM",
    "OpenAILLM",
    "OpenAICompatLLM",
    "NullPipelineLLM",
    "describe_client",
    "make_pipeline_llm_client",
    "build_pipeline_description_generator",
    "LLMClientError",
    "LLMConnectionError",
    "LLMGenerationError",
]
