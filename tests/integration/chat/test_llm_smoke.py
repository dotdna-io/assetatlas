# SPDX-License-Identifier: AGPL-3.0-or-later
"""Opt-in LLM smoke test — sanity checks one real call per configured
provider. Never runs unless ``RUN_LLM_SMOKE=1`` is set.

Why opt-in: CI would otherwise either break when the API key is missing
or burn a few cents per run for no signal beyond what the unit tests
already give. Keep this as a manual gate operators run after rotating
keys or standing up a new LLM endpoint.

Run:
    RUN_LLM_SMOKE=1 \\
    CHAT_LLM_PROVIDER=anthropic \\
    CHAT_LLM_API_KEY=sk-ant-... \\
    pytest tests/integration/chat/test_llm_smoke.py -m integration -s
"""

from __future__ import annotations

import os

import pytest

from src.api.blueprints.chat.llm import make_llm_client
from src.api.blueprints.chat.llm.base import LLMMessage, ToolSpec


pytestmark = pytest.mark.integration


def _smoke_enabled() -> bool:
    return os.getenv("RUN_LLM_SMOKE") == "1"


@pytest.fixture
def configured_client():
    """Build a live LLM client from the current env; skip when not enabled
    or the chosen provider lacks its required config."""
    if not _smoke_enabled():
        pytest.skip("RUN_LLM_SMOKE not set")
    provider = os.getenv("CHAT_LLM_PROVIDER", "anthropic")
    if provider in ("anthropic", "openai") and not os.getenv("CHAT_LLM_API_KEY"):
        pytest.skip(f"{provider} smoke requires CHAT_LLM_API_KEY")
    if provider == "openai_compat" and not os.getenv("CHAT_LLM_BASE_URL"):
        pytest.skip("openai_compat smoke requires CHAT_LLM_BASE_URL")
    return make_llm_client()


@pytest.mark.asyncio
async def test_smoke_plain_text(configured_client):
    """A trivial text-only turn must complete with non-empty output."""
    chunks_text: list[str] = []
    async with await configured_client.stream(
        system_prompt="You are a terse assistant.",
        messages=[LLMMessage(role="user",
                             content="Reply with exactly: OK")],
        tools=[],
        max_output_tokens=32,
    ) as s:
        async for c in s:
            if c.kind == "text" and c.text:
                chunks_text.append(c.text)
        res = s.result

    assert res.stop_reason in ("end_turn", "max_tokens")
    assert "".join(chunks_text), (
        f"No text emitted by {configured_client.provider}/{configured_client.model}"
    )


@pytest.mark.asyncio
async def test_smoke_single_tool_call(configured_client):
    """When offered a single tool and prompted for a lookup, the model
    should either call the tool or reply in text. A provider/model that
    can't do either at all would fail this assertion with ``"error"``."""
    echo_tool = ToolSpec(
        name="echo",
        description="Return the input string unchanged.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )

    async with await configured_client.stream(
        system_prompt=("You are an assistant. When the user asks you to "
                       "echo a string, call the echo tool once with the "
                       "exact string as the 'text' argument."),
        messages=[LLMMessage(role="user",
                             content="Echo the word 'smoke' for me.")],
        tools=[echo_tool],
        max_output_tokens=128,
    ) as s:
        async for _ in s:
            pass
        res = s.result

    assert res.stop_reason != "error"
    # Either the tool was called, or the model answered in text — both are
    # acceptable smoke signals; what we're flushing out is crashes, auth
    # failures, and broken streaming adapters.
    assert res.stop_reason in ("end_turn", "tool_use", "max_tokens")
    if res.stop_reason == "tool_use":
        assert any(tc["name"] == "echo" for tc in res.tool_calls), (
            "Model asked for tool_use but didn't call 'echo': "
            f"{[tc.get('name') for tc in res.tool_calls]}"
        )
