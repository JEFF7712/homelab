"""In-process event types passed between components."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Side(StrEnum):
    BUY_YES = "buy_yes"
    BUY_NO = "buy_no"
    SELL_YES = "sell_yes"
    SELL_NO = "sell_no"


PriceLevel = tuple[int, int]  # (price_cents, size_contracts)


@dataclass(frozen=True)
class OrderbookSide:
    """One side (YES or NO) of an orderbook. Bids descending, asks ascending."""

    bids: list[PriceLevel]
    asks: list[PriceLevel]

    def top_bid(self) -> PriceLevel | None:
        return self.bids[0] if self.bids else None

    def top_ask(self) -> PriceLevel | None:
        return self.asks[0] if self.asks else None


@dataclass(frozen=True)
class OrderbookUpdate:
    ticker: str
    yes: OrderbookSide
    no: OrderbookSide
    ts: datetime
    seq: int


@dataclass(frozen=True)
class TradeIntent:
    ticker: str
    side: Side
    price_cents: int
    size: int
    expected_edge_cents: int
    reasoning: str

    def __post_init__(self) -> None:
        if self.size <= 0:
            raise ValueError(f"size must be positive, got {self.size}")
        if not (1 <= self.price_cents <= 99):
            raise ValueError(f"price_cents must be 1..99, got {self.price_cents}")


@dataclass(frozen=True)
class TradeOrder:
    client_order_id: str
    ticker: str
    side: Side
    price_cents: int
    size: int


@dataclass(frozen=True)
class Fill:
    fill_id: str
    client_order_id: str
    ticker: str
    side: Side
    price_cents: int
    size: int
    fee_cents: int
    filled_at: datetime


@dataclass(frozen=True)
class KillSwitch:
    reason: str
    context: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketMeta:
    ticker: str
    series_ticker: str
    close_ts: datetime
    yes_strike: float | None
