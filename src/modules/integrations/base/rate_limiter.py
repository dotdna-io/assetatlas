# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Rate Limiter

Implements token bucket algorithm for rate limiting API requests.

Created: 2025-11-04
"""

import time
import threading
from typing import Optional
import logging


class RateLimiter:
    """
    Token bucket rate limiter for API requests.

    Implements the token bucket algorithm to limit requests per time period.
    Thread-safe for concurrent use.

    Example:
        # Allow 100 requests per minute
        limiter = RateLimiter(requests_per_minute=100)

        # Wait for token before making request
        limiter.wait_for_token()
        response = api_client.get('/data')
    """

    def __init__(
        self,
        requests_per_minute: int = 60,
        burst_size: Optional[int] = None,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize rate limiter.

        Args:
            requests_per_minute: Maximum requests allowed per minute
            burst_size: Maximum burst size (default: requests_per_minute)
            logger: Optional logger instance
        """
        self.requests_per_minute = requests_per_minute
        self.burst_size = burst_size or requests_per_minute
        self.logger = logger or logging.getLogger(__name__)

        # Token bucket state
        self.tokens = float(self.burst_size)
        self.last_update = time.time()

        # Thread safety
        self.lock = threading.Lock()

        # Calculate refill rate (tokens per second)
        self.refill_rate = requests_per_minute / 60.0

        self.logger.debug(
            f"Rate limiter initialized: {requests_per_minute} req/min, "
            f"burst: {self.burst_size}, refill: {self.refill_rate:.2f} tokens/sec"
        )

    def _refill(self):
        """Refill tokens based on elapsed time. Must be called under self.lock."""
        now = time.time()
        elapsed = now - self.last_update
        self.tokens = min(self.burst_size, self.tokens + elapsed * self.refill_rate)
        self.last_update = now

    def wait_for_token(self, tokens_needed: float = 1.0) -> float:
        """
        Wait until a token is available.

        Blocks until sufficient tokens are available in the bucket.

        Args:
            tokens_needed: Number of tokens needed (default: 1.0)

        Returns:
            Time waited in seconds
        """
        wait_time = 0.0

        with self.lock:
            self._refill()

            # If not enough tokens, calculate wait time
            if self.tokens < tokens_needed:
                deficit = tokens_needed - self.tokens
                wait_time = deficit / self.refill_rate

                if wait_time > 0:
                    self.logger.debug(f"Rate limit reached, waiting {wait_time:.2f}s")

        # Wait outside the lock
        if wait_time > 0:
            time.sleep(wait_time)

            # Update state after waiting
            with self.lock:
                self.tokens = 0.0
                self.last_update = time.time()
        else:
            # Consume tokens
            with self.lock:
                self.tokens -= tokens_needed

        return wait_time

    def try_consume(self, tokens_needed: float = 1.0) -> bool:
        """
        Try to consume tokens without waiting.

        Non-blocking version of wait_for_token().

        Args:
            tokens_needed: Number of tokens needed (default: 1.0)

        Returns:
            True if tokens were available and consumed, False otherwise
        """
        with self.lock:
            self._refill()

            # Check if enough tokens
            if self.tokens >= tokens_needed:
                self.tokens -= tokens_needed
                return True
            else:
                return False

    def get_available_tokens(self) -> float:
        """
        Get current number of available tokens.

        Updates internal state (refills tokens based on elapsed time).

        Returns:
            Number of tokens currently available
        """
        with self.lock:
            self._refill()
            return self.tokens

    def reset(self):
        """Reset rate limiter to full capacity"""
        with self.lock:
            self.tokens = float(self.burst_size)
            self.last_update = time.time()
            self.logger.debug("Rate limiter reset")

    def __repr__(self) -> str:
        """String representation"""
        return (
            f"<RateLimiter(requests_per_minute={self.requests_per_minute}, "
            f"burst={self.burst_size}, available={self.get_available_tokens():.1f})>"
        )


