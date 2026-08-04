# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the LLM client factory.

The factory takes a ``CHAT_LLM_*`` config dict (typically produced by
``ChatProvidersService.get_effective_config``). There is no env-var fallback
since issue #636 — the dict is the only input.
"""

import pytest

from src.api.blueprints.chat.llm import make_llm_client
from src.api.blueprints.chat.llm.anthropic_client import AnthropicClient
from src.api.blueprints.chat.llm.openai_client import OpenAIClient
from src.api.blueprints.chat.llm.openai_compat_client import OpenAICompatClient


def test_make_llm_client_anthropic():
    c = make_llm_client({
        "CHAT_LLM_PROVIDER": "anthropic",
        "CHAT_LLM_MODEL": "claude-sonnet-4-6",
        "CHAT_LLM_API_KEY": "sk-test",
    })
    assert isinstance(c, AnthropicClient)
    assert c.provider == "anthropic"
    assert c.model == "claude-sonnet-4-6"


def test_make_llm_client_is_case_insensitive():
    c = make_llm_client({
        "CHAT_LLM_PROVIDER": "ANTHROPIC",
        "CHAT_LLM_MODEL": "claude-sonnet-4-6",
        "CHAT_LLM_API_KEY": "sk-test",
    })
    assert c.provider == "anthropic"


def test_make_llm_client_prompt_cache_disabled():
    c = make_llm_client({
        "CHAT_LLM_PROVIDER": "anthropic",
        "CHAT_LLM_MODEL": "claude-sonnet-4-6",
        "CHAT_LLM_API_KEY": "sk-test",
        "CHAT_PROMPT_CACHE": False,
    })
    assert c._prompt_cache is False


def test_make_llm_client_prompt_cache_string_form_accepted():
    c = make_llm_client({
        "CHAT_LLM_PROVIDER": "anthropic",
        "CHAT_LLM_MODEL": "claude-sonnet-4-6",
        "CHAT_LLM_API_KEY": "sk-test",
        "CHAT_PROMPT_CACHE": "True",
    })
    assert c._prompt_cache is True


def test_make_llm_client_unknown_raises():
    with pytest.raises(ValueError, match="Unknown CHAT_LLM_PROVIDER"):
        make_llm_client({
            "CHAT_LLM_PROVIDER": "nope",
            "CHAT_LLM_API_KEY": "sk-test",
        })


def test_make_llm_client_openai():
    c = make_llm_client({
        "CHAT_LLM_PROVIDER": "openai",
        "CHAT_LLM_MODEL": "gpt-4o-mini",
        "CHAT_LLM_API_KEY": "sk-openai",
    })
    assert isinstance(c, OpenAIClient)
    assert c.provider == "openai"
    assert c.model == "gpt-4o-mini"


def test_make_llm_client_openai_requires_api_key():
    with pytest.raises(ValueError, match="API key"):
        make_llm_client({
            "CHAT_LLM_PROVIDER": "openai",
            "CHAT_LLM_MODEL": "gpt-4o-mini",
            "CHAT_LLM_API_KEY": "",
        })


def test_make_llm_client_anthropic_requires_api_key():
    with pytest.raises(ValueError, match="API key"):
        make_llm_client({
            "CHAT_LLM_PROVIDER": "anthropic",
            "CHAT_LLM_MODEL": "claude-sonnet-4-6",
            "CHAT_LLM_API_KEY": "",
        })


def test_make_llm_client_openai_compat_requires_base_url():
    with pytest.raises(ValueError, match="base URL"):
        make_llm_client({
            "CHAT_LLM_PROVIDER": "openai_compat",
            "CHAT_LLM_MODEL": "qwen3",
            "CHAT_LLM_API_KEY": "",
            "CHAT_LLM_BASE_URL": "",
        })


def test_make_llm_client_openai_compat_allows_empty_api_key():
    """Self-hosted vLLM/Ollama doesn't need a key; the SDK substitutes a
    sentinel header value internally."""
    c = make_llm_client({
        "CHAT_LLM_PROVIDER": "openai_compat",
        "CHAT_LLM_MODEL": "llama-3.1-8b-instruct",
        "CHAT_LLM_API_KEY": "",
        "CHAT_LLM_BASE_URL": "http://vllm.internal:8000/v1",
    })
    assert isinstance(c, OpenAICompatClient)
    assert c.provider == "openai_compat"
    assert c.model == "llama-3.1-8b-instruct"


def test_make_llm_client_openai_compat_strips_chat_completions_suffix():
    # Admins commonly paste the full endpoint (…/v1/chat/completions — the
    # shape most vLLM/Ollama/LiteLLM curl examples show). The SDK appends
    # chat/completions itself, so the raw string would 404 on a doubled path.
    # The factory must strip the suffix back to the base URL.
    c = make_llm_client({
        "CHAT_LLM_PROVIDER": "openai_compat",
        "CHAT_LLM_MODEL": "llama-3.1-8b-instruct",
        "CHAT_LLM_API_KEY": "",
        "CHAT_LLM_BASE_URL": "http://vllm.internal:8000/v1/chat/completions",
    })
    assert c._base_url == "http://vllm.internal:8000/v1"


def test_make_llm_client_openai_compat_leaves_bare_base_url_untouched():
    # A correct base URL (no suffix) must pass through unchanged.
    c = make_llm_client({
        "CHAT_LLM_PROVIDER": "openai_compat",
        "CHAT_LLM_MODEL": "m",
        "CHAT_LLM_API_KEY": "",
        "CHAT_LLM_BASE_URL": "http://x:8000/v1",
    })
    assert c._base_url == "http://x:8000/v1"


def test_make_llm_client_openai_compat_thinking_disabled_via_config():
    """``CHAT_LLM_THINKING_ENABLED=false`` should propagate into the client
    as ``chat_template_kwargs.enable_thinking=false`` on every request."""
    c = make_llm_client({
        "CHAT_LLM_PROVIDER": "openai_compat",
        "CHAT_LLM_MODEL": "qwen3",
        "CHAT_LLM_API_KEY": "",
        "CHAT_LLM_BASE_URL": "http://x/v1",
        "CHAT_LLM_THINKING_ENABLED": False,
    })
    assert isinstance(c, OpenAICompatClient)
    assert c._extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_make_llm_client_openai_compat_thinking_on_sends_no_extra_body():
    c = make_llm_client({
        "CHAT_LLM_PROVIDER": "openai_compat",
        "CHAT_LLM_MODEL": "qwen3",
        "CHAT_LLM_API_KEY": "",
        "CHAT_LLM_BASE_URL": "http://x/v1",
        "CHAT_LLM_THINKING_ENABLED": True,
    })
    # Default is True → server-side template default wins, no kwarg sent.
    assert c._extra_body is None


def test_make_llm_client_accepts_bool_prompt_cache():
    c = make_llm_client({
        "CHAT_LLM_PROVIDER": "anthropic",
        "CHAT_LLM_MODEL": "claude-sonnet-4-6",
        "CHAT_LLM_API_KEY": "sk-test",
        "CHAT_PROMPT_CACHE": False,
    })
    assert c._prompt_cache is False
