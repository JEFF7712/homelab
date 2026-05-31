from __future__ import annotations

from datetime import UTC, datetime

import pytest
import respx

from predmarkbot.events import Fill, Side
from predmarkbot.notify import LogNotifier, NtfyNotifier


@pytest.mark.asyncio
async def test_log_notifier_records_events() -> None:
    n = LogNotifier()
    await n.notify_startup(version="0.1", mode="shadow", n_markets=5)
    await n.notify_fill(
        Fill(
            fill_id="f1",
            client_order_id="c1",
            ticker="X-Y",
            side=Side.BUY_YES,
            price_cents=50,
            size=2,
            fee_cents=1,
            filled_at=datetime(2026, 5, 30, tzinfo=UTC),
        )
    )
    await n.notify_error(RuntimeError("boom"), context={"k": "v"})
    await n.notify_kill_switch(reason="daily loss", snapshot={"pnl": -2500})
    await n.notify_daily_pnl(
        date=datetime(2026, 5, 30, tzinfo=UTC).date(),
        realized=-100,
        unrealized=0,
        order_count=3,
        fill_count=2,
    )
    await n.notify_shutdown(reason="sigterm")
    assert len(n.events) == 6
    assert n.events[0][0] == "startup"


@pytest.mark.asyncio
@respx.mock
async def test_ntfy_notifier_posts_startup_message() -> None:
    route = respx.post("https://ntfy.example/predmarkbot").respond(200)
    n = NtfyNotifier(
        url="https://ntfy.example",
        topic="predmarkbot",
        token="secret",
    )
    await n.notify_startup(version="0.1", mode="shadow", n_markets=2)
    assert route.called
    req = route.calls[0].request
    assert req.headers["Authorization"] == "Bearer secret"
    body = req.content.decode()
    assert "shadow" in body
    assert "0.1" in body


@pytest.mark.asyncio
@respx.mock
async def test_ntfy_notifier_marks_kill_switch_high_priority() -> None:
    route = respx.post("https://ntfy.example/predmarkbot").respond(200)
    n = NtfyNotifier(url="https://ntfy.example", topic="predmarkbot", token="t")
    await n.notify_kill_switch(reason="daily loss", snapshot={"pnl_cents": -2500})
    req = route.calls[0].request
    assert req.headers.get("Priority") == "5"


@pytest.mark.asyncio
@respx.mock
async def test_ntfy_notifier_swallows_network_errors() -> None:
    respx.post("https://ntfy.example/predmarkbot").mock(
        side_effect=Exception("nope"),
    )
    n = NtfyNotifier(url="https://ntfy.example", topic="predmarkbot", token="t")
    # Must not raise — notification failures should never crash the bot.
    await n.notify_shutdown(reason="sigterm")
