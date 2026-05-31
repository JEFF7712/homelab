from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from predmarkbot.events import OrderbookUpdate
from predmarkbot.feed import DataFeed, InMemoryOrderbook
from predmarkbot.kalshi.ws import DeltaMessage, ParsedMessage, SnapshotMessage


def test_apply_snapshot_sets_levels() -> None:
    ob = InMemoryOrderbook()
    ob.apply_snapshot(yes=[(50, 10), (49, 5)], no=[(48, 4)])
    assert ob.top_bid_yes() == (50, 10)
    assert ob.top_bid_no() == (48, 4)


def test_apply_delta_adds_size_at_existing_level() -> None:
    ob = InMemoryOrderbook()
    ob.apply_snapshot(yes=[(50, 10)], no=[])
    ob.apply_delta(side="yes", price=50, delta=5)
    assert ob.top_bid_yes() == (50, 15)


def test_apply_delta_removes_level_when_size_zero() -> None:
    ob = InMemoryOrderbook()
    ob.apply_snapshot(yes=[(50, 10), (49, 3)], no=[])
    ob.apply_delta(side="yes", price=50, delta=-10)
    assert ob.top_bid_yes() == (49, 3)


def test_apply_delta_creates_new_level() -> None:
    ob = InMemoryOrderbook()
    ob.apply_snapshot(yes=[(50, 10)], no=[])
    ob.apply_delta(side="yes", price=51, delta=2)
    assert ob.top_bid_yes() == (51, 2)


def test_negative_size_clamped_to_zero_and_removed() -> None:
    ob = InMemoryOrderbook()
    ob.apply_snapshot(yes=[(50, 3)], no=[])
    ob.apply_delta(side="yes", price=50, delta=-10)
    assert ob.top_bid_yes() is None


# ---------------------------------------------------------------------------
# Task 4.5 — DataFeed tests
# ---------------------------------------------------------------------------

TICKER = "T"


class FakeWs:
    def __init__(self, messages: list[ParsedMessage]) -> None:
        self._messages = messages

    async def messages(self) -> AsyncIterator[ParsedMessage]:
        for m in self._messages:
            yield m


@pytest.mark.asyncio
async def test_datafeed_emits_update_after_snapshot() -> None:
    out: asyncio.Queue[OrderbookUpdate] = asyncio.Queue()
    feed = DataFeed(out=out)
    snap = SnapshotMessage(ticker=TICKER, seq=1, yes=[(50, 10), (49, 5)], no=[(48, 4)])
    ws = FakeWs([snap])
    await feed.consume(ws.messages())
    assert not out.empty()
    update = out.get_nowait()
    assert update.yes.top_bid() == (50, 10)


@pytest.mark.asyncio
async def test_datafeed_applies_delta_after_snapshot() -> None:
    out: asyncio.Queue[OrderbookUpdate] = asyncio.Queue()
    feed = DataFeed(out=out)
    snap = SnapshotMessage(ticker=TICKER, seq=1, yes=[(50, 10)], no=[])
    delta = DeltaMessage(ticker=TICKER, seq=2, side="yes", price=50, delta=-3)
    ws = FakeWs([snap, delta])
    await feed.consume(ws.messages())
    # Two updates emitted; take the last one
    update = None
    while not out.empty():
        update = out.get_nowait()
    assert update is not None
    assert update.yes.top_bid() == (50, 7)


@pytest.mark.asyncio
async def test_datafeed_force_resyncs_on_seq_gap() -> None:
    out: asyncio.Queue[OrderbookUpdate] = asyncio.Queue()
    feed = DataFeed(out=out)
    snap = SnapshotMessage(ticker=TICKER, seq=1, yes=[(50, 10)], no=[])
    delta_ok = DeltaMessage(ticker=TICKER, seq=2, side="yes", price=50, delta=-1)
    delta_gap = DeltaMessage(ticker=TICKER, seq=5, side="yes", price=50, delta=-1)  # seq gap!
    ws = FakeWs([snap, delta_ok, delta_gap])
    await feed.consume(ws.messages())
    assert TICKER in feed.resync_requested
