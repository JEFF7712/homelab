from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from predmarkbot.events import (
    MarketMeta,
    OrderbookSide,
    OrderbookUpdate,
    Side,
)
from predmarkbot.strategy.longshot import LongshotStrategy

_NOW = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)


def _book(
    *,
    ticker: str = "KXHIGHNY-26JUN10-T75",
    yes_bid: tuple[int, int] | None = (3, 100),
    no_bid: tuple[int, int] | None = (95, 100),
) -> OrderbookUpdate:
    return OrderbookUpdate(
        ticker=ticker,
        yes=OrderbookSide(bids=[yes_bid] if yes_bid else [], asks=[]),
        no=OrderbookSide(bids=[no_bid] if no_bid else [], asks=[]),
        ts=_NOW,
        seq=1,
    )


def _meta(
    *,
    ticker: str = "KXHIGHNY-26JUN10-T75",
    series: str = "KXHIGHNY",
    hours_to_close: float = 12,
) -> MarketMeta:
    return MarketMeta(
        ticker=ticker,
        series_ticker=series,
        close_ts=_NOW + timedelta(hours=hours_to_close),
        yes_strike=75.0,
    )


def _make_strategy(
    *,
    meta: MarketMeta | None,
    allowlist: set[str] | None = None,
    max_price_cents: int = 5,
) -> LongshotStrategy:
    return LongshotStrategy(
        series_allowlist=allowlist or {"KXHIGHNY"},
        size_contracts=5,
        max_price_cents=max_price_cents,
        min_seconds_to_close=3600,
        max_seconds_to_close=86400,
        historical_yes_rate=0.14,
        get_market_meta=lambda _t: meta,
        now=lambda: _NOW,
    )


@pytest.mark.asyncio
async def test_emits_intent_on_qualifying_market() -> None:
    s = _make_strategy(meta=_meta())
    intents = await s.on_update(_book())
    assert len(intents) == 1
    intent = intents[0]
    assert intent.side == Side.BUY_YES
    assert intent.size == 5
    assert intent.price_cents == 5  # YES ask = 100 - 95 = 5
    assert intent.expected_edge_cents == 9  # round(100*0.14 - 5) = 9


@pytest.mark.asyncio
async def test_skips_market_not_in_allowlist() -> None:
    s = _make_strategy(
        meta=_meta(series="KXBTC"),
        allowlist={"KXHIGHNY"},
    )
    intents = await s.on_update(_book())
    assert intents == []


@pytest.mark.asyncio
async def test_skips_meta_unknown() -> None:
    s = _make_strategy(meta=None)
    intents = await s.on_update(_book())
    assert intents == []


@pytest.mark.asyncio
async def test_skips_price_above_threshold() -> None:
    s = _make_strategy(meta=_meta())
    intents = await s.on_update(_book(yes_bid=(6, 100)))
    assert intents == []


@pytest.mark.asyncio
async def test_skips_too_close_to_expiry() -> None:
    s = _make_strategy(meta=_meta(hours_to_close=0.5))  # 30 min
    intents = await s.on_update(_book())
    assert intents == []


@pytest.mark.asyncio
async def test_skips_too_far_from_expiry() -> None:
    s = _make_strategy(meta=_meta(hours_to_close=48))  # 48h
    intents = await s.on_update(_book())
    assert intents == []


@pytest.mark.asyncio
async def test_dedupes_per_run() -> None:
    s = _make_strategy(meta=_meta())
    first = await s.on_update(_book())
    second = await s.on_update(_book())
    assert len(first) == 1
    assert second == []


@pytest.mark.asyncio
async def test_uses_yes_ask_as_entry_price() -> None:
    # YES bid 2, NO bid 96 -> YES ask = 100 - 96 = 4
    s = _make_strategy(meta=_meta())
    intents = await s.on_update(_book(yes_bid=(2, 100), no_bid=(96, 100)))
    assert len(intents) == 1
    assert intents[0].price_cents == 4


@pytest.mark.asyncio
async def test_expected_edge_math_at_one_cent_entry() -> None:
    s = _make_strategy(meta=_meta())
    # YES bid 1, NO bid 98 -> YES ask = 2
    intents = await s.on_update(_book(yes_bid=(1, 100), no_bid=(98, 100)))
    assert len(intents) == 1
    assert intents[0].price_cents == 2
    # edge = round(100 * 0.14 - 2) = round(12) = 12
    assert intents[0].expected_edge_cents == 12
