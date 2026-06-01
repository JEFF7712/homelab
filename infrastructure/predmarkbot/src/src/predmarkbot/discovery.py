"""MarketDiscovery: translate configured series into the watched-tickers set."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from datetime import UTC, datetime

from predmarkbot.kalshi.rest import KalshiRestClient
from predmarkbot.state import StateStore

_log = logging.getLogger(__name__)


class MarketDiscovery:
    def __init__(
        self, *, rest: KalshiRestClient, series: Iterable[str],
        poll_interval_seconds: int = 300,
        state: StateStore | None = None,
    ) -> None:
        self._rest = rest
        self._series = list(series)
        self._interval = poll_interval_seconds
        self._state = state

    async def discover_once(self) -> set[str]:
        tickers: set[str] = set()
        for s in self._series:
            data = await self._rest.get(
                f"/markets?series_ticker={s}&status=open&limit=1000"
            )
            for m in data.get("markets", []):
                t = m.get("ticker")
                if not isinstance(t, str):
                    continue
                tickers.add(t)
                if self._state is not None:
                    series_ticker: str = m.get("series_ticker") or t.split("-")[0]
                    await self._state.upsert_market(
                        ticker=t,
                        series_ticker=series_ticker,
                        title=m.get("title", ""),
                        status=m.get("status", "open"),
                        last_seen_ts=datetime.now(UTC).isoformat(),
                    )
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
