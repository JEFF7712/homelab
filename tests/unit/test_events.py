from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from predmarkbot.events import (
    Fill,
    KillSwitch,
    OrderbookSide,
    OrderbookUpdate,
    Side,
    TradeIntent,
    TradeOrder,
)


def test_orderbook_update_has_top_helpers() -> None:
    ob = OrderbookUpdate(
        ticker="X-Y",
        yes=OrderbookSide(bids=[(50, 10), (49, 5)], asks=[(52, 7)]),
        no=OrderbookSide(bids=[(48, 4)], asks=[(51, 6)]),
        ts=datetime(2026, 5, 30, tzinfo=UTC),
        seq=42,
    )
    assert ob.yes.top_bid() == (50, 10)
    assert ob.yes.top_ask() == (52, 7)
    assert ob.no.top_bid() == (48, 4)


def test_orderbook_side_empty_top_returns_none() -> None:
    side = OrderbookSide(bids=[], asks=[])
    assert side.top_bid() is None
    assert side.top_ask() is None


def test_trade_intent_validates_size_positive() -> None:
    with pytest.raises(ValueError):
        TradeIntent(
            ticker="X-Y",
            side=Side.BUY_YES,
            price_cents=50,
            size=0,
            expected_edge_cents=1,
            reasoning="zero size",
        )


def test_trade_order_carries_client_order_id() -> None:
    o = TradeOrder(
        client_order_id="abc",
        ticker="X-Y",
        side=Side.BUY_NO,
        price_cents=49,
        size=3,
    )
    assert o.client_order_id == "abc"


def test_fill_equality_by_fill_id() -> None:
    f1 = Fill(
        fill_id="f1", client_order_id="c1", ticker="X-Y",
        side=Side.BUY_YES, price_cents=50, size=2,
        fee_cents=1, filled_at=datetime(2026, 5, 30, tzinfo=UTC),
    )
    f2 = Fill(
        fill_id="f1", client_order_id="c1", ticker="X-Y",
        side=Side.BUY_YES, price_cents=50, size=2,
        fee_cents=1, filled_at=datetime(2026, 5, 30, tzinfo=UTC),
    )
    assert f1 == f2


def test_killswitch_carries_reason() -> None:
    k = KillSwitch(reason="daily loss")
    assert "daily loss" in k.reason


def test_market_meta_carries_close_ts_and_strike() -> None:
    from predmarkbot.events import MarketMeta
    m = MarketMeta(
        ticker="KXHIGHNY-26JUN10-T75",
        series_ticker="KXHIGHNY",
        close_ts=datetime(2026, 6, 11, 4, 59, tzinfo=UTC),
        yes_strike=75.0,
    )
    assert m.ticker == "KXHIGHNY-26JUN10-T75"
    assert m.series_ticker == "KXHIGHNY"
    assert m.yes_strike == 75.0
    # frozen
    with pytest.raises(FrozenInstanceError):
        m.ticker = "other"  # type: ignore[misc]
