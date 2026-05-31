from __future__ import annotations

from datetime import UTC, datetime

import pytest

from predmarkbot.events import Side, TradeIntent
from predmarkbot.risk import RiskDecision, RiskManager


def _intent(*, size: int = 5, edge: int = 2, side: Side = Side.BUY_YES) -> TradeIntent:
    return TradeIntent(
        ticker="T", side=side, price_cents=50, size=size,
        expected_edge_cents=edge, reasoning="t",
    )


@pytest.mark.asyncio
async def test_passes_when_all_limits_ok() -> None:
    rm = RiskManager(
        min_edge_cents=1, max_per_market_dollars=50,
        max_total_exposure_dollars=200,
        max_orders_per_minute=30, max_daily_loss_dollars=25,
        get_position_dollars=lambda _t: 0,
        get_total_exposure_dollars=lambda: 0,
        get_today_realized_pnl_dollars=lambda _d: 0,
        now=lambda: datetime(2026, 5, 30, tzinfo=UTC),
    )
    d = await rm.evaluate(_intent())
    assert d == RiskDecision.PASS


@pytest.mark.asyncio
async def test_blocks_when_edge_too_low() -> None:
    rm = RiskManager(
        min_edge_cents=5, max_per_market_dollars=50,
        max_total_exposure_dollars=200,
        max_orders_per_minute=30, max_daily_loss_dollars=25,
        get_position_dollars=lambda _t: 0,
        get_total_exposure_dollars=lambda: 0,
        get_today_realized_pnl_dollars=lambda _d: 0,
        now=lambda: datetime(2026, 5, 30, tzinfo=UTC),
    )
    d = await rm.evaluate(_intent(edge=1))
    assert d == RiskDecision.BLOCK_LOW_EDGE


@pytest.mark.asyncio
async def test_blocks_when_per_market_exceeded() -> None:
    rm = RiskManager(
        min_edge_cents=1, max_per_market_dollars=2,  # tiny cap
        max_total_exposure_dollars=200,
        max_orders_per_minute=30, max_daily_loss_dollars=25,
        get_position_dollars=lambda _t: 0,
        get_total_exposure_dollars=lambda: 0,
        get_today_realized_pnl_dollars=lambda _d: 0,
        now=lambda: datetime(2026, 5, 30, tzinfo=UTC),
    )
    # 5 contracts @ 50¢ = $2.50, exceeds $2 cap
    d = await rm.evaluate(_intent(size=5))
    assert d == RiskDecision.BLOCK_PER_MARKET


@pytest.mark.asyncio
async def test_blocks_when_total_exposure_exceeded() -> None:
    rm = RiskManager(
        min_edge_cents=1, max_per_market_dollars=100,
        max_total_exposure_dollars=2,  # tiny cap
        max_orders_per_minute=30, max_daily_loss_dollars=25,
        get_position_dollars=lambda _t: 0,
        get_total_exposure_dollars=lambda: 0,
        get_today_realized_pnl_dollars=lambda _d: 0,
        now=lambda: datetime(2026, 5, 30, tzinfo=UTC),
    )
    d = await rm.evaluate(_intent(size=5))
    assert d == RiskDecision.BLOCK_TOTAL_EXPOSURE


@pytest.mark.asyncio
async def test_blocks_when_rate_limit_exceeded() -> None:
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)
    rm = RiskManager(
        min_edge_cents=1, max_per_market_dollars=50,
        max_total_exposure_dollars=200,
        max_orders_per_minute=2, max_daily_loss_dollars=25,
        get_position_dollars=lambda _t: 0,
        get_total_exposure_dollars=lambda: 0,
        get_today_realized_pnl_dollars=lambda _d: 0,
        now=lambda: now,
    )
    assert await rm.evaluate(_intent()) == RiskDecision.PASS
    assert await rm.evaluate(_intent()) == RiskDecision.PASS
    assert await rm.evaluate(_intent()) == RiskDecision.BLOCK_RATE_LIMIT


@pytest.mark.asyncio
async def test_kill_switch_trips_at_daily_loss() -> None:
    rm = RiskManager(
        min_edge_cents=1, max_per_market_dollars=50,
        max_total_exposure_dollars=200,
        max_orders_per_minute=30, max_daily_loss_dollars=25,
        get_position_dollars=lambda _t: 0,
        get_total_exposure_dollars=lambda: 0,
        get_today_realized_pnl_dollars=lambda _d: -2600,  # -$26 in cents
        now=lambda: datetime(2026, 5, 30, tzinfo=UTC),
    )
    d = await rm.evaluate(_intent())
    assert d == RiskDecision.KILL_SWITCH
