from __future__ import annotations

import pytest
import respx

from predmarkbot.discovery import MarketDiscovery
from predmarkbot.kalshi.rest import KalshiRestClient


@pytest.mark.asyncio
@respx.mock
async def test_discovery_returns_open_tickers_for_series() -> None:
    respx.get(
        "https://x/markets?series_ticker=KXHIGHNY&status=open&limit=1000"
    ).respond(json={"markets": [
        {"ticker": "T-1", "status": "open"},
        {"ticker": "T-2", "status": "open"},
    ]})
    async with KalshiRestClient(base_url="https://x", signer=None) as rest:
        disc = MarketDiscovery(rest=rest, series=["KXHIGHNY"])
        tickers = await disc.discover_once()
    assert tickers == {"T-1", "T-2"}


@pytest.mark.asyncio
@respx.mock
async def test_discovery_unions_multiple_series() -> None:
    respx.get(
        "https://x/markets?series_ticker=A&status=open&limit=1000"
    ).respond(json={"markets": [{"ticker": "A-1"}]})
    respx.get(
        "https://x/markets?series_ticker=B&status=open&limit=1000"
    ).respond(json={"markets": [{"ticker": "B-1"}, {"ticker": "B-2"}]})
    async with KalshiRestClient(base_url="https://x", signer=None) as rest:
        disc = MarketDiscovery(rest=rest, series=["A", "B"])
        tickers = await disc.discover_once()
    assert tickers == {"A-1", "B-1", "B-2"}
