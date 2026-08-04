# SPDX-License-Identifier: AGPL-3.0-or-later
"""Daily message cap and per-user concurrency lock for chat turns.

Uses Redis for cross-process counters. Falls back to a permissive null
limiter when Redis is unavailable so dev/test environments stay functional.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Tuple


logger = logging.getLogger(__name__)

# 25h covers DST shifts + small clock skew between worker and Redis.
_DAILY_TTL_SECONDS = 25 * 3600
# 120s caps a stuck turn; ChatService's per-turn timeout (60s default) fires first in normal paths.
_LOCK_TTL_SECONDS = 120


class ChatRateLimiter:
    """Daily message cap (per user) + 1-concurrent-turn lock (per user)."""

    def __init__(self, redis, *, per_day: int):
        self._redis = redis
        self.per_day = per_day

    def _day_key(self, user_id: str) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f"chat:rl:{user_id}:{today}"

    def _lock_key(self, user_id: str) -> str:
        return f"chat:turnlock:{user_id}"

    def check_and_incr(self, user_id: str) -> Tuple[bool, int, int]:
        """Increment today's counter and return (allowed, count, remaining)."""
        key = self._day_key(user_id)
        count = int(self._redis.incr(key))
        if count == 1:
            self._redis.expire(key, _DAILY_TTL_SECONDS)
        remaining = max(0, self.per_day - count)
        return (count <= self.per_day, count, remaining)

    def current(self, user_id: str) -> Tuple[int, int]:
        """Return (count_today, remaining)."""
        v = self._redis.get(self._day_key(user_id))
        count = int(v) if v else 0
        return count, max(0, self.per_day - count)

    def acquire_concurrency_lock(self, user_id: str) -> bool:
        """Acquire a 1-concurrent-turn lock for this user."""
        # redis-py SET NX returns True on success, None when the key exists.
        return self._redis.set(self._lock_key(user_id), "1",
                               nx=True, ex=_LOCK_TTL_SECONDS) is True

    def release_concurrency_lock(self, user_id: str) -> None:
        try:
            self._redis.delete(self._lock_key(user_id))
        except Exception:
            logger.warning("Failed to release chat turn lock for %s", user_id)


class NullRateLimiter:
    """Permissive limiter used when Redis is unavailable.

    Allows all requests, reports zero counts. Used only in dev/test where
    rate limiting is not required.
    """

    def __init__(self, *, per_day: int):
        self.per_day = per_day

    def check_and_incr(self, user_id: str) -> Tuple[bool, int, int]:
        return (True, 0, self.per_day)

    def current(self, user_id: str) -> Tuple[int, int]:
        return (0, self.per_day)

    def acquire_concurrency_lock(self, user_id: str) -> bool:
        return True

    def release_concurrency_lock(self, user_id: str) -> None:
        pass
