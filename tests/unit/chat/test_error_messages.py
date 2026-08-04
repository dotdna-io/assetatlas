# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for describe_stream_error — the chat error-message sanitiser.

It must turn raw provider/exception text into a safe, model-agnostic hint:
known Bedrock/Converse causes get a specific message, everything else gets a
generic one, and the raw detail is never echoed back (issue #695)."""

from src.api.blueprints.chat.error_messages import describe_stream_error


def test_tool_use_rejection_maps_to_tool_capable_hint():
    msg = describe_stream_error(
        "An error occurred (ValidationException) when calling the "
        "ConverseStream operation: This model doesn't support tool use "
        "in streaming mode."
    )
    assert "tool-capable model" in msg
    assert "Settings → Chatbot" in msg


def test_system_prompt_rejection_maps_to_system_prompt_hint():
    msg = describe_stream_error(
        "ValidationException: This model doesn't support system messages."
    )
    assert "system prompt" in msg.lower()
    assert "Settings → Chatbot" in msg


def test_model_access_denied_maps_to_enablement_hint():
    msg = describe_stream_error(
        "AccessDeniedException: You don't have access to the model with the "
        "specified model ID."
    )
    assert "enable model access" in msg
    assert "Settings → Chatbot" in msg


def test_unknown_error_maps_to_generic_hint():
    msg = describe_stream_error("connection reset by peer")
    assert "couldn't complete" in msg
    assert "Settings → Chatbot" in msg


def test_empty_detail_maps_to_generic_hint():
    assert "Settings → Chatbot" in describe_stream_error("")


def test_overloaded_error_maps_to_transient_busy_not_config_hint():
    # A transient upstream 529 (Anthropic "Overloaded") is capacity, not a
    # misconfiguration — it must NOT tell the operator to check the provider
    # config (#815).
    msg = describe_stream_error(
        "{'type':'error','error':{'type':'overloaded_error',"
        "'message':'Overloaded'},'request_id':'req_011CcZ'}"
    )
    assert "temporarily busy" in msg.lower()
    assert "try again" in msg.lower()
    assert "Settings → Chatbot" not in msg


def test_rate_limit_429_maps_to_transient_busy():
    msg = describe_stream_error(
        "RateLimitError: Error code: 429 - rate_limit_error: number of "
        "requests has exceeded your rate limit."
    )
    assert "temporarily busy" in msg.lower()
    assert "Settings → Chatbot" not in msg


def test_bare_429_digits_in_unrelated_error_are_not_treated_as_transient():
    # A real config/validation error whose detail merely contains the digits
    # 429/529 (an account id, token budget, request id) must NOT be mislabelled
    # transient-busy — it should fall through to the generic config hint.
    msg = describe_stream_error(
        "ValidationException: request req-5290 for account 042900429100 "
        "exceeded the configured token budget"
    )
    assert "temporarily busy" not in msg.lower()
    assert "Settings → Chatbot" in msg


def test_refusal_maps_to_declined_not_config_hint():
    # stop_reason="refusal" is a normal (HTTP 200) Claude safety outcome, not a
    # misconfiguration — the user must be told the model declined, not to check
    # the provider config.
    msg = describe_stream_error("Claude stopped with stop_reason=refusal")
    assert "declined" in msg.lower()
    assert "Settings → Chatbot" not in msg


def test_never_echoes_the_raw_detail():
    """The security invariant: whatever the provider said, the hint must not
    contain it verbatim."""
    raw = "super-secret-internal-token-abc123"
    assert raw not in describe_stream_error(raw)
