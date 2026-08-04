# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tiny in-process TTL cache for semantic_suggest results.

Field-discovery questions ("which field holds EDR info?") are repetitive
across turns in a single conversation and deterministic across short time
windows. Caching their rendered text output saves both a tool round-trip
and the embedding / vector-search work on the backend.

Scope is intentionally per-process memory. A Redis-backed variant can
replace this later for multi-worker deployments; for the current single-API
dev setup the memory cache is sufficient and has no external deps.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_TTL_SECONDS = 300
_DEFAULT_MAX_ENTRIES = 1024


class _TTLCache:
    def __init__(self, *, ttl_seconds: int, max_entries: int) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._lock = threading.Lock()
        self._store: Dict[Any, Tuple[float, Any]] = {}

    def get(self, key: Any) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at < time.monotonic():
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: Any, value: Any) -> None:
        with self._lock:
            if len(self._store) >= self._max and key not in self._store:
                # FIFO eviction of a single oldest entry; cheap and good
                # enough for the expected ~tens of distinct queries per day.
                try:
                    oldest = next(iter(self._store))
                except StopIteration:
                    oldest = None
                if oldest is not None:
                    self._store.pop(oldest, None)
            self._store[key] = (time.monotonic() + self._ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


_cache = _TTLCache(ttl_seconds=_DEFAULT_TTL_SECONDS,
                   max_entries=_DEFAULT_MAX_ENTRIES)


def cached_semantic_suggest(
    runner: Callable[..., str],
    *,
    query: str,
    limit: int,
    min_score: float,
) -> str:
    """Call ``runner(query=…, limit=…, min_score=…)``, returning a cached
    result when available. Cache key uses (query.lower(), limit, min_score)
    so trivial variation (capitalization, whitespace) dedups.
    """
    key = (query.strip().lower(), int(limit), round(float(min_score), 3))
    hit = _cache.get(key)
    if hit is not None:
        logger.debug("semantic_suggest cache HIT query=%r", query)
        return hit
    value = runner(query=query, limit=limit, min_score=min_score)
    _cache.set(key, value)
    return value


def _reset_cache_for_tests() -> None:
    """Clear the cache. Test-only."""
    _cache.clear()
