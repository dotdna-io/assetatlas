# SPDX-License-Identifier: AGPL-3.0-or-later
"""Field-description / synonym LLM calls must give reasoning models (e.g. Qwen3)
enough output budget. A too-small cap is consumed by the model's internal
"thinking" before any answer is emitted, yielding empty content and a 500 on
regenerate-description (observed on 113 after the LLM decoupling).
"""
from src.services.embedding.description_generator import (
    DescriptionGenerator, _DESC_MAX_TOKENS,
)
from src.services.embedding.synonym_llm import (
    LLMSynonymGenerator, _SYN_MAX_TOKENS,
)


class _CaptureClient:
    """Fake pipeline LLM client that records the max_tokens it was called with."""
    provider = "fake"
    model = "fake"

    def __init__(self):
        self.max_tokens_calls = []

    def is_available(self):
        return True

    def generate(self, prompt, system_prompt=None, max_tokens=None, temperature=None):
        self.max_tokens_calls.append(max_tokens)
        return "hostname - computer name, machine name. A short description."

    def close(self):
        pass


def test_single_description_uses_reasoning_safe_budget():
    client = _CaptureClient()
    DescriptionGenerator(llm_client=client).generate_description("hostname")
    assert client.max_tokens_calls[-1] == _DESC_MAX_TOKENS
    # >=4096: reasoning models need ~2500 for the system-prompted description,
    # and 4096 stays within Claude 3's 4096 output cap (measured on Qwen3 113).
    assert _DESC_MAX_TOKENS >= 4096


def test_batch_description_budget_has_a_reasoning_floor_for_small_batches():
    client = _CaptureClient()
    DescriptionGenerator(llm_client=client).generate_batch_single_call(
        [{"field_name": "f0"}])
    # a 1-field batch must still get the full reasoning floor, not 512*1
    assert client.max_tokens_calls[-1] == _DESC_MAX_TOKENS


def test_batch_description_budget_is_capped_for_large_batches():
    client = _CaptureClient()
    fields = [{"field_name": f"f{i}"} for i in range(100)]
    DescriptionGenerator(llm_client=client).generate_batch_single_call(fields)
    # capped so the request can't exceed the provider's output limit
    assert client.max_tokens_calls[-1] == 4 * _DESC_MAX_TOKENS
    assert client.max_tokens_calls[-1] < 512 * len(fields)


def test_synonym_search_terms_use_reasoning_safe_budget():
    client = _CaptureClient()
    LLMSynonymGenerator(client).generate_search_terms("hostname", "source")
    assert client.max_tokens_calls[-1] == _SYN_MAX_TOKENS
    assert _SYN_MAX_TOKENS >= 4096
