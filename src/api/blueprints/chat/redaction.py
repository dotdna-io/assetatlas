# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Chat Redaction Helpers

Scrubs obvious secrets (JWTs, common API key patterns) from user message
text and truncates over-size tool outputs before persistence or logging.
Intentionally narrow: preserves hostnames, IPs, and MAC addresses as they
are legitimate chat content.
"""

from __future__ import annotations

import re
from typing import Optional


_JWT_RE = re.compile(
    r"(?:Bearer\s+)?eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{20,}"
)
_API_KEY_RE = re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b")

_TRUNC_MARKER = (
    "\n\n[TRUNCATED: output exceeded the response size cap. Narrow your "
    "filters (add more WHERE-style conditions) or reduce ``limit`` and "
    "retry. Do not assume anything about the truncated portion.]"
)


def redact_text(text: str) -> str:
    """Replace JWTs and common API key patterns with redaction markers."""
    if not text:
        return text
    text = _JWT_RE.sub("[REDACTED_JWT]", text)
    text = _API_KEY_RE.sub("[REDACTED_API_KEY]", text)
    return text


def truncate_tool_result(text: Optional[str], max_bytes: int = 50_000) -> Optional[str]:
    """Truncate a tool result to at most ``max_bytes`` UTF-8 bytes, plus a marker."""
    if text is None:
        return None
    # Fast path: UTF-8 is at most 4 bytes/char, so short strings never exceed limit.
    if len(text) * 4 <= max_bytes:
        return text
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return truncated + _TRUNC_MARKER
