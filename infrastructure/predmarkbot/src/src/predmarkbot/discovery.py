"""MarketDiscovery: translate configured series into the watched-tickers set."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from predmarkbot.kalshi.rest import KalshiRestClient

_log = logging.getLogger(__name__)


class MarketDiscovery:
    def __init__(
        self, *, rest: KalshiRestClient, series: Iterable[str],
        poll_interval_seconds: int = 300,
    ) -> None:
        self._rest = rest
        self._series = list(series)
        self._interval = poll_interval_seconds

    async def discover_once(self) -> set[str]:
        tickers: set[str] = set()
        for s in self._series:
            data = await self._rest.get(
                f"/markets?series_ticker={s}&status=open&limit=1000"
            )
            for m in data.get("markets", []):
                t = m.get("ticker")
                if isinstance(t, str):
                    tickers.add(t)
        _log.info("discovered %d tickers across %d series", len(tickers), len(self._series))
        return tickers

    async def run(self, on_change: asyncio.Queue[set[str]]) -> None:
        prev: set[str] = set()
        while True:
            current = await self.discover_once()
            if current != prev:
                await on_change.put(current)
                prev = current
            await asyncio.sleep(self._interval)
