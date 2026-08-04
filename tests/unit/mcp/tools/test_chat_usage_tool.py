# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the get_chatbot_usage chat tool (plan §2.F).

``get_chatbot_usage`` wraps ``ChatRepository.usage_summary`` — the SAME query
behind ``GET /chat/admin/usage`` — so the numbers an admin sees in chat must
match the admin dashboard exactly. These tests pin:

- the repository's numbers are surfaced verbatim (never re-derived);
- the error RATE is computed from counts (so "which tools error most" is a
  ratio the user doesn't have to divide by hand);
- a bad ``days`` is a LOUD error, never a silent clamp to a different window
  (the §4 loud-over-silent rule — a clamped window answers the wrong question);
- the artifact is a valid ``table`` envelope (the only chart kinds the chat
  registry keeps are donut/stacked_bar/table).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.api.blueprints.chat.artifacts import validate_envelope
from src.mcp.tools.chat_usage_tool import get_chatbot_usage_impl


def _usage_payload():
    return {
        "window_days": 7,
        "totals": {
            "messages": 420,
            "sessions": 33,
            "tokens_input": 1_000_000,
            "tokens_output": 250_000,
            "active_users": 9,
        },
        "top_users": [
            {"user_id": "u1", "username": "alice", "messages": 200},
            {"user_id": "u2", "username": "bob", "messages": 120},
        ],
        "by_provider": [
            {"provider": "anthropic", "model": "claude-opus-4-8",
             "input_tokens": 900_000, "output_tokens": 200_000, "messages": 300},
            {"provider": "openai", "model": "gpt-5",
             "input_tokens": 100_000, "output_tokens": 50_000, "messages": 120},
        ],
        "tool_calls": [
            {"tool_name": "query_assets", "count": 200, "error_count": 4,
             "timeout_count": 1, "denied_count": 0, "avg_duration_ms": 35},
            {"tool_name": "run_chart", "count": 10, "error_count": 5,
             "timeout_count": 0, "denied_count": 2, "avg_duration_ms": 80},
        ],
        "recent_downvotes": [
            {"message_id": "m1", "session_id": "s1", "user_id": "u1",
             "username": "alice", "comment": "wrong count",
             "created_at": "2026-06-20T10:00:00"},
        ],
    }


def _repo(payload=None):
    repo = MagicMock()
    repo.usage_summary.return_value = payload if payload is not None else _usage_payload()
    return repo


def test_happy_path_surfaces_totals_users_providers_and_tools():
    repo = _repo()

    text, artifact = get_chatbot_usage_impl(repo, days=7)

    repo.usage_summary.assert_called_once_with(days=7)
    assert "last 7 day" in text
    assert "420" in text                       # messages total
    assert "1,000,000" in text and "250,000" in text  # token totals
    assert "alice" in text and "bob" in text   # top users
    assert "claude-opus-4-8" in text           # provider/model token spend
    assert "query_assets" in text and "run_chart" in text
    assert "wrong count" in text               # recent downvote comment
    assert artifact is not None


def test_error_rate_is_computed_not_raw():
    """run_chart errored 5 of 10 calls → 50.0%. A user must not have to divide
    error_count by count themselves; the headline 'which tools error most'
    is a RATE, and a tool with few calls but high failure must stand out."""
    repo = _repo()

    text, artifact = get_chatbot_usage_impl(repo, days=7)

    assert "50.0%" in text                      # run_chart: 5/10
    assert "2.0%" in text                       # query_assets: 4/200
    # the artifact carries the same computed rate as a real number
    run_chart_row = next(r for r in artifact["series"]
                         if r["tool_name"] == "run_chart")
    assert run_chart_row["err_rate"] == 50.0


def test_artifact_is_a_valid_table_envelope():
    repo = _repo()

    _text, artifact = get_chatbot_usage_impl(repo, days=7)

    # Must pass the registry's envelope gate, or the frontend silently drops it.
    assert validate_envelope(artifact, tool_name="get_chatbot_usage") is artifact
    assert artifact["type"] == "table"
    assert {c["key"] for c in artifact["columns"]} >= {
        "tool_name", "count", "err_rate", "avg_duration_ms"}
    assert len(artifact["series"]) == 2


def test_bad_days_is_loud_error_and_does_not_query():
    """An out-of-set window must error loudly — silently clamping 14→7 would
    answer for a different period than the admin asked about."""
    repo = _repo()

    text, artifact = get_chatbot_usage_impl(repo, days=14)

    assert "Valid values" in text
    assert artifact is None
    repo.usage_summary.assert_not_called()


def test_days_accepts_stringified_int():
    """LLMs sometimes send an integer arg as a string; '30' is a valid preset
    and must resolve, not be rejected as a bad enum."""
    repo = _repo()

    get_chatbot_usage_impl(repo, days="30")

    repo.usage_summary.assert_called_once_with(days=30)


def test_by_provider_null_provider_model_renders_placeholder():
    """Legacy/error sessions can have NULL provider/model; usage_summary returns
    the keys present with None values, so it must not render the literal
    'None/None' (a .get default would never fire on a present-but-None key)."""
    repo = _repo({
        "window_days": 7, "totals": {}, "top_users": [],
        "by_provider": [{"provider": None, "model": None,
                         "input_tokens": 10, "output_tokens": 5, "messages": 2}],
        "tool_calls": [], "recent_downvotes": [],
    })

    text, _artifact = get_chatbot_usage_impl(repo, days=7)

    assert "None/None" not in text
    assert "?/?" in text


def test_repo_unavailable_is_graceful():
    text, artifact = get_chatbot_usage_impl(None, days=7)

    assert "not available" in text.lower()
    assert artifact is None


def test_empty_usage_does_not_crash_and_has_no_artifact():
    """A brand-new install with zero activity is a legitimate empty, not an
    error — render zeros, and emit no (empty) artifact."""
    repo = _repo({
        "window_days": 1,
        "totals": {}, "top_users": [], "by_provider": [],
        "tool_calls": [], "recent_downvotes": [],
    })

    text, artifact = get_chatbot_usage_impl(repo, days=1)

    assert "last 1 day" in text
    assert "no tool calls" in text.lower()
    assert artifact is None
