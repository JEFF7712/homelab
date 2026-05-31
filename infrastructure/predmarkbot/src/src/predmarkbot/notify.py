"""Notifier abstraction. Backends: LogNotifier (testing) and NtfyNotifier."""

from __future__ import annotations

import abc
import logging
from datetime import date as _date
from typing import Any

import httpx

from predmarkbot.events import Fill

_log = logging.getLogger(__name__)


class Notifier(abc.ABC):
    @abc.abstractmethod
    async def notify_startup(self, *, version: str, mode: str, n_markets: int) -> None: ...

    @abc.abstractmethod
    async def notify_shutdown(self, *, reason: str) -> None: ...

    @abc.abstractmethod
    async def notify_fill(self, fill: Fill) -> None: ...

    @abc.abstractmethod
    async def notify_error(
        self, exc: BaseException, *, context: dict[str, Any] | None = None
    ) -> None: ...

    @abc.abstractmethod
    async def notify_kill_switch(self, *, reason: str, snapshot: dict[str, Any]) -> None: ...

    @abc.abstractmethod
    async def notify_daily_pnl(
        self,
        *,
        date: _date,
        realized: int,
        unrealized: int,
        order_count: int,
        fill_count: int,
    ) -> None: ...


class LogNotifier(Notifier):
    """In-memory + log-only notifier. Used in tests and as a safe fallback."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def notify_startup(self, *, version: str, mode: str, n_markets: int) -> None:
        ev = {"version": version, "mode": mode, "n_markets": n_markets}
        self.events.append(("startup", ev))
        _log.info("startup %s", ev)

    async def notify_shutdown(self, *, reason: str) -> None:
        self.events.append(("shutdown", {"reason": reason}))
        _log.info("shutdown reason=%s", reason)

    async def notify_fill(self, fill: Fill) -> None:
        self.events.append(("fill", {"ticker": fill.ticker, "size": fill.size}))
        _log.info("fill %s size=%d price=%d", fill.ticker, fill.size, fill.price_cents)

    async def notify_error(
        self, exc: BaseException, *, context: dict[str, Any] | None = None
    ) -> None:
        self.events.append(("error", {"exc": str(exc), "context": context or {}}))
        _log.error("error %s context=%s", exc, context, exc_info=exc)

    async def notify_kill_switch(self, *, reason: str, snapshot: dict[str, Any]) -> None:
        self.events.append(("kill_switch", {"reason": reason, "snapshot": snapshot}))
        _log.critical("KILL SWITCH reason=%s snapshot=%s", reason, snapshot)

    async def notify_daily_pnl(
        self,
        *,
        date: _date,
        realized: int,
        unrealized: int,
        order_count: int,
        fill_count: int,
    ) -> None:
        ev = {
            "date": date.isoformat(),
            "realized": realized,
            "unrealized": unrealized,
            "order_count": order_count,
            "fill_count": fill_count,
        }
        self.events.append(("daily_pnl", ev))
        _log.info("daily_pnl %s", ev)


class NtfyNotifier(Notifier):
    """Push notifications via ntfy.sh (or self-hosted ntfy) using bearer-token auth."""

    def __init__(self, *, url: str, topic: str, token: str) -> None:
        self._endpoint = f"{url.rstrip('/')}/{topic}"
        self._token = token

    async def _post(
        self, body: str, *, title: str, priority: int = 3, tags: str = ""
    ) -> None:
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._token}",
            "Title": title,
            "Priority": str(priority),
        }
        if tags:
            headers["Tags"] = tags
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(self._endpoint, content=body, headers=headers)
        except Exception:  # noqa: BLE001  # notification failures must never crash the bot
            _log.warning("ntfy POST failed (swallowed)", exc_info=True)

    async def notify_startup(self, *, version: str, mode: str, n_markets: int) -> None:
        body = f"version={version} mode={mode} markets={n_markets}"
        await self._post(body, title="predmarkbot up", priority=3, tags="green_circle")

    async def notify_shutdown(self, *, reason: str) -> None:
        body = f"reason={reason}"
        await self._post(body, title="predmarkbot down", tags="yellow_circle")

    async def notify_fill(self, fill: Fill) -> None:
        body = (
            f"ticker={fill.ticker} side={fill.side.value}"
            f" size={fill.size} price={fill.price_cents}¢"
        )
        await self._post(body, title="fill", tags="moneybag")

    async def notify_error(
        self, exc: BaseException, *, context: dict[str, Any] | None = None
    ) -> None:
        body = f"{type(exc).__name__}: {exc} context={context or {}}"
        await self._post(body, title="predmarkbot error", priority=4, tags="warning")

    async def notify_kill_switch(self, *, reason: str, snapshot: dict[str, Any]) -> None:
        body = f"reason={reason} snapshot={snapshot}"
        await self._post(
            body, title="predmarkbot KILL SWITCH", priority=5, tags="rotating_light"
        )

    async def notify_daily_pnl(
        self,
        *,
        date: _date,
        realized: int,
        unrealized: int,
        order_count: int,
        fill_count: int,
    ) -> None:
        body = (
            f"date={date.isoformat()}"
            f" realized=${realized / 100:+.2f}"
            f" unrealized=${unrealized / 100:+.2f}"
            f" orders={order_count} fills={fill_count}"
        )
        await self._post(
            body, title="predmarkbot daily P&L", tags="chart_with_upwards_trend"
        )
