"""RiskManager: evaluate TradeIntent against safety limits."""
from __future__ import annotations

import collections
from collections.abc import Callable
from datetime import date, datetime
from enum import Enum, auto

from predmarkbot.events import TradeIntent


class RiskDecision(Enum):
    PASS = auto()
    BLOCK_LOW_EDGE = auto()
    BLOCK_PER_MARKET = auto()
    BLOCK_TOTAL_EXPOSURE = auto()
    BLOCK_RATE_LIMIT = auto()
    KILL_SWITCH = auto()


class RiskManager:
    def __init__(
        self,
        *,
        min_edge_cents: int,
        max_per_market_dollars: int,
        max_total_exposure_dollars: int,
        max_orders_per_minute: int,
        max_daily_loss_dollars: int,
        get_position_dollars: Callable[[str], int],
        get_total_exposure_dollars: Callable[[], int],
        get_today_realized_pnl_dollars: Callable[[date], int],
        now: Callable[[], datetime],
    ) -> None:
        self._min_edge = min_edge_cents
        self._max_per_market_cents = max_per_market_dollars * 100
        self._max_total_cents = max_total_exposure_dollars * 100
        self._max_per_min = max_orders_per_minute
        self._max_daily_loss_cents = -abs(max_daily_loss_dollars * 100)
        self._pos = get_position_dollars
        self._total = get_total_exposure_dollars
        self._pnl = get_today_realized_pnl_dollars
        self._now = now
        self._recent_order_ts: collections.deque[datetime] = collections.deque(maxlen=1000)

    async def evaluate(self, intent: TradeIntent) -> RiskDecision:
        # 5: kill switch (checked first — overrides everything)
        today = self._now().date()
        if self._pnl(today) <= self._max_daily_loss_cents:
            return RiskDecision.KILL_SWITCH

        # 1: min edge
        if intent.expected_edge_cents < self._min_edge:
            return RiskDecision.BLOCK_LOW_EDGE

        # 2: per-market cap
        new_dollars_cents = intent.price_cents * intent.size
        per_market_existing = self._pos(intent.ticker)
        if per_market_existing + new_dollars_cents > self._max_per_market_cents:
            return RiskDecision.BLOCK_PER_MARKET

        # 3: total exposure cap
        if self._total() + new_dollars_cents > self._max_total_cents:
            return RiskDecision.BLOCK_TOTAL_EXPOSURE

        # 4: rate limit (token bucket: count orders in trailing 60s)
        now = self._now()
        while self._recent_order_ts and (now - self._recent_order_ts[0]).total_seconds() > 60:
            self._recent_order_ts.popleft()
        if len(self._recent_order_ts) >= self._max_per_min:
            return RiskDecision.BLOCK_RATE_LIMIT
        self._recent_order_ts.append(now)

        return RiskDecision.PASS
