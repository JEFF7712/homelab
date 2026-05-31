from __future__ import annotations

from pathlib import Path

import pytest
import respx
from httpx import Response

from predmarkbot.kalshi.auth import KalshiSigner, load_private_key
from predmarkbot.kalshi.rest import KalshiApiError, KalshiRestClient

FIXTURE_KEY = Path(__file__).parent.parent / "fixtures" / "test_key.pem"


def _signer() -> KalshiSigner:
    return KalshiSigner(key_id="k", private_key=load_private_key(FIXTURE_KEY))


@pytest.mark.asyncio
@respx.mock
async def test_public_get_does_not_sign() -> None:
    route = respx.get("https://demo-api.kalshi.co/trade-api/v2/series/KXHIGHNY").respond(
        json={"series": {"ticker": "KXHIGHNY"}},
    )
    async with KalshiRestClient(
        base_url="https://demo-api.kalshi.co/trade-api/v2",
        signer=None,
    ) as client:
        data = await client.get("/series/KXHIGHNY")
    assert route.called
    req = route.calls[0].request
    assert "KALSHI-ACCESS-KEY" not in req.headers
    assert data["series"]["ticker"] == "KXHIGHNY"


@pytest.mark.asyncio
@respx.mock
async def test_signed_get_attaches_three_headers() -> None:
    route = respx.get(
        "https://demo-api.kalshi.co/trade-api/v2/portfolio/balance"
    ).respond(json={"balance": 100})
    async with KalshiRestClient(
        base_url="https://demo-api.kalshi.co/trade-api/v2",
        signer=_signer(),
    ) as client:
        await client.get("/portfolio/balance", signed=True)
    req = route.calls[0].request
    assert "KALSHI-ACCESS-KEY" in req.headers
    assert "KALSHI-ACCESS-TIMESTAMP" in req.headers
    assert "KALSHI-ACCESS-SIGNATURE" in req.headers


@pytest.mark.asyncio
@respx.mock
async def test_5xx_retried_with_backoff() -> None:
    route = respx.get("https://demo-api.kalshi.co/trade-api/v2/series/X").mock(
        side_effect=[Response(503), Response(503), Response(200, json={"ok": True})],
    )
    async with KalshiRestClient(
        base_url="https://demo-api.kalshi.co/trade-api/v2",
        signer=None,
        retry_max=3,
        retry_base_delay=0.0,
    ) as client:
        data = await client.get("/series/X")
    assert route.call_count == 3
    assert data == {"ok": True}


@pytest.mark.asyncio
@respx.mock
async def test_4xx_not_retried_and_raises() -> None:
    route = respx.post(
        "https://demo-api.kalshi.co/trade-api/v2/portfolio/orders"
    ).respond(400, json={"error": {"message": "bad price"}})
    async with KalshiRestClient(
        base_url="https://demo-api.kalshi.co/trade-api/v2",
        signer=_signer(),
    ) as client:
        with pytest.raises(KalshiApiError) as exc:
            await client.post("/portfolio/orders", json={"price": 0}, signed=True)
    assert route.call_count == 1
    assert exc.value.status == 400