class AdaptiveRateLimiter(RateLimiter):
    """
    Adaptive rate limiter that adjusts based on API responses.

    Automatically reduces rate when receiving 429 (Too Many Requests) errors
    and gradually increases rate when requests succeed.

    Example:
        limiter = AdaptiveRateLimiter(initial_rpm=100)

        try:
            limiter.wait_for_token()
            response = api_client.get('/data')
            limiter.on_success()
        except requests.HTTPError as e:
            if e.response.status_code == 429:
                limiter.on_rate_limit()
                raise
    """

    def __init__(
        self,
        initial_rpm: int = 60,
        min_rpm: int = 10,
        max_rpm: int = 200,
        decrease_factor: float = 0.5,
        increase_factor: float = 1.1,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize adaptive rate limiter.

        Args:
            initial_rpm: Initial requests per minute
            min_rpm: Minimum requests per minute
            max_rpm: Maximum requests per minute
            decrease_factor: Factor to decrease rate on 429 (default: 0.5)
            increase_factor: Factor to increase rate on success (default: 1.1)
            logger: Optional logger instance
        """
        super().__init__(requests_per_minute=initial_rpm, logger=logger)

        self.min_rpm = min_rpm
        self.max_rpm = max_rpm
        self.decrease_factor = decrease_factor
        self.increase_factor = increase_factor

        self.success_count = 0
        self.success_threshold = 100  # Increase rate after N successes

    def on_success(self, response=None):
        """
        Call this when a request succeeds.

        Gradually increases rate limit after sustained success.

        Args:
            response: Optional HTTP response (used by HeaderRateLimiter subclass)
        """
        self.success_count += 1

        if self.success_count >= self.success_threshold:
            new_rpm = min(self.max_rpm, self.requests_per_minute * self.increase_factor)

            if new_rpm > self.requests_per_minute:
                self.logger.info(
                    f"Increasing rate limit: {self.requests_per_minute:.0f} → {new_rpm:.0f} req/min"
                )
                self.requests_per_minute = new_rpm
                self.refill_rate = new_rpm / 60.0

            self.success_count = 0

    def on_rate_limit(self, response=None):
        """
        Call this when receiving a 429 Too Many Requests error.

        Immediately reduces rate limit.

        Args:
            response: Optional HTTP response (used by HeaderRateLimiter subclass)
        """
        new_rpm = max(self.min_rpm, self.requests_per_minute * self.decrease_factor)

        self.logger.warning(
            f"Rate limit hit! Decreasing rate: {self.requests_per_minute:.0f} → {new_rpm:.0f} req/min"
        )

        self.requests_per_minute = new_rpm
        self.refill_rate = new_rpm / 60.0
        self.success_count = 0

        # Reset tokens to prevent immediate retry
        self.reset()


class HeaderRateLimiter(AdaptiveRateLimiter):
    """Rate limiter that reads rate-limit info from HTTP response headers.

    Proactively sleeps when X-RateLimit-Remaining drops below threshold.
    """

    def __init__(self, remaining_header="X-RateLimit-Remaining",
                 retry_after_header="Retry-After", threshold=10, **kwargs):
        super().__init__(**kwargs)
        self.remaining_header = remaining_header
        self.retry_after_header = retry_after_header
        self.threshold = threshold

    def on_success(self, response=None):
        super().on_success()
        if response is not None:
            remaining = response.headers.get(self.remaining_header)
            if remaining is not None:
                try:
                    if int(remaining) <= self.threshold:
                        retry_after = response.headers.get(self.retry_after_header, "1")
                        wait = min(float(retry_after), 60)
                        self.logger.info(f"Rate limit approaching ({remaining} remaining), waiting {wait}s")
                        time.sleep(wait)
                except (ValueError, TypeError):
                    pass

    def on_rate_limit(self, response=None):
        super().on_rate_limit()
        if response is not None:
            retry_after = response.headers.get(self.retry_after_header, "5")
            try:
                wait = min(float(retry_after), 120)
                self.logger.warning(f"Rate limited (429), waiting {wait}s")
                time.sleep(wait)
            except (ValueError, TypeError):
                time.sleep(5)
