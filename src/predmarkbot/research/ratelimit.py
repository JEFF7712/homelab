"""Asyncio token-bucket rate limiter."""
from __future__ import annotations

import asyncio
import time


class TokenBucket:
    """Classic token bucket: `rate_per_sec` tokens added, capped at `burst`.

    Each `acquire()` consumes one token, awaiting if none available.
    Thread-safe within a single asyncio loop (uses an asyncio.Lock).
    """

    def __init__(self, *, rate_per_sec: float, burst: int) -> None:
        self._rate = rate_per_sec
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(
                    self._capacity,
                    self._tokens + elapsed * self._rate,
                )
                self._last_refill = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait)
