from __future__ import annotations

import os
from pathlib import Path

import pytest

from predmarkbot.kalshi.auth import KalshiSigner, load_private_key
from predmarkbot.kalshi.rest import KalshiRestClient

pytestmark = pytest.mark.integration

DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"


def _signer() -> KalshiSigner:
    key_id = os.environ.get("KALSHI_KEY_ID")
    key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
    if not key_id or not key_path:
        pytest.skip("KALSHI_KEY_ID / KALSHI_PRIVATE_KEY_PATH not set")
    return KalshiSigner(key_id=key_id, private_key=load_private_key(Path(key_path)))


@pytest.mark.asyncio
async def test_signed_balance_endpoint() -> None:
    async with KalshiRestClient(base_url=DEMO_BASE, signer=_signer()) as c:
        data = await c.get("/portfolio/balance", signed=True)
    # Either real balance or "no funds yet" — both are valid contracts here.
    assert isinstance(data, dict)


@pytest.mark.asyncio
async def test_signed_positions_endpoint() -> None:
    async with KalshiRestClient(base_url=DEMO_BASE, signer=_signer()) as c:
        data = await c.get("/portfolio/positions", signed=True)
    assert isinstance(data, dict)
