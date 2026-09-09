# SPDX-License-Identifier: AGPL-3.0-or-later
"""Redis cache for Explorer field-values results (#1410).

`AssetService.get_field_values` serves `/api/assets/values` — the Explorer
typeahead and column-filter dropdowns — by scanning `assets` for distinct
values on every call. The table only changes when the pipeline runs or on a
handful of discrete edits, so the result is cached in Redis under a key that
mixes in two version stamps:

  * `src.modules.fields.catalog_version` — which fields exist;
  * `src.modules.shared.asset_data_version` — what the asset rows are.

Every writer that can change values bumps one of the stamps, so a stale entry
is unreachable after any write — there are no pattern deletes and no
TTL-based staleness. The 900s TTL is a safety net that only bounds how long
an orphaned entry (keyed under a superseded stamp) occupies memory.

Correctness rules, all deliberate:

  * NO in-memory fallback. When Redis is unavailable (no REDIS_URL, either
    stamp reads None, any Redis error) the cache is a strict no-op for that
    call — a per-worker memory cache would serve stale values across workers,
    exactly the #1136 failure class.
  * Key derivation never uses `hash()` — PYTHONHASHSEED differs per worker,
    so two workers would derive different keys for the same request (#1136).
    The key is md5 over a canonical JSON dump (sort_keys, tight separators);
    md5 is a key-shortening device here, not a security boundary.
  * `filters` may arrive as a raw JSON string (unparsed query param) or as an
    already-parsed list; strings are parsed first and everything is re-dumped
    with sort_keys, so key-order variants of the same filters collapse to one
    key. Unparseable input bypasses the cache — the repository raises its own
    validation error, same as the uncached path.
  * The key is derived ONCE per request and remembered on the payload dict.
    Re-deriving at set() time would read the stamps again, and a bump landing
    between the miss and the store would file pre-bump data under the new
    stamp — a stale hit that survives until the TTL.

All Redis errors are swallowed and logged at DEBUG; a broken cache must never
break a request. After any Redis error the cache latches OFF for
`_REDIS_DOWN_SECONDS` (stamp reads included — they hit the same server),
so a hung-but-connected Redis costs one 1 s socket timeout per window
instead of up to four (two stamp reads + get + setex) per request.
"""

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

from src.modules.fields import catalog_version
from src.modules.shared import asset_data_version

logger = logging.getLogger(__name__)

KEY_PREFIX = "assetatlas:explorer:values:"

DEFAULT_TTL_SECONDS = 900

# How long the cache stays latched off after a Redis error.
_REDIS_DOWN_SECONDS = 30.0

# Reserved payload attribute under which get() stashes the derived key for
# set(). Chosen to be impossible to confuse with a request parameter.
_KEY_ATTR = "_field_values_cache_key"
_UNSET = object()

# Prometheus counter, created here (not in src.api.utils.metrics, which a
# src/modules module may not import). Counter works in both serving modes:
# single-process registers it on the default REGISTRY the exporter serves;
# multiprocess mode mmaps every inc() and the /metrics route's
# MultiProcessCollector aggregates it. Guarded so a duplicate registration
# (module imported twice under different names) degrades to no-metrics rather
# than an import-time crash.
try:
    from prometheus_client import Counter

    _RESULT_COUNTER = Counter(
        "assetatlas_field_values_cache_total",
        "Explorer field-values cache outcomes (hit, miss, bypass)",
        ["result"],
    )
except Exception:  # pragma: no cover - defensive
    _RESULT_COUNTER = None


def _track(result: str) -> None:
    if _RESULT_COUNTER is not None:
        try:
            _RESULT_COUNTER.labels(result=result).inc()
        except Exception:  # pragma: no cover - defensive
            pass


_default_client = None
_default_client_failed = False
_default_client_lock = threading.Lock()


def _default_client_provider():
    """Lazy shared Redis client from REDIS_URL; None when unavailable.

    Independent of the Flask-Caching pool so the cache works identically with
    or without an app context (same rationale as the stamp sources). A client
    that failed to build is not retried within the process.
    """
    global _default_client, _default_client_failed
    if _default_client is not None or _default_client_failed:
        return _default_client
    url = os.getenv("REDIS_URL")
    if not url:
        return None
    with _default_client_lock:
        if _default_client is not None or _default_client_failed:
            return _default_client
        try:
            import redis
            # Short timeouts: a hung Redis must not stall a typeahead.
            _default_client = redis.Redis.from_url(
                url, socket_timeout=1.0, socket_connect_timeout=1.0,
            )
        except Exception as exc:
            logger.debug("field-values cache: Redis client unavailable: %s", exc)
            _default_client_failed = True
    return _default_client


