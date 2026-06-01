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
    - YES top bid <= `max_price_cents`
    - close_ts is between `min_seconds_to_close` and `max_seconds_to_close` from now
    - we haven't already emitted for this ticker in this run

    Entry price = YES top ask (derived as 100 - NO top bid), capped at
    `max_price_cents`. Falls back to `yes_bid_price + 1` if NO has no bid.
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

        # 4. Price filter
        yes_top = update.yes.top_bid()
        if yes_top is None:
            return []
        yes_bid_price, _yes_qty = yes_top
        if yes_bid_price > self._max_price:
            return []

        # 5. Time-window filter
        seconds_to_close = (meta.close_ts - self._now()).total_seconds()
        if seconds_to_close < self._min_secs:
            return []
        if seconds_to_close > self._max_secs:
            return []

        # 6. Build the intent.
        # YES ask is derived from NO bid: yes_ask = 100 - no_bid.
        no_top = update.no.top_bid()
        if no_top is not None:
            no_bid_price, _ = no_top
            enter_price = 100 - no_bid_price
        else:
            enter_price = yes_bid_price + 1
        enter_price = min(enter_price, self._max_price)
        # Clamp to valid price range [1, 99] per TradeIntent validator
        enter_price = max(1, min(enter_price, 99))

        edge = round(100 * self._yes_rate - enter_price)
        intent = TradeIntent(
            ticker=update.ticker,
            side=Side.BUY_YES,
            price_cents=enter_price,
            size=self._size,
            expected_edge_cents=edge,
            reasoning=(
                f"longshot @ {enter_price}¢, "
                f"{int(seconds_to_close)}s to close, "
                f"hist_yes_rate={self._yes_rate:.3f}"
            ),
        )
        self._already_emitted.add(update.ticker)
        return [intent]
