from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from predmarkbot.events import Fill, Side, TradeOrder
from predmarkbot.state import StateStore


@pytest.mark.asyncio
async def test_state_creates_schema_on_first_open(tmp_path: Path) -> None:
    db_path = tmp_path / "s.db"
    async with StateStore(db_path) as store:
        version = await store.schema_version()
    assert version >= 1


@pytest.mark.asyncio
async def test_insert_and_fetch_order(tmp_path: Path) -> None:
    async with StateStore(tmp_path / "s.db") as store:
        order = TradeOrder(
            client_order_id="c1", ticker="X-Y",
            side=Side.BUY_YES, price_cents=50, size=3,
        )
        await store.insert_pending_order(order)
        rows = await store.list_orders(status="pending")
    assert len(rows) == 1
    assert rows[0]["client_order_id"] == "c1"
    assert rows[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_mark_order_submitted_then_record_fill(tmp_path: Path) -> None:
    async with StateStore(tmp_path / "s.db") as store:
        await store.insert_pending_order(TradeOrder(
            client_order_id="c1", ticker="X-Y",
            side=Side.BUY_YES, price_cents=50, size=3,
        ))
        await store.mark_order_submitted("c1", kalshi_order_id="K-1")
        fill = Fill(
            fill_id="f1", client_order_id="c1", ticker="X-Y",
            side=Side.BUY_YES, price_cents=50, size=3,
            fee_cents=1, filled_at=datetime(2026, 5, 30, tzinfo=UTC),
        )
        await store.record_fill(fill)
        position = await store.get_position("X-Y", Side.BUY_YES)
    assert position == 3


@pytest.mark.asyncio
async def test_today_realized_pnl_starts_zero(tmp_path: Path) -> None:
    async with StateStore(tmp_path / "s.db") as store:
        pnl = await store.today_realized_pnl_cents(today=datetime(2026, 5, 30, tzinfo=UTC).date())
    assert pnl == 0
