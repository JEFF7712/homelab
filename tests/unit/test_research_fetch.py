from __future__ import annotations

from pathlib import Path

import pytest
import respx
from httpx import Response

from predmarkbot.kalshi.rest import KalshiRestClient
from predmarkbot.research.fetch import fetch_resolved_markets, pull_all
from predmarkbot.research.ratelimit import TokenBucket
from predmarkbot.research.store import ResearchStore


@pytest.mark.asyncio
@respx.mock
async def test_fetch_paginates_and_upserts(tmp_path: Path) -> None:
    base = "https://demo-api.kalshi.co/trade-api/v2"
    respx.get(f"{base}/markets").mock(
        side_effect=[
            Response(200, json={
                "markets": [
                    {
                        "ticker": "X-1", "event_ticker": "E1",
                        "series_ticker": "S", "category": "weather",
                        "title": "t1",
                        "open_time": "2026-01-01T00:00:00Z",
                        "close_time": "2026-01-02T00:00:00Z",
                        "settle_time": "2026-01-02T01:00:00Z",
                        "result": "yes",
                    },
                ],
                "cursor": "PAGE2",
            }),
            Response(200, json={
                "markets": [
                    {
                        "ticker": "X-2", "event_ticker": "E2",
                        "series_ticker": "S", "category": "weather",
                        "title": "t2",
                        "open_time": "2026-01-02T00:00:00Z",
                        "close_time": "2026-01-03T00:00:00Z",
                        "settle_time": "2026-01-03T01:00:00Z",
                        "result": "no",
                    },
                ],
                "cursor": "",
            }),
        ]
    )
    async with (
        KalshiRestClient(base_url=base, signer=None) as rest,
        ResearchStore(tmp_path / "r.db") as store,
    ):
        bucket = TokenBucket(rate_per_sec=100.0, burst=10)
        n = await fetch_resolved_markets(
            rest=rest, store=store, bucket=bucket,
            from_close="2026-01-01T00:00:00Z",
            to_close="2026-01-03T00:00:00Z",
        )
        tickers = await store.list_market_tickers()
    assert n == 2
    assert tickers == ["X-1", "X-2"]


@pytest.mark.asyncio
@respx.mock
async def test_fetch_idempotent_on_rerun(tmp_path: Path) -> None:
    base = "https://demo-api.kalshi.co/trade-api/v2"
    respx.get(f"{base}/markets").respond(json={
        "markets": [{
            "ticker": "X-1", "event_ticker": "E", "series_ticker": "S",
            "category": "weather", "title": "t",
            "open_time": "2026-01-01T00:00:00Z",
            "close_time": "2026-01-02T00:00:00Z",
            "settle_time": "2026-01-02T01:00:00Z",
            "result": "yes",
        }],
        "cursor": "",
    })
    async with (
        KalshiRestClient(base_url=base, signer=None) as rest,
        ResearchStore(tmp_path / "r.db") as store,
    ):
        bucket = TokenBucket(rate_per_sec=100.0, burst=10)
        await fetch_resolved_markets(
            rest=rest, store=store, bucket=bucket,
            from_close="2026-01-01T00:00:00Z",
            to_close="2026-01-03T00:00:00Z",
        )
        await fetch_resolved_markets(
            rest=rest, store=store, bucket=bucket,
            from_close="2026-01-01T00:00:00Z",
            to_close="2026-01-03T00:00:00Z",
        )
        tickers = await store.list_market_tickers()
    assert tickers == ["X-1"]


@pytest.mark.asyncio
@respx.mock
async def test_candles_fetch_writes_rows(tmp_path: Path) -> None:
    from predmarkbot.research.fetch import fetch_candlesticks
    base = "https://demo-api.kalshi.co/trade-api/v2"
    respx.get(f"{base}/series/S/markets/X-1/candlesticks").respond(json={
        "candlesticks": [
            {"end_period_ts": 1735689600, "yes_bid": {
                "open": 40, "high": 42, "low": 39, "close": 41
            }, "volume": 1000},
        ]
    })
    async with (
        KalshiRestClient(base_url=base, signer=None) as rest,
        ResearchStore(tmp_path / "r.db") as store,
    ):
        bucket = TokenBucket(rate_per_sec=100.0, burst=10)
        await fetch_candlesticks(
            rest=rest, store=store, bucket=bucket,
            ticker="X-1", series_ticker="S",
            start_ts="2026-01-01T00:00:00Z",
            end_ts="2026-01-02T00:00:00Z",
        )
        rows = await store.get_candlesticks("X-1")
    assert len(rows) == 1
    assert rows[0]["close_yes_cents"] == 41


@pytest.mark.asyncio
@respx.mock
async def test_candles_records_failure_on_persistent_4xx(tmp_path: Path) -> None:
    from predmarkbot.research.fetch import fetch_candlesticks
    base = "https://demo-api.kalshi.co/trade-api/v2"
    respx.get(f"{base}/series/S/markets/X-1/candlesticks").respond(404, json={"error": "no"})
    async with (
        KalshiRestClient(base_url=base, signer=None) as rest,
        ResearchStore(tmp_path / "r.db") as store,
    ):
        bucket = TokenBucket(rate_per_sec=100.0, burst=10)
        await fetch_candlesticks(
            rest=rest, store=store, bucket=bucket,
            ticker="X-1", series_ticker="S",
            start_ts="2026-01-01T00:00:00Z",
            end_ts="2026-01-02T00:00:00Z",
        )
        failures = await store.list_fetch_failures()
    assert len(failures) == 1
    assert failures[0]["ticker"] == "X-1"


@pytest.mark.asyncio
@respx.mock
async def test_pull_all_fetches_markets_then_candles(tmp_path: Path) -> None:
    base = "https://demo-api.kalshi.co/trade-api/v2"
    # Markets endpoint — single market, no pagination
    respx.get(f"{base}/markets").respond(json={
        "markets": [{
            "ticker": "X-1", "event_ticker": "E1", "series_ticker": "S",
            "category": "weather", "title": "t1",
            "open_time": "2026-01-01T00:00:00Z",
            "close_time": "2026-01-02T00:00:00Z",
            "settle_time": "2026-01-02T01:00:00Z",
            "result": "yes",
        }],
        "cursor": "",
    })
    # Candlesticks endpoint for X-1
    respx.get(f"{base}/series/S/markets/X-1/candlesticks").respond(json={
        "candlesticks": [
            {"end_period_ts": 1735689600, "yes_bid": {
                "open": 40, "high": 42, "low": 39, "close": 41,
            }, "volume": 500},
        ]
    })
    async with (
        KalshiRestClient(base_url=base, signer=None) as rest,
        ResearchStore(tmp_path / "r.db") as store,
    ):
        n_markets, n_candles = await pull_all(
            rest=rest, store=store,
            from_close="2026-01-01T00:00:00Z",
            to_close="2026-01-02T00:00:00Z",
            rate_per_sec=100.0,
        )
        tickers = await store.list_market_tickers()
        tickers_with_candles = await store.tickers_with_candles()
    assert n_markets == 1
    assert n_candles == 1
    assert "X-1" in tickers
    assert "X-1" in tickers_with_candles
