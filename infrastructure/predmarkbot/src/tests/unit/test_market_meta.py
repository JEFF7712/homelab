from __future__ import annotations

from datetime import UTC, datetime

import pytest
import respx

from predmarkbot.kalshi.rest import KalshiRestClient
from predmarkbot.market_meta import MarketMetaCache


@pytest.mark.asyncio
@respx.mock
async def test_refresh_populates_cache() -> None:
    base = "https://demo-api.kalshi.co/trade-api/v2"
    respx.get(f"{base}/markets/KXHIGHNY-26JUN10-T75").respond(json={
        "market": {
            "ticker": "KXHIGHNY-26JUN10-T75",
            "series_ticker": "KXHIGHNY",
            "close_time": "2026-06-11T04:59:00Z",
            "floor_strike": 75,
        }
    })
    async with KalshiRestClient(base_url=base, signer=None) as rest:
        cache = MarketMetaCache(rest=rest)
        await cache.refresh(["KXHIGHNY-26JUN10-T75"])
        meta = cache.get("KXHIGHNY-26JUN10-T75")
    assert meta is not None
    assert meta.ticker == "KXHIGHNY-26JUN10-T75"
    assert meta.series_ticker == "KXHIGHNY"
    assert meta.close_ts == datetime(2026, 6, 11, 4, 59, tzinfo=UTC)
    assert meta.yes_strike == 75.0


@pytest.mark.asyncio
@respx.mock
async def test_refresh_idempotent_skips_existing() -> None:
    base = "https://demo-api.kalshi.co/trade-api/v2"
    route = respx.get(f"{base}/markets/KXHIGHNY-26JUN10-T75").respond(json={
        "market": {
            "ticker": "KXHIGHNY-26JUN10-T75",
            "series_ticker": "KXHIGHNY",
            "close_time": "2026-06-11T04:59:00Z",
            "floor_strike": 75,
        }
    })
    async with KalshiRestClient(base_url=base, signer=None) as rest:
        cache = MarketMetaCache(rest=rest)
        await cache.refresh(["KXHIGHNY-26JUN10-T75"])
        await cache.refresh(["KXHIGHNY-26JUN10-T75"])  # second call should skip
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_refresh_handles_failures_quietly() -> None:
    base = "https://demo-api.kalshi.co/trade-api/v2"
    respx.get(f"{base}/markets/X-BAD").respond(404, json={"error": "no"})
    respx.get(f"{base}/markets/X-GOOD").respond(json={
        "market": {
            "ticker": "X-GOOD",
            "series_ticker": "KXHIGHNY",
            "close_time": "2026-06-11T04:59:00Z",
            "floor_strike": 80,
        }
    })
    async with KalshiRestClient(base_url=base, signer=None) as rest:
        cache = MarketMetaCache(rest=rest)
        await cache.refresh(["X-BAD", "X-GOOD"])
    assert cache.get("X-BAD") is None
    assert cache.get("X-GOOD") is not None


def test_get_returns_none_for_unknown_ticker() -> None:
    from unittest.mock import MagicMock
    cache = MarketMetaCache(rest=MagicMock())
    assert cache.get("never-seen") is None


@pytest.mark.asyncio
@respx.mock
async def test_refresh_falls_back_to_ticker_prefix_for_series() -> None:
    base = "https://demo-api.kalshi.co/trade-api/v2"
    respx.get(f"{base}/markets/KXHIGHNY-X").respond(json={
        "market": {
            "ticker": "KXHIGHNY-X",
            "series_ticker": None,
            "close_time": "2026-06-11T04:59:00Z",
            "floor_strike": 80,
        }
    })
    async with KalshiRestClient(base_url=base, signer=None) as rest:
        cache = MarketMetaCache(rest=rest)
        await cache.refresh(["KXHIGHNY-X"])
        meta = cache.get("KXHIGHNY-X")
    assert meta is not None
    assert meta.series_ticker == "KXHIGHNY"
