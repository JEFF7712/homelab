"""LongshotStrategy — buy YES on out-of-the-money weather threshold markets.

Codifies the 2026-06-01 favorite-longshot research finding (+1413 bps
realized-vs-expected gap on 1978 KXHIGH* markets in the 0-5¢ bucket).
Emits one intent per market per run on the cheap-first filter chain.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from predmarkbot.events import MarketMeta, OrderbookUpdate, Side, TradeIntent
from predmarkbot.strategy.base import Strategy


class LongshotStrategy(Strategy):
    """Buy YES when:
    - market's series is in `series_allowlist`
    - YES ask (derived as 100 - NO top bid) <= `max_price_cents`
    - close_ts is between `min_seconds_to_close` and `max_seconds_to_close` from now
    - we haven't already emitted for this ticker in this run

    Entry price = YES ask (derived as 100 - NO top bid). Falls back to
    `yes_bid_price + 1` if NO has no bid. Skips if no liquidity on either side.
    """

    def __init__(
        self,
        *,
        series_allowlist: set[str],
        size_contracts: int,
        max_price_cents: int,
        min_seconds_to_close: int,
        max_seconds_to_close: int,
        historical_yes_rate: float,
        get_market_meta: Callable[[str], MarketMeta | None],
        now: Callable[[], datetime],
    ) -> None:
        self._allowlist = series_allowlist
        self._size = size_contracts
        self._max_price = max_price_cents
        self._min_secs = min_seconds_to_close
        self._max_secs = max_seconds_to_close
        self._yes_rate = historical_yes_rate
        self._get_meta = get_market_meta
        self._now = now
        self._already_emitted: set[str] = set()

    async def on_update(self, update: OrderbookUpdate) -> list[TradeIntent]:
        # 1. Need market metadata
        meta = self._get_meta(update.ticker)
        if meta is None:
            return []

        # 2. Series allowlist
        if meta.series_ticker not in self._allowlist:
            return []

        # 3. Already-emitted dedupe (in-memory, per-run)
        if update.ticker in self._already_emitted:
            return []

        # 4. Derive YES ask (the price we'd actually buy at).
        # Primary: 100 - NO top bid.  Fallback: YES top bid + 1.
        no_top = update.no.top_bid()
        yes_top = update.yes.top_bid()
        if no_top is not None:
            no_bid_price, _ = no_top
            yes_ask = 100 - no_bid_price
        elif yes_top is not None:
            yes_bid_price, _ = yes_top
            yes_ask = yes_bid_price + 1
        else:
            return []  # no liquidity on either side

        # 5. Price filter on YES ask (the actual entry price)
        if yes_ask > self._max_price:
            return []
        # Clamp to valid TradeIntent range [1, 99]
        yes_ask = max(1, min(yes_ask, 99))

        # 6. Time-window filter
        seconds_to_close = (meta.close_ts - self._now()).total_seconds()
        if seconds_to_close < self._min_secs or seconds_to_close > self._max_secs:
            return []

        # 7. Build the intent.
        edge = round(100 * self._yes_rate - yes_ask)
        intent = TradeIntent(
            ticker=update.ticker,
            side=Side.BUY_YES,
            price_cents=yes_ask,
            size=self._size,
            expected_edge_cents=edge,
            reasoning=(
                f"longshot @ {yes_ask}¢, "
                f"{int(seconds_to_close)}s to close, "
                f"hist_yes_rate={self._yes_rate:.3f}"
            ),
        )
        self._already_emitted.add(update.ticker)
        return [intent]
