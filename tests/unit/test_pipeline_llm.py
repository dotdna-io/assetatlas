# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the pipeline-LLM adapters and the make_pipeline_llm_client factory.

Covers:
* Adapter interface parity with the ``NullPipelineLLM`` no-op fallback
  (``is_available``, ``generate``, ``provider``, ``model``, ``config.base_url``).
* Each adapter's ``generate`` happy path and graceful failure on SDK errors.
* The factory's selection rules: configured chat → matching adapter; missing
  fields → fall back to ``NullPipelineLLM``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.services.embedding.pipeline_llm import (
    AnthropicLLM, LLMConnectionError, LLMGenerationError, NullPipelineLLM,
    OpenAICompatLLM, OpenAILLM, PipelineLLM, PipelineLLMConfig,
    build_pipeline_description_generator, describe_client, make_pipeline_llm_client,
)


# ---------------------------------------------------------------------------
# AnthropicLLM
# ---------------------------------------------------------------------------

class _FakeAnthropicBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeAnthropicResponse:
    def __init__(self, text: str):
        self.content = [_FakeAnthropicBlock(text)]


def _make_anthropic_llm(create_return=None, create_side_effect=None) -> AnthropicLLM:
    cfg = PipelineLLMConfig(provider="anthropic", model="claude-test", api_key="sk-x")
    with patch("anthropic.Anthropic") as sdk_cls:
        instance = MagicMock()
        if create_side_effect is not None:
            instance.messages.create.side_effect = create_side_effect
        else:
            instance.messages.create.return_value = create_return
        sdk_cls.return_value = instance
        client = AnthropicLLM(cfg)
    client._sdk = instance
    return client


def test_anthropic_generate_returns_text():
    client = _make_anthropic_llm(_FakeAnthropicResponse("hostname - computer name."))
    out = client.generate("Describe hostname", system_prompt="be concise")
    assert out == "hostname - computer name."
    call_kwargs = client._sdk.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-test"
    assert call_kwargs["system"] == "be concise"
    assert call_kwargs["messages"] == [{"role": "user", "content": "Describe hostname"}]


def test_anthropic_generate_raises_connection_error_on_timeout():
    from anthropic import APITimeoutError
    err = APITimeoutError(request=MagicMock())
    client = _make_anthropic_llm(create_side_effect=err)
    with pytest.raises(LLMConnectionError):
        client.generate("Describe hostname")


def test_anthropic_generate_empty_content_raises():
    client = _make_anthropic_llm(_FakeAnthropicResponse(""))
    with pytest.raises(LLMGenerationError):
        client.generate("Describe hostname")


def test_anthropic_provider_and_model_attributes():
    client = _make_anthropic_llm(_FakeAnthropicResponse("x"))
    assert client.provider == "anthropic"
    assert client.model == "claude-test"
    assert client.config.base_url == ""  # cloud provider — no URL


def test_anthropic_is_available_requires_api_key():
    cfg = PipelineLLMConfig(provider="anthropic", model="m", api_key="")
    with patch("anthropic.Anthropic"):
        client = AnthropicLLM(cfg)
    assert client.is_available() is False


# ---------------------------------------------------------------------------
# OpenAILLM / OpenAICompatLLM
# ---------------------------------------------------------------------------

class _FakeOpenAIChoice:
    def __init__(self, content: str):
        self.message = MagicMock()
        self.message.content = content


class _FakeOpenAIResponse:
    def __init__(self, content: str):
        self.choices = [_FakeOpenAIChoice(content)]


def _make_openai_llm(create_return=None, create_side_effect=None,
                     compat_url: str | None = None) -> OpenAILLM:
    cfg = PipelineLLMConfig(
        provider="openai_compat" if compat_url else "openai",
        model="gpt-test",
        api_key="sk-x",
        base_url=compat_url or "",
    )
    with patch("openai.OpenAI") as sdk_cls:
        instance = MagicMock()
        if create_side_effect is not None:
            instance.chat.completions.create.side_effect = create_side_effect
        else:
            instance.chat.completions.create.return_value = create_return
        sdk_cls.return_value = instance
        client = (OpenAICompatLLM(cfg) if compat_url else OpenAILLM(cfg))
    client._sdk = instance
    return client


