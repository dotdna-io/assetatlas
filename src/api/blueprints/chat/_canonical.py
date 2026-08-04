# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical-JSON helper used for deduplication + hashing across the chat
module. Centralised so the stable-sort + default=str idiom isn't re-typed
at each call site (dedup in the orchestration loop, SHA-256 keying in the
recording-LLM test harness)."""

from __future__ import annotations

import json
from typing import Any, Optional


def stable_json(obj: Any, *, max_len: Optional[int] = None) -> str:
    """Return a sorted, deterministic JSON string for ``obj``.

    ``default=str`` tolerates non-JSON-native inputs (datetime, UUID) so the
    callers don't have to pre-serialize. Falls back to ``repr`` on anything
    that json rejects outright (e.g. circular structures) so the helper
    cannot itself raise.
    """
    try:
        s = json.dumps(obj, sort_keys=True, default=str)
    except Exception:
        s = repr(obj)
    return s[:max_len] if max_len is not None else s
