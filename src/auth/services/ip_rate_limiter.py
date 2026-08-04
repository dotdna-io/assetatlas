# SPDX-License-Identifier: AGPL-3.0-or-later
"""
IP-based rate limiting for sensitive auth endpoints.

Uses the application cache (Redis in prod, in-memory in dev/test) keyed by
(action, ip). Counters expire after the rolling window TTL.

Intent is enumeration / spam mitigation — not financial throttling. We
deliberately fail open on cache errors and on missing source IPs so that
authentication paths cannot be locked out by infrastructure problems.
"""
import logging
from typing import Optional

from src.api.utils.caching import cache

logger = logging.getLogger(__name__)


class IPRateLimiter:
    """Per-IP counter with TTL, backed by the application cache."""

    DEFAULT_WINDOW_SECONDS = 3600  # 1 hour

    @staticmethod
    def _key(action: str, ip: str) -> str:
        return f"ratelimit:{action}:{ip}"

    @staticmethod
    def check_and_increment(
        action: str,
        ip: Optional[str],
        limit: int,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
    ) -> bool:
        """
        Return True if the call is allowed; False if the limit is already reached.

        On the True path the cache counter is incremented best-effort — a failed
        write is logged but does not block the call (fail-open). On a missing IP
        or any cache error, returns True without recording anything.
        """
        if not ip:
            return True

        key = IPRateLimiter._key(action, ip)

        # Get-then-set is racy across workers; an off-by-one over the limit is
        # acceptable for enumeration mitigation. Do not "fix" with atomic INCR
        # without revisiting TTL semantics — INCR does not refresh the window.
        try:
            current = cache.get(key) or 0
        except Exception as exc:
            logger.warning("IP rate-limit cache read failed; failing open: %s", exc)
            return True

        if current >= limit:
            return False

        try:
            cache.set(key, current + 1, timeout=window_seconds)
        except Exception as exc:
            logger.warning("IP rate-limit cache write failed; not blocking: %s", exc)

        return True

    @staticmethod
    def _counter_key(action: str, key: str) -> str:
        return f"ratelimit_counter:{action}:{key}"

    @staticmethod
    def _lock_key(action: str, key: str) -> str:
        return f"ratelimit_lock:{action}:{key}"

    @staticmethod
    def is_locked(action: str, key: str) -> bool:
        """Return True if (action, key) is currently locked out (fail-open on cache error)."""
        if not key:
            return False
        try:
            return bool(cache.get(IPRateLimiter._lock_key(action, key)))
        except Exception as exc:
            logger.warning("Lockout cache read failed; failing open: %s", exc)
            return False

    @staticmethod
    def record_failure(
        action: str,
        key: str,
        *,
        window_seconds: int,
        limit: int,
        lockout_seconds: int,
    ) -> None:
        """Bump the failure counter for (action, key) and trigger lockout when the
        counter reaches ``limit`` within ``window_seconds``. Fail-open on cache error.
        Keyed independently from the rolling-window counter used by
        ``check_and_increment`` so the two callers can not collide.
        """
        if not key:
            return
        counter_k = IPRateLimiter._counter_key(action, key)
        try:
            current = cache.get(counter_k) or 0
        except Exception as exc:
            logger.warning("Lockout counter read failed; not recording: %s", exc)
            return
        try:
            cache.set(counter_k, current + 1, timeout=window_seconds)
        except Exception as exc:
            logger.warning("Lockout counter write failed: %s", exc)
            return
        if current + 1 >= limit:
            try:
                cache.set(IPRateLimiter._lock_key(action, key), True, timeout=lockout_seconds)
                logger.warning(
                    "Lockout triggered: action=%s key=%s window=%ds lockout=%ds",
                    action, key, window_seconds, lockout_seconds,
                )
            except Exception as exc:
                logger.warning("Lockout key write failed: %s", exc)
