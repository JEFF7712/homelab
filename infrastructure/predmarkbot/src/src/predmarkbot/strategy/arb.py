"""YES+NO arbitrage strategy."""
from __future__ import annotations

from collections.abc import Callable

from predmarkbot import fees
from predmarkbot.events import OrderbookUpdate, Side, TradeIntent
from predmarkbot.strategy.base import Strategy


class ArbStrategy(Strategy):
    """Detect and emit intents when buying YES+NO together beats cost."""

    def __init__(
        self,
        *,
        get_position: Callable[[str, Side], int],
        min_edge_cents: int,
        max_intent_size: int,
    ) -> None:
        self._get_position = get_position
        self._min_edge_cents = min_edge_cents
        self._max_intent_size = max_intent_size

    async def on_update(self, update: OrderbookUpdate) -> list[TradeIntent]:
        yes_top = update.yes.top_bid()
        no_top = update.no.top_bid()

        if yes_top is None or no_top is None:
            return []

        yes_price, yes_qty = yes_top
        no_price, no_qty = no_top

        size = min(yes_qty, no_qty, self._max_intent_size)
        if size <= 0:
            return []

        if (
            self._get_position(update.ticker, Side.BUY_YES) > 0
            or self._get_position(update.ticker, Side.BUY_NO) > 0
        ):
            return []

        fee = fees.round_trip_fee_cents(yes_price=yes_price, no_price=no_price, size=size)
        gross_credit_cents = (yes_price + no_price) * size
        cost_cents = 100 * size + fee
        edge = (gross_credit_cents - cost_cents) // size

        if edge < self._min_edge_cents:
            return []

        reasoning = (
            f"yes_bid={yes_price} + no_bid={no_price} = {yes_price + no_price};"
            f" fee={fee} edge_per_contract={edge}"
        )

        return [
            TradeIntent(
                ticker=update.ticker,
                side=Side.BUY_YES,
                price_cents=yes_price,
                size=size,
                expected_edge_cents=edge,
                reasoning=reasoning,
            ),
            TradeIntent(
                ticker=update.ticker,
                side=Side.BUY_NO,
                price_cents=no_price,
                size=size,
                expected_edge_cents=edge,
                reasoning=reasoning,
            ),
        ]
