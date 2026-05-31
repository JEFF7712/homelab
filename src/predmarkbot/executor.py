"""Executor: sole component that places orders against Kalshi."""
from __future__ import annotations

import logging

from predmarkbot.events import TradeOrder
from predmarkbot.kalshi.rest import KalshiApiError, KalshiRestClient
from predmarkbot.notify import Notifier
from predmarkbot.state import StateStore

_log = logging.getLogger(__name__)


class Executor:
    def __init__(
        self, *, rest: KalshiRestClient, state: StateStore, notifier: Notifier
    ) -> None:
        self._rest = rest
        self._state = state
        self._notifier = notifier

    async def submit(self, order: TradeOrder) -> None:
        # Idempotency check: if client_order_id already exists, skip.
        existing = await self._state.list_orders()
        if any(r["client_order_id"] == order.client_order_id for r in existing):
            _log.info("skipping duplicate client_order_id %s", order.client_order_id)
            return

        # State-first write: persist intent BEFORE network call.
        await self._state.insert_pending_order(order)

        payload: dict[str, object] = {
            "ticker": order.ticker,
            "client_order_id": order.client_order_id,
            "side": _kalshi_side(order.side),
            "action": "buy",
            "type": "limit",
            "yes_price": order.price_cents if "yes" in order.side.value else None,
            "no_price": order.price_cents if "no" in order.side.value else None,
            "count": order.size,
        }
        # Strip None fields (Kalshi rejects nulls in some endpoints)
        cleaned: dict[str, object] = {k: v for k, v in payload.items() if v is not None}

        try:
            resp = await self._rest.post(
                "/portfolio/orders", json=cleaned, signed=True
            )
        except KalshiApiError as exc:
            if 400 <= exc.status < 500:
                body = exc.body if isinstance(exc.body, dict) else {"raw": str(exc.body)}
                msg = body.get("error", {}).get("message", str(exc.body))  # type: ignore[union-attr]
                await self._state.mark_order_rejected(
                    order.client_order_id, error=str(msg),
                )
                await self._notifier.notify_error(exc, context={
                    "client_order_id": order.client_order_id, "stage": "submit",
                })
                return
            raise
        kalshi_id = (resp.get("order") or {}).get("order_id", "")
        await self._state.mark_order_submitted(
            order.client_order_id, kalshi_order_id=kalshi_id,
        )


def _kalshi_side(side: object) -> str:
    s = getattr(side, "value", str(side))
    return "yes" if "yes" in s else "no"