def test_openai_generate_returns_content():
    client = _make_openai_llm(_FakeOpenAIResponse("ok"))
    out = client.generate("hi", system_prompt="be terse")
    assert out == "ok"
    call_kwargs = client._sdk.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-test"
    assert call_kwargs["messages"][0] == {"role": "system", "content": "be terse"}
    assert call_kwargs["messages"][1] == {"role": "user", "content": "hi"}


def test_openai_generate_timeout_maps_to_connection_error():
    from openai import APITimeoutError
    err = APITimeoutError(request=MagicMock())
    client = _make_openai_llm(create_side_effect=err)
    with pytest.raises(LLMConnectionError):
        client.generate("hi")


def test_openai_compat_requires_base_url():
    cfg = PipelineLLMConfig(provider="openai_compat", model="m", api_key="x")
    with pytest.raises(ValueError, match="base_url"):
        OpenAICompatLLM(cfg)


def test_openai_compat_exposes_base_url():
    client = _make_openai_llm(_FakeOpenAIResponse("ok"),
                              compat_url="http://vllm.test/v1")
    assert client.config.base_url == "http://vllm.test/v1"
    assert client.provider == "openai_compat"


def test_openai_is_available_requires_api_key():
    cfg = PipelineLLMConfig(provider="openai", model="m", api_key="")
    with patch("openai.OpenAI"):
        client = OpenAILLM(cfg)
    assert client.is_available() is False

    cfg.api_key = "sk-real"
    with patch("openai.OpenAI"):
        client = OpenAILLM(cfg)
    assert client.is_available() is True


def _fresh_compat(base_url: str = "http://vllm.test/v1") -> OpenAICompatLLM:
    cfg = PipelineLLMConfig(
        provider="openai_compat", model="m", base_url=base_url,
    )
    with patch("openai.OpenAI"):
        return OpenAICompatLLM(cfg)


def test_openai_compat_is_available_probes_models_endpoint():
    """OpenAI-compat servers (vLLM, Ollama, llama.cpp, LiteLLM) all expose
    /models. is_available() should accept 200/401/403 (server is up but may
    require auth) and return False on connection errors. Each scenario uses
    a fresh client to bypass the per-instance availability cache."""
    import requests as _requests

    # 200 → up
    client = _fresh_compat()
    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        assert client.is_available() is True
        mock_get.assert_called_once_with(
            "http://vllm.test/v1/models", timeout=5,
        )

    # 401 → up but auth-protected (still "available")
    client = _fresh_compat()
    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=401)
        assert client.is_available() is True

    # 500 → not available
    client = _fresh_compat()
    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=500)
        assert client.is_available() is False

    # Connection error → not available
    client = _fresh_compat()
    with patch("requests.get", side_effect=_requests.ConnectionError("nope")):
        assert client.is_available() is False


def test_openai_compat_caches_is_available_for_ttl():
    """Hot loops (synonym generation iterates per-field) must not re-probe
    a slow self-hosted endpoint on every call."""
    client = _fresh_compat()
    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        for _ in range(5):
            assert client.is_available() is True
        # All calls within TTL → only one HTTP probe.
        assert mock_get.call_count == 1


def test_openai_compat_re_probes_after_ttl_expires():
    client = _fresh_compat()
    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        client.is_available()
        # Force TTL expiry.
        client._available_until = 0.0
        client.is_available()
        assert mock_get.call_count == 2


