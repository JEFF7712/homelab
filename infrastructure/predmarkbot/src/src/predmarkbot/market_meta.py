"""In-memory cache of market metadata (ticker, series, close_ts, strike).

Populated by the runner at startup + on each MarketDiscovery poll tick.
The strategy reads from it synchronously per OrderbookUpdate.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from predmarkbot.events import MarketMeta
from predmarkbot.kalshi.rest import KalshiApiError, KalshiRestClient

_log = logging.getLogger(__name__)


def _parse_ts(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class MarketMetaCache:
    """Synchronous read interface, async refresh.

    `refresh()` is idempotent — already-cached tickers are skipped.
    Per-ticker fetch failures are logged at WARN; the strategy will see
    `get()` return None and short-circuit.
    """

    def __init__(self, *, rest: KalshiRestClient) -> None:
        self._rest = rest
        self._cache: dict[str, MarketMeta] = {}

    def get(self, ticker: str) -> MarketMeta | None:
        return self._cache.get(ticker)

    async def refresh(self, tickers: Iterable[str]) -> None:
        for ticker in tickers:
            if ticker in self._cache:
                continue
            try:
                data = await self._rest.get(f"/markets/{ticker}")
            except KalshiApiError as exc:
                _log.warning("market meta fetch failed for %s: %s", ticker, exc)
                continue
            m = data.get("market", data)
            raw_series = m.get("series_ticker")
            series_ticker = (
                str(raw_series) if raw_series else ticker.split("-", 1)[0]
            )
            close_raw = m.get("close_time") or m.get("expected_expiration_time")
            if not close_raw:
                _log.warning("market %s has no close_time; skipping", ticker)
                continue
            self._cache[ticker] = MarketMeta(
                ticker=ticker,
                series_ticker=series_ticker,
                close_ts=_parse_ts(str(close_raw)),
                yes_strike=_safe_float(
                    m.get("yes_strike") or m.get("floor_strike")
                ),
            )
