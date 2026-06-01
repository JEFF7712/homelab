from __future__ import annotations

import asyncio
import time

import pytest

from predmarkbot.research.ratelimit import TokenBucket


@pytest.mark.asyncio
async def test_first_n_acquires_are_instant() -> None:
    bucket = TokenBucket(rate_per_sec=5.0, burst=5)
    start = time.monotonic()
    for _ in range(5):
        await bucket.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.05


@pytest.mark.asyncio
async def test_over_burst_waits_for_refill() -> None:
    bucket = TokenBucket(rate_per_sec=10.0, burst=2)
    for _ in range(2):
        await bucket.acquire()
    start = time.monotonic()
    await bucket.acquire()  # must wait ~0.1s for one token to refill
    elapsed = time.monotonic() - start
    assert 0.08 <= elapsed <= 0.2


@pytest.mark.asyncio
async def test_concurrent_callers_serialize_correctly() -> None:
    bucket = TokenBucket(rate_per_sec=10.0, burst=1)
    async def call(i: int) -> float:
        await bucket.acquire()
        return time.monotonic()
    start = time.monotonic()
    times = await asyncio.gather(*[call(i) for i in range(4)])
    rel = [t - start for t in sorted(times)]
    # Token bucket replenishes at 1/0.1s; allow tolerance
    assert rel[0] < 0.02
    assert rel[1] >= 0.08
    assert rel[2] >= 0.18
    assert rel[3] >= 0.28
