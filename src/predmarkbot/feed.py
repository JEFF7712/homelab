"""In-memory orderbook + DataFeed driver.

This module is split:
    InMemoryOrderbook  — pure data structure; unit-tested in test_feed.py
    DataFeed           — async loop that maintains books, reconciles, emits events
                         (appended in Task 4.5)
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from predmarkbot.events import OrderbookUpdate
    from predmarkbot.kalshi.ws import ParsedMessage


@dataclass
class InMemoryOrderbook:
    """Tracks YES and NO bid stacks for one market.

    Bids are stored as {price_cents: size}. Top bid = highest key.
    (v1 doesn't track asks separately; we model 'NO bids' as the inverse side.)
    """

    _yes: dict[int, int] = field(default_factory=dict)
    _no: dict[int, int] = field(default_factory=dict)

    def apply_snapshot(
        self, *, yes: list[tuple[int, int]], no: list[tuple[int, int]]
    ) -> None:
        self._yes = {p: q for p, q in yes if q > 0}
        self._no = {p: q for p, q in no if q > 0}

    def apply_delta(self, *, side: str, price: int, delta: int) -> None:
        side_map = self._yes if side == "yes" else self._no
        new_size = side_map.get(price, 0) + delta
        if new_size <= 0:
            side_map.pop(price, None)
        else:
            side_map[price] = new_size

    def top_bid_yes(self) -> tuple[int, int] | None:
        if not self._yes:
            return None
        p = max(self._yes)
        return (p, self._yes[p])

    def top_bid_no(self) -> tuple[int, int] | None:
        if not self._no:
            return None
        p = max(self._no)
        return (p, self._no[p])


_log = logging.getLogger(__name__)


class DataFeed:
    """Async driver: consumes parsed WS messages, maintains per-ticker books,
    detects sequence gaps, and emits OrderbookUpdate events.
    """

    def __init__(self, *, out: asyncio.Queue[OrderbookUpdate]) -> None:
        self._out = out
        self._books: dict[str, InMemoryOrderbook] = {}
        self._last_seq: dict[str, int] = {}
        self.resync_requested: set[str] = set()

    async def consume(self, messages: AsyncIterator[ParsedMessage]) -> None:
        async for m in messages:
            await self._handle(m)

    async def _handle(self, m: ParsedMessage) -> None:
        from predmarkbot.kalshi.ws import DeltaMessage, SnapshotMessage

        if isinstance(m, SnapshotMessage):
            book = self._books.get(m.ticker)
            if book is None:
                book = InMemoryOrderbook()
                self._books[m.ticker] = book
            book.apply_snapshot(yes=m.yes, no=m.no)
            self._last_seq[m.ticker] = m.seq
            self.resync_requested.discard(m.ticker)
            await self._emit(m.ticker, book)

        elif isinstance(m, DeltaMessage):
            ticker = m.ticker
            if ticker not in self._books:
                _log.warning("DeltaMessage for unknown ticker %s; requesting resync", ticker)
                self.resync_requested.add(ticker)
                return
            expected = self._last_seq.get(ticker, 0) + 1
            if m.seq != expected:
                _log.warning(
                    "Seq gap for %s: expected %d got %d; requesting resync",
                    ticker,
                    expected,
                    m.seq,
                )
                self.resync_requested.add(ticker)
                return
            book = self._books[ticker]
            book.apply_delta(side=m.side, price=m.price, delta=m.delta)
            self._last_seq[ticker] = m.seq
            await self._emit(ticker, book)

        # UnknownMessage — silently ignored

    async def _emit(self, ticker: str, book: InMemoryOrderbook) -> None:
        from predmarkbot.events import OrderbookSide, OrderbookUpdate

        top_yes = book.top_bid_yes()
        top_no = book.top_bid_no()
        yes_side = OrderbookSide(bids=[top_yes] if top_yes else [], asks=[])
        no_side = OrderbookSide(bids=[top_no] if top_no else [], asks=[])
        update = OrderbookUpdate(
            ticker=ticker,
            yes=yes_side,
            no=no_side,
            ts=datetime.now(UTC),
            seq=self._last_seq[ticker],
        )
        await self._out.put(update)
