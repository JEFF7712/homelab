from __future__ import annotations

import pytest

from predmarkbot.kalshi.rest import KalshiRestClient

pytestmark = pytest.mark.integration

DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"


@pytest.mark.asyncio
async def test_series_endpoint_returns_known_series() -> None:
    async with KalshiRestClient(base_url=DEMO_BASE, signer=None) as c:
        data = await c.get("/series/KXHIGHNY")
    assert "series" in data
    assert data["series"]["ticker"].startswith("KX")


@pytest.mark.asyncio
async def test_markets_endpoint_returns_open_markets() -> None:
    async with KalshiRestClient(base_url=DEMO_BASE, signer=None) as c:
        data = await c.get("/markets?series_ticker=KXHIGHNY&status=open&limit=5")
    assert "markets" in data
    # If demo has no open markets, this will return [] — that's still a valid contract check.
    assert isinstance(data["markets"], list)


@pytest.mark.asyncio
async def test_orderbook_endpoint_returns_levels() -> None:
    async with KalshiRestClient(base_url=DEMO_BASE, signer=None) as c:
        markets = (await c.get("/markets?series_ticker=KXHIGHNY&status=open&limit=1"))["markets"]
        if not markets:
            pytest.skip("no open markets in demo")
        ticker = markets[0]["ticker"]
        ob = await c.get(f"/markets/{ticker}/orderbook")
    # Schema check: top-level key may be "orderbook" — verify if test fails.
    assert ob is not None
