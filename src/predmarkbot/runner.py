"""Wire all components together in a single asyncio process."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from predmarkbot import __version__
from predmarkbot.clock import ClockSkewError, check_clock_skew
from predmarkbot.config import Config, Mode
from predmarkbot.discovery import MarketDiscovery
from predmarkbot.events import KillSwitch, OrderbookUpdate, TradeOrder
from predmarkbot.executor import Executor
from predmarkbot.feed import DataFeed
from predmarkbot.kalshi.auth import KalshiSigner, load_private_key
from predmarkbot.kalshi.rest import KalshiRestClient
from predmarkbot.kalshi.ws import KalshiWsClient
from predmarkbot.notify import LogNotifier, Notifier, NtfyNotifier
from predmarkbot.risk import RiskDecision, RiskManager
from predmarkbot.state import StateStore
from predmarkbot.strategy.arb import ArbStrategy

_log = logging.getLogger(__name__)


class _KillSwitchSignal(Exception):
    """Adapter exception so the dataclass KillSwitch event can flow via raise.

    `events.KillSwitch` is a frozen dataclass (not a BaseException subclass) so
    it can't be raised directly. This wrapper carries the event payload up
    through the asyncio.gather() boundary in `run()`.
    """

    def __init__(self, event: KillSwitch) -> None:
        super().__init__(event.reason)
        self.event = event


async def run(config: Config) -> None:
    """Top-level entry: configure logging, build components, run forever."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    notifier = _build_notifier(config)

    try:
        await check_clock_skew(now_provider=lambda: datetime.now(UTC))
    except ClockSkewError as exc:
        await notifier.notify_error(exc, context={"stage": "startup_clock_check"})
        sys.exit(2)

    signer = KalshiSigner(
        key_id=os.environ[config.kalshi.key_id_env],
        private_key=load_private_key(Path(config.kalshi.private_key_path)),
    )

    async with (
        KalshiRestClient(base_url=config.kalshi.api_base_url, signer=signer) as rest,
        StateStore(Path(config.state.db_path)) as state,
    ):
        # Refuse to start if a previous run tripped the kill switch.
        sentinel = Path(config.state.db_path + ".killed")
        if sentinel.exists():  # noqa: ASYNC240  # one-shot startup check; sync I/O is fine
            await notifier.notify_error(
                RuntimeError("kill-switch sentinel present — refusing to start"),
                context={"sentinel": str(sentinel)},
            )
            sys.exit(3)

        # Reconcile pending/submitted orders against Kalshi at startup.
        # v1 simplification: only logs counts; full reconciliation is a follow-up.
        await _reconcile_orders_on_startup(rest=rest, state=state)

        discovery = MarketDiscovery(
            rest=rest,
            series=config.discovery.series,
            poll_interval_seconds=config.discovery.poll_interval_seconds,
        )
        watched = await discovery.discover_once()
        await notifier.notify_startup(
            version=__version__, mode=config.mode.value, n_markets=len(watched),
        )

        # v1 simplification: strategy `get_position` returns 0. Real lookups
        # happen via the async StateStore; wiring a sync cache that refreshes
        # after each fill is a follow-up listed in the plan's self-review.
        strategy = ArbStrategy(
            get_position=lambda _t, _s: 0,
            min_edge_cents=config.risk.min_edge_cents,
            max_intent_size=config.risk.max_intent_size,
        )

        # v1 simplification: risk callbacks return 0. They should be wired to
        # state-backed sync caches in a follow-up; for now this means the
        # per-market / total-exposure / daily-loss limits never trip in v1.
        risk = RiskManager(
            min_edge_cents=config.risk.min_edge_cents,
            max_per_market_dollars=config.risk.max_per_market_dollars,
            max_total_exposure_dollars=config.risk.max_total_exposure_dollars,
            max_orders_per_minute=config.risk.max_orders_per_minute,
            max_daily_loss_dollars=config.risk.max_daily_loss_dollars,
            get_position_dollars=lambda _t: 0,
            get_total_exposure_dollars=lambda: 0,
            get_today_realized_pnl_dollars=lambda _d: 0,
            now=lambda: datetime.now(UTC),
        )

        executor = Executor(rest=rest, state=state, notifier=notifier)
        updates: asyncio.Queue[OrderbookUpdate] = asyncio.Queue()
        feed = DataFeed(out=updates)

        async with KalshiWsClient(base_url=config.kalshi.ws_base_url) as ws:
            await ws.subscribe_orderbook(sorted(watched))
            tasks: list[asyncio.Task[None]] = [
                asyncio.create_task(feed.consume(ws.messages()), name="feed"),
                asyncio.create_task(
                    _drain_updates(
                        updates=updates,
                        strategy=strategy,
                        risk=risk,
                        executor=executor,
                        state=state,
                        notifier=notifier,
                        mode=config.mode,
                    ),
                    name="strategy_loop",
                ),
                asyncio.create_task(
                    _daily_pnl_loop(state=state, notifier=notifier),
                    name="daily_pnl",
                ),
            ]
            try:
                await asyncio.gather(*tasks)
            except _KillSwitchSignal as signal:
                kill = signal.event
                await notifier.notify_kill_switch(
                    reason=kill.reason, snapshot=dict(kill.context),
                )
                # Write the sentinel BEFORE exiting so subsequent runs refuse to start.
                sentinel.write_text(kill.reason)  # noqa: ASYNC240  # shutdown path; sync I/O is fine
                sys.exit(42)
            finally:
                for t in tasks:
                    t.cancel()