def test_openai_compat_is_available_strips_trailing_slash():
    cfg = PipelineLLMConfig(
        provider="openai_compat", model="m", base_url="http://vllm.test/v1/",
    )
    with patch("openai.OpenAI"):
        client = OpenAICompatLLM(cfg)
    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        client.is_available()
        mock_get.assert_called_once_with(
            "http://vllm.test/v1/models", timeout=5,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class _FakeChatProvidersService:
    """Stand-in for ChatProvidersService that returns canned configs."""

    def __init__(self, *, active="anthropic", effective=None,
                 effective_raises=False):
        self._active = active
        self._effective = effective or {}
        self._effective_raises = effective_raises

    def get_active_provider(self):
        return self._active

    def get_effective_config(self, provider):
        if self._effective_raises:
            raise ValueError(f"no api key for {provider}")
        return dict(self._effective)


def test_factory_returns_null_when_no_service():
    client = make_pipeline_llm_client(None)
    assert isinstance(client, NullPipelineLLM)


def test_factory_returns_anthropic_when_chat_configured():
    svc = _FakeChatProvidersService(active="anthropic", effective={
        "CHAT_LLM_PROVIDER": "anthropic",
        "CHAT_LLM_MODEL": "claude-sonnet-4-6",
        "CHAT_LLM_API_KEY": "sk-test",
        "CHAT_LLM_BASE_URL": "",
    })
    with patch("anthropic.Anthropic"):
        client = make_pipeline_llm_client(svc)
    assert isinstance(client, AnthropicLLM)
    assert client.model == "claude-sonnet-4-6"


def test_factory_returns_openai_when_chat_configured():
    svc = _FakeChatProvidersService(active="openai", effective={
        "CHAT_LLM_PROVIDER": "openai",
        "CHAT_LLM_MODEL": "gpt-4o-mini",
        "CHAT_LLM_API_KEY": "sk-test",
        "CHAT_LLM_BASE_URL": "",
    })
    with patch("openai.OpenAI"):
        client = make_pipeline_llm_client(svc)
    assert isinstance(client, OpenAILLM)


def test_factory_returns_openai_compat_when_chat_configured():
    svc = _FakeChatProvidersService(active="openai_compat", effective={
        "CHAT_LLM_PROVIDER": "openai_compat",
        "CHAT_LLM_MODEL": "qwen3",
        "CHAT_LLM_API_KEY": "",
        "CHAT_LLM_BASE_URL": "http://vllm.test/v1",
    })
    with patch("openai.OpenAI"):
        client = make_pipeline_llm_client(svc)
    assert isinstance(client, OpenAICompatLLM)


def test_factory_falls_back_when_api_key_missing():
    svc = _FakeChatProvidersService(effective_raises=True)
    client = make_pipeline_llm_client(svc)
    assert isinstance(client, NullPipelineLLM)


def test_factory_falls_back_when_provider_unsupported():
    svc = _FakeChatProvidersService(active="mystery-model", effective={
        "CHAT_LLM_PROVIDER": "mystery-model",
        "CHAT_LLM_API_KEY": "sk-test",
        "CHAT_LLM_MODEL": "",
        "CHAT_LLM_BASE_URL": "",
    })
    client = make_pipeline_llm_client(svc)
    assert isinstance(client, NullPipelineLLM)


def test_factory_falls_back_when_compat_missing_base_url():
    svc = _FakeChatProvidersService(active="openai_compat", effective={
        "CHAT_LLM_PROVIDER": "openai_compat",
        "CHAT_LLM_MODEL": "qwen3",
        "CHAT_LLM_API_KEY": "",
        "CHAT_LLM_BASE_URL": "",
    })
    client = make_pipeline_llm_client(svc)
    assert isinstance(client, NullPipelineLLM)


def test_factory_falls_back_when_adapter_construction_fails():
    svc = _FakeChatProvidersService(active="anthropic", effective={
        "CHAT_LLM_PROVIDER": "anthropic",
        "CHAT_LLM_MODEL": "claude-sonnet-4-6",
        "CHAT_LLM_API_KEY": "sk-test",
        "CHAT_LLM_BASE_URL": "",
    })
    with patch("anthropic.Anthropic", side_effect=RuntimeError("boom")):
        client = make_pipeline_llm_client(svc)
    assert isinstance(client, NullPipelineLLM)


# ---------------------------------------------------------------------------
# describe_client + Protocol
# ---------------------------------------------------------------------------

def test_describe_client_reads_attrs_when_present():
    cfg = PipelineLLMConfig(provider="anthropic", model="claude-x", api_key="k")
    with patch("anthropic.Anthropic"):
        client = AnthropicLLM(cfg)
    assert describe_client(client) == ("anthropic", "claude-x")


def test_describe_client_falls_back_when_attrs_missing():
    class _Bare:
        pass
    assert describe_client(_Bare()) == ("none", "unknown")


def test_null_pipeline_llm_satisfies_pipeline_protocol():
    assert isinstance(NullPipelineLLM(), PipelineLLM)


def test_anthropic_adapter_satisfies_pipeline_protocol():
    cfg = PipelineLLMConfig(provider="anthropic", model="m", api_key="k")
    with patch("anthropic.Anthropic"):
        client = AnthropicLLM(cfg)
    assert isinstance(client, PipelineLLM)


# ---------------------------------------------------------------------------
# NullPipelineLLM + build_pipeline_description_generator
# ---------------------------------------------------------------------------

class _FakeSvc:
    def __init__(self, provider="anthropic", model="", api_key="", base_url=""):
        self._cfg = {
            "CHAT_LLM_PROVIDER": provider, "CHAT_LLM_MODEL": model,
            "CHAT_LLM_API_KEY": api_key, "CHAT_LLM_BASE_URL": base_url,
        }
    def get_active_provider(self): return self._cfg["CHAT_LLM_PROVIDER"]
    def get_effective_config(self, provider): return self._cfg


def test_null_pipeline_llm_reports_unavailable_and_raises():
    c = NullPipelineLLM()
    assert c.is_available() is False
    assert c.provider == "none"
    with pytest.raises(LLMConnectionError):
        c.generate("hello")


def test_make_pipeline_llm_client_null_when_no_service():
    assert isinstance(make_pipeline_llm_client(None), NullPipelineLLM)


def test_make_pipeline_llm_client_null_when_provider_unconfigured():
    # provider set but no api_key/model -> not usable -> Null
    assert isinstance(make_pipeline_llm_client(_FakeSvc(model="", api_key="")),
                      NullPipelineLLM)


def test_make_pipeline_llm_client_builds_adapter_when_configured():
    client = make_pipeline_llm_client(_FakeSvc(model="claude-x", api_key="sk-test"))
    assert isinstance(client, AnthropicLLM)
    assert client.model == "claude-x"


def test_build_description_generator_uses_service_and_degrades():
    gen = build_pipeline_description_generator(
        db_adapter=None, chat_providers_service=_FakeSvc(model="", api_key=""))
    assert isinstance(gen.llm_client, NullPipelineLLM)
    assert gen.is_available() is False


def test_db_chat_providers_service_wires_adapter():
    """Locks the cross-class wiring in ``_db_chat_providers_service`` — the
    scheduler/CLI path with no app context — so future signature drift in
    ChatProvidersRepository/SettingsManager/ChatProvidersService is caught.
    Neither constructor queries the DB, so a fake with just the attribute
    the helper reads (``conn_manager``) is enough; no real DB needed."""
    from src.services.embedding.pipeline_llm import _db_chat_providers_service

    class _FakeDB:
        conn_manager = object()

    fake = _FakeDB()
    svc = _db_chat_providers_service(fake)
    assert svc.repo.db is fake
    assert svc.settings_manager.connection_manager is fake.conn_manager


# ---------------------------------------------------------------------------
# env_request_timeout + make_pipeline_llm_client timeout wiring
# ---------------------------------------------------------------------------

def test_env_request_timeout_default_when_unset(monkeypatch):
    from src.services.embedding.pipeline_llm import env_request_timeout
    monkeypatch.delenv("EMBEDDING_LLM_REQUEST_TIMEOUT", raising=False)
    assert env_request_timeout(120.0) == 120.0


def test_env_request_timeout_reads_env(monkeypatch):
    from src.services.embedding.pipeline_llm import env_request_timeout
    monkeypatch.setenv("EMBEDDING_LLM_REQUEST_TIMEOUT", "200")
    assert env_request_timeout(120.0) == 200.0


def test_env_request_timeout_malformed_falls_back(monkeypatch):
    from src.services.embedding.pipeline_llm import env_request_timeout
    monkeypatch.setenv("EMBEDDING_LLM_REQUEST_TIMEOUT", "banana")
    assert env_request_timeout(120.0) == 120.0


def test_make_pipeline_llm_client_applies_env_request_timeout(monkeypatch):
    monkeypatch.setenv("EMBEDDING_LLM_REQUEST_TIMEOUT", "175")
    client = make_pipeline_llm_client(_FakeSvc(model="claude-x", api_key="sk-test"))
    assert client.config.timeout == 175.0


def test_make_pipeline_llm_client_default_request_timeout(monkeypatch):
    monkeypatch.delenv("EMBEDDING_LLM_REQUEST_TIMEOUT", raising=False)
    client = make_pipeline_llm_client(_FakeSvc(model="claude-x", api_key="sk-test"))
    assert client.config.timeout == 120.0
