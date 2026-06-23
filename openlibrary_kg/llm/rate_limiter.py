"""Token bucket rate limiter for LLM API calls."""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Token bucket rate limiter.

    Allows burst traffic up to capacity, then limits to a steady rate.
    Thread-safe and async-safe.
    """

    def __init__(self, rate: float, capacity: int | None = None):
        """
        Args:
            rate: Tokens per second (requests per second).
            capacity: Max burst size (defaults to rate, minimum 1).
        """
        self.rate = rate
        self.capacity = max(int(capacity or rate), 1)
        self._tokens = float(self.capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_refill = now

    async def acquire(self) -> None:
        """Wait until a token is available, then consume one."""
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait = (1 - self._tokens) / self.rate
                self._last_refill = time.monotonic()
            await asyncio.sleep(wait)