async def _daily_pnl_loop(*, state: StateStore, notifier: Notifier) -> None:
    """Sleep until midnight UTC, then emit a daily P&L notification. Loops forever."""
    while True:
        now = datetime.now(UTC)
        # Compute next midnight UTC
        target = datetime(now.year, now.month, now.day, tzinfo=UTC) + timedelta(days=1)
        delay = (target - now).total_seconds()
        await asyncio.sleep(delay)

        yesterday = (target - timedelta(days=1)).date()
        realized = await state.today_realized_pnl_cents(today=yesterday)
        unrealized = await state.total_open_exposure_cents()

        async with state.conn.execute(
            "SELECT count(*) AS c FROM orders WHERE date(submitted_at)=?",
            (yesterday.isoformat(),),
        ) as cur:
            row = await cur.fetchone()
        order_count = int(row["c"]) if row else 0

        async with state.conn.execute(
            "SELECT count(*) AS c FROM fills WHERE date(filled_at)=?",
            (yesterday.isoformat(),),
        ) as cur:
            row = await cur.fetchone()
        fill_count = int(row["c"]) if row else 0

        await notifier.notify_daily_pnl(
            date=yesterday,
            realized=realized,
            unrealized=unrealized,
            order_count=order_count,
            fill_count=fill_count,
        )


async def _drain_updates(
    *,
    updates: asyncio.Queue[OrderbookUpdate],
    strategy: ArbStrategy,
    risk: RiskManager,
    executor: Executor,
    state: StateStore,
    notifier: Notifier,
    mode: Mode,
) -> None:
    """Pull updates off the queue, evaluate through strategy + risk, dispatch."""
    while True:
        upd = await updates.get()
        intents = await strategy.on_update(upd)
        for intent in intents:
            decision = await risk.evaluate(intent)
            if decision is RiskDecision.KILL_SWITCH:
                # KillSwitch.context is dict[str, object]; build it explicitly typed
                # so mypy doesn't complain about dict[str, str] invariance.
                ctx: dict[str, object] = {"ticker": upd.ticker}
                raise _KillSwitchSignal(
                    KillSwitch(reason="daily_loss_exceeded", context=ctx),
                )
            if decision is not RiskDecision.PASS:
                _log.info("risk blocked %s: %s", intent.ticker, decision.name)
                continue
            if mode is Mode.SHADOW:
                await state.record_shadow_intent(
                    ts=upd.ts,
                    ticker=intent.ticker,
                    side=intent.side,
                    price_cents=intent.price_cents,
                    size=intent.size,
                    expected_edge_cents=intent.expected_edge_cents,
                    reasoning=intent.reasoning,
                )
            else:
                order = TradeOrder(
                    client_order_id=str(uuid.uuid4()),
                    ticker=intent.ticker,
                    side=intent.side,
                    price_cents=intent.price_cents,
                    size=intent.size,
                )
                await executor.submit(order)


async def _reconcile_orders_on_startup(
    *, rest: KalshiRestClient, state: StateStore,
) -> None:
    """v1 stub: log pending/submitted order counts.

    Full reconciliation against /portfolio/orders is deferred; subsequent fill
    polling is expected to catch up on any stale local state. Documented as a
    v1 simplification in the plan.
    """
    del rest  # unused in v1 stub
    pending = await state.list_orders(status="pending")
    submitted = await state.list_orders(status="submitted")
    if not pending and not submitted:
        return
    _log.info(
        "reconciling %d pending + %d submitted orders against Kalshi at startup",
        len(pending),
        len(submitted),
    )


def _build_notifier(config: Config) -> Notifier:
    """Pick a notifier based on whether the ntfy token env var is set."""
    token = os.environ.get(config.notify.ntfy_token_env)
    if not token:
        _log.warning(
            "%s not set; using LogNotifier (no remote notifications)",
            config.notify.ntfy_token_env,
        )
        return LogNotifier()
    return NtfyNotifier(
        url=config.notify.ntfy_url,
        topic=config.notify.ntfy_topic,
        token=token,
    )
