# SPDX-License-Identifier: AGPL-3.0-or-later
"""Map raw LLM/provider error detail to safe, operator-actionable messages
for the chat UI.

Centralised so both the streaming route (which catches provider exceptions)
and the service orchestrator (which surfaces in-stream ``result.error``)
sanitise identically: provider internals never reach the client; the raw
text is logged server-side at each call site (issue #695)."""

from __future__ import annotations


# Shown when a chat reply is cut off at the output-token cap
# (stop_reason == "max_tokens"). This is a length limit, NOT a provider
# misconfiguration, so it must NOT route through describe_stream_error (whose
# generic hint tells the user to check Settings → Chatbot). Reasoning models
# (e.g. Qwen3) are especially prone to this — their hidden <think> block spends
# output tokens before any answer text.
MAX_TOKENS_NOTICE = (
    "The response was cut off because it reached the length limit for a "
    "single reply. Try asking for fewer items or a narrower question, or ask "
    "an administrator to raise the chat response-length limit."
)


# Shown for transient upstream capacity errors (Anthropic overloaded_error /
# HTTP 529, provider 429 / rate limits). These are NOT misconfigurations, so —
# like MAX_TOKENS_NOTICE — they must NOT route through the generic "check the
# chat provider configuration" hint: a resend usually succeeds (issue #815).
TRANSIENT_BUSY_NOTICE = (
    "The AI service is temporarily busy. Please try again in a moment."
)


# Shown when Claude returns stop_reason == "refusal" — a normal (HTTP 200)
# safety outcome where the model declines to answer, NOT a misconfiguration.
# Like the notices above it must NOT route through the generic "check the chat
# provider configuration" hint.
REFUSAL_NOTICE = (
    "The assistant declined to respond to that request. Try rephrasing it, or "
    "start a new conversation."
)


def describe_stream_error(detail: str) -> str:
    """Return a model-agnostic, user-safe hint for a failed chat turn.

    ``detail`` is the raw provider/exception text — already logged server-side
    by the caller, never echoed back here. Known Bedrock/Converse
    ``ValidationException`` causes get a specific hint; everything else gets a
    generic one."""
    text = (detail or "").lower()
    if "tool use" in text or "tool_use" in text:
        return ("The selected chat model doesn't support tool use, which the "
                "assistant requires. An administrator should choose a "
                "tool-capable model in Settings → Chatbot.")
    if "system messages" in text or "system prompt" in text:
        return ("The selected chat model doesn't support system prompts. An "
                "administrator should choose a different model in "
                "Settings → Chatbot.")
    if ("don't have access" in text or "access to the model" in text
            or "accessdenied" in text):
        return ("The configured chat model isn't enabled for this account or "
                "region. An administrator should enable model access or pick "
                "another model in Settings → Chatbot.")
    # Match on the textual capacity markers, NOT bare "429"/"529": those digits
    # occur incidentally in unrelated error detail (12-digit AWS account ids,
    # token counts, request ids), which would mislabel a real config error as
    # transient. The Anthropic 529 detail always carries "overloaded_error" and
    # 429s carry "rate_limit"/"too many requests", so the words suffice.
    if ("overloaded" in text or "rate limit" in text
            or "rate_limit" in text or "too many requests" in text):
        return TRANSIENT_BUSY_NOTICE
    # A model refusal (Claude stop_reason=="refusal") is a normal safety
    # outcome, not a config problem — surface it as such. Match the specific
    # sentinel the client emits, not a bare "refusal", so an unrelated provider
    # error that merely contains the word isn't mislabelled as a refusal.
    if "stop_reason=refusal" in text:
        return REFUSAL_NOTICE
    return ("The assistant couldn't complete that request. An administrator "
            "should check the chat provider configuration in "
            "Settings → Chatbot.")