class FieldValuesCache:
    """get()/set() for field-values results, keyed by params + version stamps.

    Args:
        redis_client_provider: Zero-arg callable returning a Redis client or
            None (unavailable). Defaults to the lazy shared REDIS_URL client.
        ttl_seconds: Safety-net expiry for entries; correctness comes from
            the version stamps, not the TTL.
        catalog_stamp / data_stamp: Zero-arg callables returning the current
            stamp value or None when the source is unavailable. Injectable
            for tests; defaults read the shared process-wide stamps.
    """

    def __init__(
        self,
        redis_client_provider: Optional[Callable[[], Any]] = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        catalog_stamp: Callable[[], Optional[int]] = catalog_version.get,
        data_stamp: Callable[[], Optional[int]] = asset_data_version.get,
    ):
        self._client_provider = redis_client_provider or _default_client_provider
        self._ttl = ttl_seconds
        self._catalog_stamp = catalog_stamp
        self._data_stamp = data_stamp
        # monotonic() deadline: while in the future, the cache is latched off
        # (set by any Redis error). Float read/write is atomic under the GIL;
        # a racing request at worst pays one extra timeout.
        self._redis_down_until = 0.0

    def _note_redis_error(self, what: str, exc: Exception) -> None:
        logger.debug("field-values cache %s failed: %s", what, exc)
        self._redis_down_until = time.monotonic() + _REDIS_DOWN_SECONDS

    # ------------------------------------------------------------------
    # Key derivation
    # ------------------------------------------------------------------

    def _derive_key(self, payload: Dict[str, Any]) -> Optional[str]:
        """Cache key for one request shape; None when the cache is disabled.

        Disabled means: either stamp source is unavailable (Redis down or
        unconfigured — without stamps there is no invalidation story), or the
        payload cannot be canonicalised (unparseable filters string,
        non-JSON-serialisable content — the live path handles those with its
        own validation), or the cache is latched off after a Redis error.
        """
        if time.monotonic() < self._redis_down_until:
            return None
        try:
            catalog_v = self._catalog_stamp()
            data_v = self._data_stamp()
        except Exception as exc:
            logger.debug("field-values cache: stamp read failed: %s", exc)
            return None
        if catalog_v is None or data_v is None:
            return None
        filters = payload.get("filters")
        try:
            if isinstance(filters, str):
                filters = json.loads(filters)
            canonical = json.dumps(
                {
                    "field": payload.get("field"),
                    "limit": payload.get("limit"),
                    "search": payload.get("search"),
                    "filters": filters,
                    "include_total": payload.get("include_total"),
                    "catalog_v": catalog_v,
                    "data_v": data_v,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            logger.debug("field-values cache: cannot canonicalise payload: %s", exc)
            return None
        digest = hashlib.md5(canonical.encode()).hexdigest()
        return f"{KEY_PREFIX}{digest}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key_payload: Dict[str, Any]) -> Optional[dict]:
        """Return the cached result for this request shape, or None.

        Remembers the derived key on the payload dict so set() files the
        fresh result under the SAME stamps the lookup missed on — see the
        module docstring for why re-deriving would be a stale-write race.
        None means either "miss" or "cache disabled"; both fall through to
        the live path.
        """
        key = self._derive_key(key_payload)
        key_payload[_KEY_ATTR] = key
        if key is None:
            _track("bypass")
            return None
        try:
            client = self._client_provider()
            if client is None:
                _track("bypass")
                return None
            raw = client.get(key)
        except Exception as exc:
            self._note_redis_error("read", exc)
            _track("miss")
            return None
        if raw is None:
            _track("miss")
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as exc:
            logger.debug("field-values cache: undecodable entry: %s", exc)
            _track("miss")
            return None
        if not isinstance(value, dict) or "values" not in value:
            logger.debug("field-values cache: malformed entry for %s", key)
            _track("miss")
            return None
        _track("hit")
        return value

    def set(self, key_payload: Dict[str, Any], result: Dict[str, Any]) -> None:
        """Store the live-computed result. Best-effort: never raises.

        Reuses the key get() derived for this payload; a payload that never
        went through get() (or whose lookup was disabled) derives fresh /
        skips the store.
        """
        key = key_payload.pop(_KEY_ATTR, _UNSET)
        if key is _UNSET:
            key = self._derive_key(key_payload)
        if key is None:
            return
        try:
            client = self._client_provider()
        except Exception as exc:
            self._note_redis_error("client build", exc)
            return
        if client is None:
            return
        try:
            blob = json.dumps(result)
        except (TypeError, ValueError) as exc:
            logger.debug("field-values cache: result not JSON-serialisable: %s", exc)
            return
        try:
            client.setex(key, self._ttl, blob)
        except Exception as exc:
            self._note_redis_error("write", exc)
