from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from pathlib import Path

import pytest

from predmarkbot.kalshi.auth import KalshiSigner, load_private_key
from predmarkbot.kalshi.rest import KalshiRestClient
from predmarkbot.kalshi.ws import KalshiWsClient, ParsedMessage

pytestmark = pytest.mark.integration

DEMO_REST = "https://demo-api.kalshi.co/trade-api/v2"
DEMO_WS = "wss://demo-api.kalshi.co/trade-api/ws/v2"


def _signer() -> KalshiSigner | None:
    key_id = os.environ.get("KALSHI_KEY_ID")
    key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
    if not key_id or not key_path:
        return None
    return KalshiSigner(key_id=key_id, private_key=load_private_key(Path(key_path)))


@pytest.mark.asyncio
async def test_subscribe_receives_at_least_one_message() -> None:
    async with KalshiRestClient(base_url=DEMO_REST, signer=None) as rest:
        markets = (await rest.get("/markets?series_ticker=KXHIGHNY&status=open&limit=1"))["markets"]
        if not markets:
            pytest.skip("no open markets in demo")
        ticker = markets[0]["ticker"]

    signer = _signer()
    if signer is None:
        pytest.skip("KALSHI_KEY_ID / KALSHI_PRIVATE_KEY_PATH not set — cannot auth WS")

    received: list[ParsedMessage] = []
    async with KalshiWsClient(base_url=DEMO_WS, signer=signer) as ws:
        await ws.subscribe_orderbook([ticker])
        with suppress(asyncio.TimeoutError):
            async with asyncio.timeout(30):
                async for msg in ws.messages():
                    received.append(msg)
                    if len(received) >= 1:
                        break
    assert received, "no messages received within 30s"
