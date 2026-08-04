# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ChatRateLimiter and NullRateLimiter."""

from unittest.mock import MagicMock

from src.api.blueprints.chat.rate_limit import (ChatRateLimiter,
                                                NullRateLimiter)


def _redis():
    """A MagicMock standing in for a redis-py client."""
    r = MagicMock()
    r.incr.return_value = 1
    r.get.return_value = None
    r.set.return_value = True
    r.delete.return_value = 1
    return r


def test_check_and_incr_allows_under_limit():
    r = _redis()
    r.incr.return_value = 5
    rl = ChatRateLimiter(r, per_day=10)
    allowed, count, remaining = rl.check_and_incr("u-1")
    assert allowed is True
    assert count == 5
    assert remaining == 5


def test_check_and_incr_denies_over_limit():
    r = _redis()
    r.incr.return_value = 11
    rl = ChatRateLimiter(r, per_day=10)
    allowed, count, remaining = rl.check_and_incr("u-1")
    assert allowed is False
    assert count == 11
    assert remaining == 0


def test_check_and_incr_sets_ttl_on_first_hit():
    r = _redis()
    r.incr.return_value = 1
    rl = ChatRateLimiter(r, per_day=10)
    rl.check_and_incr("u-1")
    assert r.expire.called
    args = r.expire.call_args.args
    assert args[1] == 25 * 3600


def test_check_and_incr_skips_ttl_after_first_hit():
    r = _redis()
    r.incr.return_value = 2
    rl = ChatRateLimiter(r, per_day=10)
    rl.check_and_incr("u-1")
    assert not r.expire.called


def test_current_returns_count_and_remaining():
    r = _redis()
    r.get.return_value = b"3"
    rl = ChatRateLimiter(r, per_day=10)
    count, remaining = rl.current("u-1")
    assert count == 3
    assert remaining == 7


def test_current_with_no_key_returns_zero():
    r = _redis()
    r.get.return_value = None
    rl = ChatRateLimiter(r, per_day=10)
    count, remaining = rl.current("u-1")
    assert count == 0
    assert remaining == 10


def test_acquire_concurrency_lock_succeeds_when_unlocked():
    r = _redis()
    r.set.return_value = True
    rl = ChatRateLimiter(r, per_day=10)
    assert rl.acquire_concurrency_lock("u-1") is True
    args, kwargs = r.set.call_args
    assert kwargs == {"nx": True, "ex": 120}


def test_acquire_concurrency_lock_fails_when_locked():
    r = _redis()
    r.set.return_value = None  # SET NX returns None when key exists
    rl = ChatRateLimiter(r, per_day=10)
    assert rl.acquire_concurrency_lock("u-1") is False


def test_release_concurrency_lock_calls_delete():
    r = _redis()
    rl = ChatRateLimiter(r, per_day=10)
    rl.release_concurrency_lock("u-1")
    assert r.delete.called


def test_null_rate_limiter_allows_everything():
    rl = NullRateLimiter(per_day=10)
    assert rl.check_and_incr("u-1") == (True, 0, 10)
    assert rl.current("u-1") == (0, 10)
    assert rl.acquire_concurrency_lock("u-1") is True
    rl.release_concurrency_lock("u-1")  # no-op, no exception
