# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for chat redaction helpers.

Tests redact_text (JWT + API key scrubbing) and truncate_tool_result
(byte-bounded tool output clipping with marker).
"""

from src.api.blueprints.chat.redaction import redact_text, truncate_tool_result


_REAL_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1c2VyMSIsImlhdCI6MTcxNDAwMDAwMH0"
    ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)


def test_redacts_bearer_token():
    out = redact_text(f"auth=Bearer {_REAL_JWT}")
    assert "eyJhbGciOiJIUzI1NiIs" not in out
    assert "[REDACTED_JWT]" in out


def test_redacts_generic_api_key_patterns():
    out = redact_text("key=sk_live_abcdefg1234567890abcdef ok")
    assert "sk_live_abcdefg1234567890abcdef" not in out
    assert "[REDACTED_API_KEY]" in out


def test_preserves_hostnames_and_ips():
    s = "server-1.corp.example 10.0.0.5 aa:bb:cc:dd:ee:ff"
    assert redact_text(s) == s


def test_truncate_tool_result_under_limit_is_noop():
    s = "x" * 1000
    assert truncate_tool_result(s, max_bytes=50_000) == s


def test_truncate_tool_result_over_limit_appends_marker():
    from src.api.blueprints.chat.redaction import _TRUNC_MARKER
    s = "x" * 60_000
    out = truncate_tool_result(s, max_bytes=50_000)
    assert out.startswith("x" * 10)
    assert out.endswith(_TRUNC_MARKER)
    # Marker is actionable: tells the model what to do about the truncation
    # rather than just saying "truncated".
    assert "Narrow" in _TRUNC_MARKER
    assert len(out.encode("utf-8")) <= 50_000 + len(_TRUNC_MARKER.encode("utf-8"))


def test_truncate_tool_result_none_returns_none():
    assert truncate_tool_result(None) is None
