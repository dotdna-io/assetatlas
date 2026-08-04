# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for IPRateLimiter (cache-backed per-IP counter)."""
import pytest
from unittest.mock import patch

from src.api.utils.caching import cache
from src.auth.services.ip_rate_limiter import IPRateLimiter


@pytest.fixture(autouse=True)
def _isolate_cache(blueprint_app):
    """Clear cache between tests so counters do not leak."""
    with blueprint_app.app_context():
        cache.clear()
    yield
    with blueprint_app.app_context():
        cache.clear()


def test_increments_count_on_each_call(blueprint_app):
    with blueprint_app.app_context():
        for _ in range(5):
            assert IPRateLimiter.check_and_increment("password_reset", "1.2.3.4", limit=5) is True
        # 6th call exceeds limit
        assert IPRateLimiter.check_and_increment("password_reset", "1.2.3.4", limit=5) is False


def test_separate_ips_are_independent(blueprint_app):
    with blueprint_app.app_context():
        for _ in range(5):
            IPRateLimiter.check_and_increment("password_reset", "1.2.3.4", limit=5)
        # Second IP starts fresh
        assert IPRateLimiter.check_and_increment("password_reset", "5.6.7.8", limit=5) is True


def test_separate_actions_are_independent(blueprint_app):
    with blueprint_app.app_context():
        for _ in range(5):
            IPRateLimiter.check_and_increment("password_reset", "1.2.3.4", limit=5)
        # Different action starts fresh for the same IP
        assert IPRateLimiter.check_and_increment("login_attempt", "1.2.3.4", limit=5) is True


def test_missing_ip_does_not_block(blueprint_app):
    """If we cannot determine the source IP we fail open — rate limit cannot apply."""
    with blueprint_app.app_context():
        for _ in range(100):
            assert IPRateLimiter.check_and_increment("password_reset", None, limit=5) is True


def test_cache_failure_fails_open(blueprint_app):
    """Cache infra failures must not block authentication paths."""
    with blueprint_app.app_context():
        with patch.object(cache, "get", side_effect=RuntimeError("cache down")):
            # Should not raise, should not block.
            assert IPRateLimiter.check_and_increment("password_reset", "1.2.3.4", limit=5) is True


# Unit tests for the per-key lockout helper added in #613.

@patch('src.auth.services.ip_rate_limiter.cache')
class TestIsLocked:
    def test_returns_false_when_no_lock_key(self, mock_cache):
        mock_cache.get.return_value = None
        assert IPRateLimiter.is_locked('totp_fail', 'alice') is False

    def test_returns_true_when_lock_key_present(self, mock_cache):
        mock_cache.get.return_value = True
        assert IPRateLimiter.is_locked('totp_fail', 'alice') is True

    def test_record_failure_sets_lock_after_threshold(self, mock_cache):
        # Simulate counter already at threshold-1
        mock_cache.get.return_value = 4
        IPRateLimiter.record_failure(
            action='totp_fail', key='alice',
            window_seconds=900, limit=5, lockout_seconds=1800,
        )
        # The helper should write the lockout key
        lock_keys = [c.args[0] for c in mock_cache.set.call_args_list]
        assert any(k.startswith('ratelimit_lock:totp_fail:alice') for k in lock_keys)

    def test_record_failure_does_not_lock_below_threshold(self, mock_cache):
        mock_cache.get.return_value = 1
        IPRateLimiter.record_failure(
            action='totp_fail', key='alice',
            window_seconds=900, limit=5, lockout_seconds=1800,
        )
        lock_keys = [c.args[0] for c in mock_cache.set.call_args_list]
        assert not any(k.startswith('ratelimit_lock:') for k in lock_keys)
