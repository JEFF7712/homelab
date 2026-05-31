"""Tests for ArbStrategy."""
from __future__ import annotations

from datetime import datetime

import pytest

from predmarkbot.events import OrderbookSide, OrderbookUpdate, Side
from predmarkbot.strategy.arb import ArbStrategy

_TS = datetime(2026, 1, 1)
_TICKER = "TEST-MARKET"


def _book(
    yes_bid: tuple[int, int] | None,
    no_bid: tuple[int, int] | None,
) -> OrderbookUpdate:
    yes_bids = [yes_bid] if yes_bid is not None else []
    no_bids = [no_bid] if no_bid is not None else []
    return OrderbookUpdate(
        ticker=_TICKER,
        yes=OrderbookSide(bids=yes_bids, asks=[]),
        no=OrderbookSide(bids=no_bids, asks=[]),
        ts=_TS,
        seq=1,
    )


def _existing_positions(_ticker: str, _side: Side) -> int:
    return 0


@pytest.mark.asyncio
async def test_no_arb_when_sum_below_threshold() -> None:
    strat = ArbStrategy(
        get_position=_existing_positions,
        min_edge_cents=1,
        max_intent_size=10,
    )
    update = _book((40, 10), (40, 10))
    intents = await strat.on_update(update)
    assert intents == []


@pytest.mark.asyncio
async def test_emits_two_intents_when_clear_arb() -> None:
    strat = ArbStrategy(
        get_position=_existing_positions,
        min_edge_cents=1,
        max_intent_size=10,
    )
    # yes_bid=55 qty=8, no_bid=55 qty=6 → size limited by min(8, 6, 10) = 6
    update = _book((55, 8), (55, 6))
    intents = await strat.on_update(update)
    assert len(intents) == 2
    sides = {i.side for i in intents}
    assert sides == {Side.BUY_YES, Side.BUY_NO}
    for intent in intents:
        assert intent.size == 6


@pytest.mark.asyncio
async def test_size_capped_by_max_intent_size() -> None:
    strat = ArbStrategy(
        get_position=_existing_positions,
        min_edge_cents=1,
        max_intent_size=3,
    )
    # qty=100 each but max_intent_size=3
    update = _book((55, 100), (55, 100))
    intents = await strat.on_update(update)
    assert len(intents) == 2
    for intent in intents:
        assert intent.size == 3


@pytest.mark.asyncio
async def test_does_not_stack_into_existing_position() -> None:
    def get_pos(ticker: str, side: Side) -> int:
        if side == Side.BUY_YES:
            return 5
        return 0

    strat = ArbStrategy(
        get_position=get_pos,
        min_edge_cents=1,
        max_intent_size=10,
    )
    update = _book((55, 10), (55, 10))
    intents = await strat.on_update(update)
    assert intents == []


@pytest.mark.asyncio
async def test_missing_side_emits_nothing() -> None:
    strat = ArbStrategy(
        get_position=_existing_positions,
        min_edge_cents=1,
        max_intent_size=10,
    )
    update = _book((55, 10), None)
    intents = await strat.on_update(update)
    assert intents == []
