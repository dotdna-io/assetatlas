# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the semantic_suggest TTL cache."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from src.api.blueprints.chat import semantic_cache as sc


def setup_function(_fn):
    sc._reset_cache_for_tests()


def test_cache_hit_skips_runner():
    runner = MagicMock(return_value="suggestions-for-edr")
    out1 = sc.cached_semantic_suggest(
        runner, query="EDR", limit=5, min_score=0.5,
    )
    out2 = sc.cached_semantic_suggest(
        runner, query="EDR", limit=5, min_score=0.5,
    )
    assert out1 == out2 == "suggestions-for-edr"
    assert runner.call_count == 1


def test_cache_key_is_case_and_whitespace_insensitive():
    runner = MagicMock(return_value="same-result")
    sc.cached_semantic_suggest(runner, query="EDR", limit=5, min_score=0.5)
    sc.cached_semantic_suggest(runner, query="  edr ", limit=5, min_score=0.5)
    assert runner.call_count == 1


def test_different_limit_bypasses_cache():
    runner = MagicMock(side_effect=["a", "b"])
    out1 = sc.cached_semantic_suggest(
        runner, query="EDR", limit=5, min_score=0.5,
    )
    out2 = sc.cached_semantic_suggest(
        runner, query="EDR", limit=10, min_score=0.5,
    )
    assert out1 == "a"
    assert out2 == "b"
    assert runner.call_count == 2


def test_ttl_expiry(monkeypatch):
    runner = MagicMock(side_effect=["first", "second"])
    base = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: base)
    sc.cached_semantic_suggest(runner, query="q", limit=5, min_score=0.5)

    # Advance past the default 5-minute TTL.
    monkeypatch.setattr(time, "monotonic", lambda: base + 301.0)
    out = sc.cached_semantic_suggest(runner, query="q", limit=5, min_score=0.5)
    assert out == "second"
    assert runner.call_count == 2


def test_max_entries_eviction():
    # Fill cache to capacity + 1; oldest entry is evicted, newest retained.
    runner = MagicMock(side_effect=lambda **kw: f"val-{kw['query']}")
    for i in range(sc._DEFAULT_MAX_ENTRIES + 5):
        sc.cached_semantic_suggest(
            runner, query=f"q{i}", limit=5, min_score=0.5,
        )
    # Last inserted key should hit cache; very first should miss.
    assert sc._cache.get(("q0", 5, 0.5)) is None
    newest_key = (f"q{sc._DEFAULT_MAX_ENTRIES + 4}", 5, 0.5)
    assert sc._cache.get(newest_key) is not None
