from __future__ import annotations

from pathlib import Path

import pytest
import respx

from predmarkbot.events import Side, TradeOrder
from predmarkbot.executor import Executor
from predmarkbot.kalshi.auth import KalshiSigner, load_private_key
from predmarkbot.kalshi.rest import KalshiRestClient
from predmarkbot.notify import LogNotifier
from predmarkbot.state import StateStore

FIXTURE_KEY = Path(__file__).parent.parent / "fixtures" / "test_key.pem"


def _signer() -> KalshiSigner:
    return KalshiSigner(key_id="k", private_key=load_private_key(FIXTURE_KEY))


@pytest.mark.asyncio
@respx.mock
async def test_submit_writes_pending_then_marks_submitted(tmp_path: Path) -> None:
    respx.post("https://x/portfolio/orders").respond(
        json={"order": {"order_id": "K-1"}},
    )
    async with StateStore(tmp_path / "s.db") as store, \
               KalshiRestClient(base_url="https://x", signer=_signer()) as rest:
        ex = Executor(rest=rest, state=store, notifier=LogNotifier())
        order = TradeOrder(
            client_order_id="c1", ticker="T", side=Side.BUY_YES,
            price_cents=50, size=3,
        )
        await ex.submit(order)
        rows = await store.list_orders()
    assert len(rows) == 1
    assert rows[0]["status"] == "submitted"
    assert rows[0]["kalshi_order_id"] == "K-1"


@pytest.mark.asyncio
@respx.mock
async def test_submit_handles_4xx_by_marking_rejected(tmp_path: Path) -> None:
    respx.post("https://x/portfolio/orders").respond(
        400, json={"error": {"message": "market closed"}}
    )
    async with StateStore(tmp_path / "s.db") as store, \
               KalshiRestClient(base_url="https://x", signer=_signer()) as rest:
        ex = Executor(rest=rest, state=store, notifier=LogNotifier())
        await ex.submit(TradeOrder(
            client_order_id="c2", ticker="T", side=Side.BUY_NO,
            price_cents=49, size=2,
        ))
        rows = await store.list_orders(status="rejected")
    assert len(rows) == 1
    assert "market closed" in rows[0]["error"]


@pytest.mark.asyncio
@respx.mock
async def test_submit_skips_duplicate_client_order_id(tmp_path: Path) -> None:
    route = respx.post("https://x/portfolio/orders").respond(
        json={"order": {"order_id": "K-1"}},
    )
    async with StateStore(tmp_path / "s.db") as store, \
               KalshiRestClient(base_url="https://x", signer=_signer()) as rest:
        ex = Executor(rest=rest, state=store, notifier=LogNotifier())
        order = TradeOrder(
            client_order_id="dup", ticker="T", side=Side.BUY_YES,
            price_cents=50, size=3,
        )
        await ex.submit(order)
        await ex.submit(order)  # second time: should be no-op
    assert route.call_count == 1
